//! Native decode + DSP pipeline.
//!
//! Replaces the FFmpeg subprocess for filtered/seeked playback: a dedicated
//! thread decodes the source with symphonia (using songbird's codec registry,
//! which includes Opus), converts to stereo, runs the resampler + filter
//! chain from `dsp`, and streams raw f32 PCM to the mixer through a bounded
//! channel. Backpressure comes from the channel bound; the thread exits as
//! soon as the mixer drops the read end (track stop/replace).

use std::{
    io::{Error as IoError, ErrorKind as IoErrorKind, Read, Result as IoResult, Seek, SeekFrom},
    sync::{
        mpsc::{Receiver, SyncSender},
        Mutex,
    },
    time::Duration,
};

use reqwest::header::{CONTENT_RANGE, RANGE};
use songbird::input::{
    codecs::{get_codec_registry, get_probe},
    Compose, File, Input, RawAdapter,
};
use symphonia::core::{
    audio::SampleBuffer,
    codecs::{DecoderOptions, CODEC_TYPE_NULL},
    errors::Error as SymphoniaError,
    formats::{FormatOptions, SeekMode, SeekTo},
    io::{MediaSource, MediaSourceStream},
    meta::MetadataOptions,
    probe::Hint,
    units::Time,
};
use tracing::{debug, warn};

use crate::dsp::{FilterChain, Resampler, OUTPUT_RATE};

/// PCM chunk size pushed through the channel (frames * 2 ch * 4 bytes).
const CHUNK_FRAMES: usize = 2048;
/// Channel bound: 64 chunks =~ 1.4 s of audio buffered ahead of the mixer.
const CHANNEL_BOUND: usize = 64;

enum SourceSpec {
    File(String),
    Http(String),
}

/// Builds a fully native Input: decode -> seek -> resample(+speed) -> DSP.
///
/// The source is opened inside the decode thread (blocking I/O), so this
/// returns immediately; a failing URL surfaces as a quick track_end.
pub fn build_dsp_input(
    source_type: &str,
    url: &str,
    chain: FilterChain,
    seek: f64,
) -> Result<Input, String> {
    let spec = match source_type {
        "file" => SourceSpec::File(url.to_string()),
        _ => SourceSpec::Http(url.to_string()),
    };

    let (tx, rx) = std::sync::mpsc::sync_channel::<Vec<u8>>(CHANNEL_BOUND);

    std::thread::Builder::new()
        .name("playify-dsp".into())
        .spawn(move || decode_worker(spec, chain, seek, tx))
        .map_err(|e| format!("cannot spawn decode thread: {e}"))?;

    let reader = ChannelReader {
        rx: Mutex::new(rx),
        current: Vec::new(),
        pos: 0,
    };
    Ok(RawAdapter::new(reader, OUTPUT_RATE, 2).into())
}

fn open_source(spec: SourceSpec) -> Result<Box<dyn MediaSource>, String> {
    match spec {
        SourceSpec::File(path) => {
            let mut file = File::new(path);
            Ok(file
                .create()
                .map_err(|e| format!("cannot open file: {e:?}"))?
                .input)
        }
        SourceSpec::Http(url) => Ok(Box::new(HttpRangeSource::open(url)?)),
    }
}

fn decode_worker(spec: SourceSpec, mut chain: FilterChain, seek: f64, tx: SyncSender<Vec<u8>>) {
    let source = match open_source(spec) {
        Ok(s) => s,
        Err(e) => {
            warn!("dsp pipeline: cannot open source: {e}");
            return;
        }
    };
    let mss = MediaSourceStream::new(source, Default::default());
    // Symphonia identifies the container from content; songbird 0.6 ignores
    // hints as well, so an empty hint is fine here.
    let probed = match get_probe().format(
        &Hint::new(),
        mss,
        &FormatOptions::default(),
        &MetadataOptions::default(),
    ) {
        Ok(p) => p,
        Err(e) => {
            warn!("dsp pipeline: probe failed: {e}");
            return;
        }
    };
    let mut format = probed.format;

    let Some(track) = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
    else {
        warn!("dsp pipeline: no decodable track");
        return;
    };
    let track_id = track.id;
    let params = track.codec_params.clone();

    let mut decoder = match get_codec_registry().make(&params, &DecoderOptions::default()) {
        Ok(d) => d,
        Err(e) => {
            warn!("dsp pipeline: cannot create decoder: {e}");
            return;
        }
    };

    // -- seek ---------------------------------------------------------------
    let mut discard_frames: u64 = 0;
    if seek > 0.0 {
        match format.seek(
            SeekMode::Accurate,
            SeekTo::Time {
                time: Time::from(seek),
                track_id: Some(track_id),
            },
        ) {
            Ok(seeked) => {
                decoder.reset();
                debug!(
                    "dsp pipeline: native seek to {seek}s (actual ts {})",
                    seeked.actual_ts
                );
            }
            Err(e) => {
                // Unseekable source/container: decode-and-discard instead.
                let rate = params.sample_rate.unwrap_or(OUTPUT_RATE) as f64;
                discard_frames = (seek * rate) as u64;
                debug!("dsp pipeline: seek unsupported ({e}), discarding {discard_frames} frames");
            }
        }
    }

    // -- decode loop ----------------------------------------------------------
    let mut resampler: Option<Resampler> = None;
    let mut stereo: Vec<f32> = Vec::with_capacity(8192);
    let mut resampled: Vec<f32> = Vec::with_capacity(8192);
    let mut pending_bytes: Vec<u8> = Vec::with_capacity(CHUNK_FRAMES * 8 * 2);
    let mut sample_buf: Option<SampleBuffer<f32>> = None;

    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(SymphoniaError::ResetRequired) => {
                // New stream segment (e.g. chained ogg): follow with a reset.
                decoder.reset();
                continue;
            }
            Err(SymphoniaError::IoError(e))
                if e.kind() == std::io::ErrorKind::Interrupted
                    || e.kind() == std::io::ErrorKind::WouldBlock =>
            {
                continue; // transient, retry
            }
            Err(SymphoniaError::IoError(e))
                if e.kind() == std::io::ErrorKind::UnexpectedEof =>
            {
                debug!("dsp pipeline: end of stream");
                break;
            }
            Err(e) => {
                warn!("dsp pipeline: next_packet failed: {e:?}");
                break;
            }
        };
        if packet.track_id() != track_id {
            continue;
        }

        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            Err(SymphoniaError::DecodeError(e)) => {
                debug!("dsp pipeline: skipping corrupt packet: {e}");
                continue;
            }
            Err(e) => {
                warn!("dsp pipeline: decoder failed: {e}");
                break;
            }
        };

        let spec = *decoded.spec();
        let channels = spec.channels.count().max(1);
        if resampler.is_none() {
            resampler = Some(Resampler::new(spec.rate, chain.speed));
        }

        // Packets vary in frame count; grow the reusable buffer when a
        // packet exceeds the current capacity (copy panics otherwise).
        let needed = decoded.capacity() as u64;
        let grow = match &sample_buf {
            Some(buf) => (buf.capacity() as u64) < needed * channels as u64,
            None => true,
        };
        if grow {
            sample_buf = Some(SampleBuffer::<f32>::new(needed, spec));
        }
        let buf = sample_buf.as_mut().expect("sample buffer initialised above");
        buf.copy_interleaved_ref(decoded);
        let samples = buf.samples();

        // -- to stereo --------------------------------------------------------
        stereo.clear();
        match channels {
            1 => {
                for &s in samples {
                    stereo.push(s);
                    stereo.push(s);
                }
            }
            2 => stereo.extend_from_slice(samples),
            n => {
                for frame in samples.chunks_exact(n) {
                    stereo.push(frame[0]);
                    stereo.push(frame[1]);
                }
            }
        }

        // -- decode-and-discard seek fallback ----------------------------------
        if discard_frames > 0 {
            let frames = (stereo.len() / 2) as u64;
            if frames <= discard_frames {
                discard_frames -= frames;
                continue;
            }
            stereo.drain(..(discard_frames as usize) * 2);
            discard_frames = 0;
        }

        // -- resample (rate + speed), then run the filter chain ----------------
        resampled.clear();
        resampler
            .as_mut()
            .expect("resampler initialised above")
            .process(&stereo, &mut resampled);
        chain.process(&mut resampled);

        for sample in &resampled {
            pending_bytes.extend_from_slice(&sample.to_le_bytes());
        }
        while pending_bytes.len() >= CHUNK_FRAMES * 8 {
            let chunk: Vec<u8> = pending_bytes.drain(..CHUNK_FRAMES * 8).collect();
            if tx.send(chunk).is_err() {
                return; // mixer dropped the reader: track stopped/replaced
            }
        }
    }

    if !pending_bytes.is_empty() {
        let _ = tx.send(pending_bytes);
    }
    debug!("dsp pipeline: decode worker finished");
}

/// Blocking `Read` over the decode thread's output channel.
struct ChannelReader {
    rx: Mutex<Receiver<Vec<u8>>>,
    current: Vec<u8>,
    pos: usize,
}

impl Read for ChannelReader {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        if self.pos >= self.current.len() {
            match self.rx.lock().expect("reader lock poisoned").recv() {
                Ok(chunk) => {
                    self.current = chunk;
                    self.pos = 0;
                }
                Err(_) => return Ok(0), // decode thread finished: EOF
            }
        }
        let n = (self.current.len() - self.pos).min(buf.len());
        buf[..n].copy_from_slice(&self.current[self.pos..self.pos + n]);
        self.pos += n;
        Ok(n)
    }
}

impl Seek for ChannelReader {
    fn seek(&mut self, _: SeekFrom) -> std::io::Result<u64> {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "live dsp stream is not seekable",
        ))
    }
}

impl MediaSource for ChannelReader {
    fn is_seekable(&self) -> bool {
        false
    }

    fn byte_len(&self) -> Option<u64> {
        None
    }
}

/// Seekable HTTP source using Range requests.
///
/// Symphonia's isomp4 reader (YouTube m4a) refuses non-seekable streams, and
/// songbird's own HttpStream always reports `is_seekable() == false`. This
/// source advertises seekability whenever the server honours Range requests
/// (a 206 response), which also gives us free mid-stream reconnection —
/// replacing FFmpeg's `-reconnect` flags.
struct HttpRangeSource {
    client: reqwest::blocking::Client,
    url: String,
    len: Option<u64>,
    seekable: bool,
    pos: u64,
    body: Option<reqwest::blocking::Response>,
    retries: u32,
}

impl HttpRangeSource {
    fn open(url: String) -> Result<Self, String> {
        let client = reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| format!("cannot build http client: {e}"))?;
        let mut source = HttpRangeSource {
            client,
            url,
            len: None,
            seekable: false,
            pos: 0,
            body: None,
            retries: 0,
        };
        source
            .reopen()
            .map_err(|e| format!("http open failed: {e}"))?;
        Ok(source)
    }

    fn reopen(&mut self) -> IoResult<()> {
        let response = self
            .client
            .get(&self.url)
            .header(RANGE, format!("bytes={}-", self.pos))
            .send()
            .map_err(|e| IoError::new(IoErrorKind::Other, e))?;

        let status = response.status();
        if status == reqwest::StatusCode::PARTIAL_CONTENT {
            self.seekable = true;
            if self.len.is_none() {
                // "Content-Range: bytes <from>-<to>/<total>"
                self.len = response
                    .headers()
                    .get(CONTENT_RANGE)
                    .and_then(|v| v.to_str().ok())
                    .and_then(|v| v.rsplit('/').next())
                    .and_then(|total| total.parse().ok());
            }
        } else if status.is_success() {
            if self.pos != 0 {
                return Err(IoError::new(
                    IoErrorKind::Unsupported,
                    "server ignored Range request mid-stream",
                ));
            }
            self.len = response.content_length();
            self.seekable = false;
        } else {
            return Err(IoError::new(
                IoErrorKind::Other,
                format!("http status {status}"),
            ));
        }
        self.body = Some(response);
        Ok(())
    }
}

impl Read for HttpRangeSource {
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        loop {
            if self.body.is_none() {
                self.reopen()?;
            }
            match self.body.as_mut().expect("body opened above").read(buf) {
                Ok(0) => {
                    // Premature EOF on a known-length stream: reconnect.
                    if let Some(len) = self.len {
                        if self.pos < len && self.retries < 3 {
                            self.retries += 1;
                            self.body = None;
                            continue;
                        }
                    }
                    return Ok(0);
                }
                Ok(n) => {
                    self.pos += n as u64;
                    self.retries = 0;
                    return Ok(n);
                }
                Err(e) if self.retries < 3 => {
                    debug!("http source: read failed ({e}), reconnecting");
                    self.retries += 1;
                    self.body = None;
                }
                Err(e) => return Err(e),
            }
        }
    }
}

impl Seek for HttpRangeSource {
    fn seek(&mut self, from: SeekFrom) -> IoResult<u64> {
        let target = match from {
            SeekFrom::Start(offset) => offset as i128,
            SeekFrom::Current(delta) => self.pos as i128 + delta as i128,
            SeekFrom::End(delta) => {
                let len = self.len.ok_or_else(|| {
                    IoError::new(IoErrorKind::Unsupported, "length unknown")
                })? as i128;
                len + delta as i128
            }
        };
        if target < 0 {
            return Err(IoError::new(IoErrorKind::InvalidInput, "negative seek"));
        }
        let target = target as u64;
        if !self.seekable && target != self.pos {
            return Err(IoError::new(
                IoErrorKind::Unsupported,
                "server does not support Range requests",
            ));
        }
        if target != self.pos {
            self.pos = target;
            self.body = None; // next read reopens at the new offset
        }
        Ok(self.pos)
    }
}

impl MediaSource for HttpRangeSource {
    fn is_seekable(&self) -> bool {
        self.seekable
    }

    fn byte_len(&self) -> Option<u64> {
        self.len
    }
}
