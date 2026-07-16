//! Native DSP implementations of Playify's audio filters.
//!
//! Replaces the FFmpeg `-af` chains for the built-in filters so that
//! filtered playback stays fully in-process. All processing operates on
//! interleaved stereo f32 frames at 48 kHz (the mixer's native format).
//!
//! FFmpeg equivalences (see AUDIO_FILTERS in core.py):
//!   slowed     asetrate=44100*0.8            -> asetrate 35280 Hz (resampler)
//!   spedup     asetrate=44100*1.2            -> asetrate 52920 Hz
//!   nightcore  asetrate=44100*1.25,atempo=1  -> asetrate 55125 Hz
//!   reverb     aecho=0.8:0.9:40|50|60:...    -> Echo (FIR multi-tap)
//!   8d         apulsator=hz=0.08             -> Pulsator (LFO pan)
//!   muffled    lowpass=f=500                 -> Biquad low-pass (Q=0.707)
//!   bassboost  bass=g=10                     -> Biquad low-shelf (f=100, Q=0.5)
//!   earrape    acrusher=...                  -> Crusher (drive + quantize)
//!
//! Exactness note on asetrate: FFmpeg's asetrate relabels the stream to an
//! ABSOLUTE rate (44100*factor), regardless of the input's real rate. With
//! the typical 48 kHz Opus sources from YouTube, `nightcore` therefore plays
//! at 55125/48000 = 1.148x — not 1.25x. The resampler reproduces exactly
//! that: step = asetrate / 48000, independent of the source rate.

pub const OUTPUT_RATE: u32 = 48_000;

/// Real playback speed for a set of filter names relative to the 48 kHz
/// output (asetrate semantics) — used for position bookkeeping by callers.
pub fn effective_speed(names: &[String]) -> f64 {
    let mut asetrate = None;
    for name in names {
        match name.as_str() {
            "slowed" => asetrate = Some(44_100.0 * 0.8),
            "spedup" => asetrate = Some(44_100.0 * 1.2),
            "nightcore" => asetrate = Some(44_100.0 * 1.25),
            _ => {}
        }
    }
    asetrate.map(|a| a / OUTPUT_RATE as f64).unwrap_or(1.0)
}

/// A parsed filter chain: one optional absolute asetrate target (applied by
/// the resampler) plus an ordered list of per-sample effects.
pub struct FilterChain {
    /// Absolute rate label, FFmpeg-style. In a chained graph each asetrate
    /// overwrites the previous label, so the last named speed filter wins.
    pub asetrate: Option<f64>,
    effects: Vec<Effect>,
}

enum Effect {
    Lowpass(Biquad),
    LowShelf(Biquad),
    Echo(Echo),
    Pulsator(Pulsator),
    Crusher(Crusher),
}

impl FilterChain {
    /// Builds a chain from Playify filter names. Returns None if any name
    /// is unknown, in which case the caller should fall back to FFmpeg.
    pub fn from_names(names: &[String]) -> Option<Self> {
        let mut asetrate = None;
        let mut effects = Vec::new();
        for name in names {
            match name.as_str() {
                "slowed" => asetrate = Some(44_100.0 * 0.8),
                "spedup" => asetrate = Some(44_100.0 * 1.2),
                "nightcore" => asetrate = Some(44_100.0 * 1.25),
                "muffled" => effects.push(Effect::Lowpass(Biquad::lowpass(500.0, 0.707))),
                "bassboost" => {
                    // ffmpeg `bass` defaults: f=100 Hz, width_type=q, width=0.5.
                    effects.push(Effect::LowShelf(Biquad::low_shelf_q(100.0, 10.0, 0.5)))
                }
                "reverb" => effects.push(Effect::Echo(Echo::new(
                    0.8,
                    0.9,
                    &[(40.0, 0.4), (50.0, 0.3), (60.0, 0.2)],
                ))),
                "8d" => effects.push(Effect::Pulsator(Pulsator::new(0.08))),
                "earrape" => effects.push(Effect::Crusher(Crusher::new(8.0, 18.0, 8))),
                _ => return None,
            }
        }
        Some(FilterChain { asetrate, effects })
    }

    /// Processes interleaved stereo frames in place.
    pub fn process(&mut self, samples: &mut [f32]) {
        for effect in &mut self.effects {
            match effect {
                Effect::Lowpass(b) | Effect::LowShelf(b) => b.process(samples),
                Effect::Echo(e) => e.process(samples),
                Effect::Pulsator(p) => p.process(samples),
                Effect::Crusher(c) => c.process(samples),
            }
        }
        // Keep the mixer safe from hot chains (earrape, stacked boosts).
        for s in samples.iter_mut() {
            *s = s.clamp(-1.0, 1.0);
        }
    }
}

// ---------------------------------------------------------------------------
// Resampler: converts the source to 48 kHz while reproducing FFmpeg's
// asetrate relabelling (speed and pitch change together). The consumption
// step is `asetrate / 48000` — independent of the source's real rate,
// exactly like `-af asetrate=A` followed by `-ar 48000`.
// Catmull-Rom (cubic) interpolation over stereo frames.
// ---------------------------------------------------------------------------

pub struct Resampler {
    /// Input frames consumed per output frame.
    step: f64,
    pos: f64,
    /// Pending input frames (stereo).
    buf: Vec<[f32; 2]>,
    bypass: bool,
}

impl Resampler {
    pub fn new(input_rate: u32, asetrate: Option<f64>) -> Self {
        let effective_rate = asetrate.unwrap_or(input_rate as f64);
        let step = effective_rate / OUTPUT_RATE as f64;
        Resampler {
            step,
            pos: 0.0,
            buf: Vec::with_capacity(8192),
            bypass: (step - 1.0).abs() < 1e-9,
        }
    }

    /// Feeds interleaved input samples, appends interleaved 48 kHz output
    /// to `out`.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        if self.bypass {
            out.extend_from_slice(input);
            return;
        }
        for frame in input.chunks_exact(2) {
            self.buf.push([frame[0], frame[1]]);
        }

        // Need index-1 .. index+2 around the read position for Catmull-Rom.
        loop {
            let index = self.pos.floor() as usize;
            if index + 2 >= self.buf.len() {
                break;
            }
            let frac = (self.pos - index as f64) as f32;
            let p0 = self.buf[index.saturating_sub(1)];
            let p1 = self.buf[index];
            let p2 = self.buf[index + 1];
            let p3 = self.buf[index + 2];
            out.push(catmull_rom(p0[0], p1[0], p2[0], p3[0], frac));
            out.push(catmull_rom(p0[1], p1[1], p2[1], p3[1], frac));
            self.pos += self.step;
        }

        // Drop consumed frames, keeping one behind the cursor for p0.
        let keep_from = (self.pos.floor() as usize).saturating_sub(1);
        if keep_from > 0 {
            self.buf.drain(..keep_from.min(self.buf.len()));
            self.pos -= keep_from as f64;
        }
    }
}

#[inline]
fn catmull_rom(p0: f32, p1: f32, p2: f32, p3: f32, t: f32) -> f32 {
    let t2 = t * t;
    let t3 = t2 * t;
    0.5 * ((2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
}

// ---------------------------------------------------------------------------
// Biquad (RBJ audio EQ cookbook), stereo state.
// ---------------------------------------------------------------------------

struct Biquad {
    b0: f32,
    b1: f32,
    b2: f32,
    a1: f32,
    a2: f32,
    // Direct form II transposed state, per channel.
    z1: [f32; 2],
    z2: [f32; 2],
}

impl Biquad {
    fn lowpass(freq: f32, q: f32) -> Self {
        let w0 = 2.0 * std::f32::consts::PI * freq / OUTPUT_RATE as f32;
        let (sin0, cos0) = w0.sin_cos();
        let alpha = sin0 / (2.0 * q);
        let b1 = 1.0 - cos0;
        let b0 = b1 / 2.0;
        let b2 = b0;
        let a0 = 1.0 + alpha;
        let a1 = -2.0 * cos0;
        let a2 = 1.0 - alpha;
        Self::normalized(b0, b1, b2, a0, a1, a2)
    }

    /// Low shelf with Q-factor width, matching ffmpeg af_biquads' QFACTOR
    /// mode (alpha = sin(w0) / (2*Q)) used by the `bass` filter.
    fn low_shelf_q(freq: f32, gain_db: f32, q: f32) -> Self {
        let a = 10f32.powf(gain_db / 40.0);
        let w0 = 2.0 * std::f32::consts::PI * freq / OUTPUT_RATE as f32;
        let (sin0, cos0) = w0.sin_cos();
        let alpha = sin0 / (2.0 * q);
        let two_sqrt_a_alpha = 2.0 * a.sqrt() * alpha;
        let b0 = a * ((a + 1.0) - (a - 1.0) * cos0 + two_sqrt_a_alpha);
        let b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos0);
        let b2 = a * ((a + 1.0) - (a - 1.0) * cos0 - two_sqrt_a_alpha);
        let a0 = (a + 1.0) + (a - 1.0) * cos0 + two_sqrt_a_alpha;
        let a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos0);
        let a2 = (a + 1.0) + (a - 1.0) * cos0 - two_sqrt_a_alpha;
        Self::normalized(b0, b1, b2, a0, a1, a2)
    }

    fn normalized(b0: f32, b1: f32, b2: f32, a0: f32, a1: f32, a2: f32) -> Self {
        Biquad {
            b0: b0 / a0,
            b1: b1 / a0,
            b2: b2 / a0,
            a1: a1 / a0,
            a2: a2 / a0,
            z1: [0.0; 2],
            z2: [0.0; 2],
        }
    }

    fn process(&mut self, samples: &mut [f32]) {
        for frame in samples.chunks_exact_mut(2) {
            for (ch, sample) in frame.iter_mut().enumerate() {
                let x = *sample;
                let y = self.b0 * x + self.z1[ch];
                self.z1[ch] = self.b1 * x - self.a1 * y + self.z2[ch];
                self.z2[ch] = self.b2 * x - self.a2 * y;
                *sample = y;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Echo: FIR multi-tap delay (ffmpeg aecho semantics, no feedback).
// ---------------------------------------------------------------------------

struct Echo {
    in_gain: f32,
    out_gain: f32,
    /// (delay in frames, decay) pairs.
    taps: Vec<(usize, f32)>,
    /// Ring buffer of past input frames.
    history: Vec<[f32; 2]>,
    write: usize,
}

impl Echo {
    fn new(in_gain: f32, out_gain: f32, taps_ms: &[(f32, f32)]) -> Self {
        let taps: Vec<(usize, f32)> = taps_ms
            .iter()
            .map(|&(ms, decay)| ((ms / 1000.0 * OUTPUT_RATE as f32) as usize, decay))
            .collect();
        let max_delay = taps.iter().map(|&(d, _)| d).max().unwrap_or(1) + 1;
        Echo {
            in_gain,
            out_gain,
            taps,
            history: vec![[0.0; 2]; max_delay],
            write: 0,
        }
    }

    fn process(&mut self, samples: &mut [f32]) {
        let len = self.history.len();
        for frame in samples.chunks_exact_mut(2) {
            let dry = [frame[0], frame[1]];
            self.history[self.write] = dry;
            let mut wet = [dry[0] * self.in_gain, dry[1] * self.in_gain];
            for &(delay, decay) in &self.taps {
                let idx = (self.write + len - delay) % len;
                wet[0] += self.history[idx][0] * decay;
                wet[1] += self.history[idx][1] * decay;
            }
            frame[0] = wet[0] * self.out_gain;
            frame[1] = wet[1] * self.out_gain;
            self.write = (self.write + 1) % len;
        }
    }
}

// ---------------------------------------------------------------------------
// Pulsator: slow sinusoidal panning between channels ("8D audio").
// ---------------------------------------------------------------------------

struct Pulsator {
    phase: f32,
    step: f32,
}

impl Pulsator {
    fn new(hz: f32) -> Self {
        Pulsator {
            phase: 0.0,
            step: 2.0 * std::f32::consts::PI * hz / OUTPUT_RATE as f32,
        }
    }

    fn process(&mut self, samples: &mut [f32]) {
        for frame in samples.chunks_exact_mut(2) {
            let lfo = self.phase.sin();
            // Constant total energy pan: left/right gains in opposite phase.
            let left = 0.5 * (1.0 + lfo);
            let right = 0.5 * (1.0 - lfo);
            frame[0] *= left;
            frame[1] *= right;
            self.phase += self.step;
            if self.phase > 2.0 * std::f32::consts::PI {
                self.phase -= 2.0 * std::f32::consts::PI;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Crusher: drive + bit quantization, approximating ffmpeg acrusher with
// level_in=8, level_out=18, bits=8 (the "earrape" preset). Deliberately ugly.
// ---------------------------------------------------------------------------

struct Crusher {
    level_in: f32,
    level_out: f32,
    steps: f32,
}

impl Crusher {
    fn new(level_in: f32, level_out: f32, bits: u32) -> Self {
        Crusher {
            level_in,
            level_out,
            steps: (1u32 << (bits - 1)) as f32,
        }
    }

    fn process(&mut self, samples: &mut [f32]) {
        for s in samples.iter_mut() {
            let driven = (*s * self.level_in).clamp(-1.0, 1.0);
            let quantized = (driven * self.steps).round() / self.steps;
            *s = quantized * self.level_out;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_names_build_a_chain() {
        let names: Vec<String> = ["nightcore", "bassboost", "reverb", "8d", "muffled", "earrape"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let chain = FilterChain::from_names(&names).expect("all names known");
        // nightcore = asetrate=44100*1.25 = 55125 Hz, absolute.
        assert_eq!(chain.asetrate, Some(55_125.0));
    }

    #[test]
    fn unknown_name_rejects_chain() {
        assert!(FilterChain::from_names(&["wobble".to_string()]).is_none());
    }

    #[test]
    fn resampler_matches_ffmpeg_asetrate_semantics() {
        // 1 s of 48 kHz input relabelled to 55125 Hz (nightcore), resampled
        // back to 48 kHz: duration shrinks by 48000/55125 (speed 1.148x),
        // exactly like `-af asetrate=55125 -ar 48000` — NOT by 1.25x.
        let mut resampler = Resampler::new(48_000, Some(55_125.0));
        let input = vec![0.5f32; 48_000 * 2];
        let mut out = Vec::new();
        resampler.process(&input, &mut out);
        let expected = (48_000f64 * 48_000.0 / 55_125.0) as usize * 2;
        assert!((out.len() as i64 - expected as i64).abs() < 32);
    }

    #[test]
    fn resampler_without_asetrate_only_converts_rate() {
        // 44.1 kHz source without speed filters: plain rate conversion.
        let mut resampler = Resampler::new(44_100, None);
        let input = vec![0.25f32; 44_100 * 2];
        let mut out = Vec::new();
        resampler.process(&input, &mut out);
        assert!((out.len() as i64 - (48_000 * 2) as i64).abs() < 32);
    }

    #[test]
    fn dsp_chain_processes_without_nan() {
        let names: Vec<String> = ["bassboost", "reverb", "8d"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let mut chain = FilterChain::from_names(&names).unwrap();
        let mut samples: Vec<f32> = (0..9600)
            .map(|i| (i as f32 * 0.01).sin() * 0.5)
            .collect();
        chain.process(&mut samples);
        assert!(samples.iter().all(|s| s.is_finite() && s.abs() <= 1.0));
    }
}
