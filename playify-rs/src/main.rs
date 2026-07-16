//! Playify audio node.
//!
//! A standalone voice driver for Discord built on songbird. The Python bot
//! keeps the gateway connection and forwards voice credentials
//! (VOICE_STATE_UPDATE / VOICE_SERVER_UPDATE payloads) over a local
//! WebSocket; this node owns the UDP voice connection, decoding, Opus
//! encoding, encryption and the 20 ms send loop.
//!
//! Protocol (JSON text frames):
//!   Python -> node: {"op": "...", "request_id": n, "guild_id": ..., ...}
//!   node -> Python: {"op": "response", "request_id": n, "ok": bool, "error": ...}
//!                   {"op": "event", "event": "track_end" | "track_error"
//!                    | "driver_connect" | "driver_disconnect" | "driver_reconnect",
//!                    "guild_id": ...}

mod dsp;
mod pipeline;

use std::{
    collections::HashMap, net::SocketAddr, num::NonZeroU64, process::Stdio, sync::Arc,
    time::Duration,
};

use async_trait::async_trait;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use songbird::{
    driver::Driver,
    events::{context_data::DisconnectReason, CoreEvent, Event, EventContext, EventHandler, TrackEvent},
    id::{ChannelId, GuildId, UserId},
    input::{File, HttpRequest, Input, RawAdapter},
    tracks::{Track, TrackHandle},
    Config, ConnectionInfo,
};
use symphonia::core::io::ReadOnlySource;

use crate::dsp::FilterChain;
use tokio::{
    net::{TcpListener, TcpStream},
    sync::{broadcast, Mutex},
};
use tokio_tungstenite::tungstenite::Message;
use tracing::{error, info, warn};

const SAMPLE_RATE: u32 = 48_000;
const CHANNELS: u32 = 2;

struct Session {
    driver: Driver,
    handle: Option<TrackHandle>,
    ffmpeg: Option<std::process::Child>,
    volume: f32,
    track_id: u64,
    /// Which decode engine the current track uses: "direct", "dsp", "ffmpeg".
    engine: &'static str,
}

impl Session {
    fn kill_ffmpeg(&mut self) {
        if let Some(mut child) = self.ffmpeg.take() {
            match child.try_wait() {
                Ok(Some(_)) => {} // already exited
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    }

    fn stop_track(&mut self) {
        if let Some(handle) = self.handle.take() {
            let _ = handle.stop();
        }
        self.kill_ffmpeg();
    }
}

type Sessions = Arc<Mutex<HashMap<u64, Session>>>;

/// Forwards songbird track/driver events to every connected control client.
struct Notifier {
    guild_id: u64,
    event_name: &'static str,
    /// Which play() call this notifier belongs to; None for driver-level events.
    track_id: Option<u64>,
    events_tx: broadcast::Sender<String>,
    sessions: Sessions,
}

#[async_trait]
impl EventHandler for Notifier {
    async fn act(&self, ctx: &EventContext<'_>) -> Option<Event> {
        let mut payload = json!({
            "op": "event",
            "event": self.event_name,
            "guild_id": self.guild_id,
        });
        if let Some(track_id) = self.track_id {
            payload["track_id"] = json!(track_id);
        }

        match ctx {
            EventContext::Track(list) => {
                // A finished/errored track leaves a dead ffmpeg child behind:
                // reap it here so no zombie survives outside of an explicit stop.
                // Only the *current* track may clean up: a stale End event from a
                // replaced track must not kill the new track's ffmpeg.
                if self.event_name == "track_end" || self.event_name == "track_error" {
                    let mut sessions = self.sessions.lock().await;
                    if let Some(session) = sessions.get_mut(&self.guild_id) {
                        if self.track_id == Some(session.track_id) {
                            session.kill_ffmpeg();
                            session.handle = None;
                        }
                    }
                }
                if let Some((state, _)) = list.first() {
                    payload["playing"] = json!(format!("{:?}", state.playing));
                }
            }
            EventContext::DriverDisconnect(data) => {
                let reason = data
                    .reason
                    .map(|r| format!("{r:?}"))
                    .unwrap_or_else(|| "Unknown".into());
                payload["reason"] = json!(reason);
                // Requested/system disconnects (channel close, kick) should not
                // resurrect; the Python side decides whether to reconnect.
                if matches!(data.reason, Some(DisconnectReason::Io)) {
                    warn!(guild = self.guild_id, "driver lost connection (IO)");
                }
            }
            _ => {}
        }

        let _ = self.events_tx.send(payload.to_string());
        None
    }
}

#[derive(Deserialize)]
struct ConnectArgs {
    guild_id: u64,
    user_id: u64,
    session_id: String,
    token: String,
    endpoint: String,
    channel_id: Option<u64>,
}

#[derive(Deserialize)]
struct PlayArgs {
    guild_id: u64,
    url: String,
    #[serde(default)]
    source_type: String, // "http" | "file" | "ffmpeg"
    #[serde(default = "default_volume")]
    volume: f32,
    #[serde(default)]
    seek: f64,
    #[serde(default)]
    seek_pre_input: bool,
    #[serde(default)]
    filters: Option<String>,
    /// Playify filter names ("nightcore", "bassboost", ...). When every name
    /// is known, the native DSP pipeline is used instead of FFmpeg.
    #[serde(default)]
    filter_names: Vec<String>,
    #[serde(default)]
    reconnect_flags: bool,
}

fn default_volume() -> f32 {
    1.0
}

fn build_ffmpeg(args: &PlayArgs) -> std::io::Result<std::process::Child> {
    let ffmpeg_bin = std::env::var("PLAYIFY_FFMPEG").unwrap_or_else(|_| "ffmpeg".into());
    let mut cmd = std::process::Command::new(ffmpeg_bin);

    if args.reconnect_flags {
        cmd.args([
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]);
    }
    if args.seek > 0.0 && args.seek_pre_input {
        cmd.args(["-ss", &format!("{}", args.seek)]);
    }
    cmd.args(["-i", &args.url, "-vn"]);
    if args.seek > 0.0 && !args.seek_pre_input {
        cmd.args(["-ss", &format!("{}", args.seek)]);
    }
    if let Some(filters) = args.filters.as_deref() {
        if !filters.is_empty() {
            cmd.args(["-af", filters]);
        }
    }
    cmd.args([
        "-f", "f32le",
        "-ar", &SAMPLE_RATE.to_string(),
        "-ac", &CHANNELS.to_string(),
        "-loglevel", "error",
        "pipe:1",
    ]);
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    cmd.spawn()
}

async fn handle_request(
    msg: Value,
    sessions: &Sessions,
    events_tx: &broadcast::Sender<String>,
    http_client: &reqwest::Client,
) -> Result<Value, String> {
    let op = msg.get("op").and_then(Value::as_str).unwrap_or_default();

    match op {
        "ping" => Ok(json!({"pong": true})),

        "connect" => {
            let args: ConnectArgs =
                serde_json::from_value(msg.clone()).map_err(|e| e.to_string())?;

            let endpoint = args
                .endpoint
                .trim_start_matches("wss://")
                .trim_start_matches("ws://")
                .to_string();

            let guild_id = NonZeroU64::new(args.guild_id).ok_or("guild_id cannot be 0")?;
            let user_id = NonZeroU64::new(args.user_id).ok_or("user_id cannot be 0")?;
            // Mandatory since songbird 0.6: the DAVE (E2EE) handshake needs to
            // know which channel's MLS group to join.
            let channel_id = args
                .channel_id
                .and_then(NonZeroU64::new)
                .ok_or("channel_id is required")?;

            let info = ConnectionInfo {
                channel_id: ChannelId::from(channel_id),
                endpoint,
                guild_id: GuildId::from(guild_id),
                session_id: args.session_id,
                token: args.token,
                user_id: UserId::from(user_id),
            };

            let mut sessions_guard = sessions.lock().await;
            let session = sessions_guard.entry(args.guild_id).or_insert_with(|| {
                let mut driver = Driver::new(Config::default());
                for (event, name) in [
                    (Event::Core(CoreEvent::DriverConnect), "driver_connect"),
                    (Event::Core(CoreEvent::DriverDisconnect), "driver_disconnect"),
                    (Event::Core(CoreEvent::DriverReconnect), "driver_reconnect"),
                ] {
                    driver.add_global_event(
                        event,
                        Notifier {
                            guild_id: args.guild_id,
                            event_name: name,
                            track_id: None,
                            events_tx: events_tx.clone(),
                            sessions: sessions.clone(),
                        },
                    );
                }
                Session {
                    driver,
                    handle: None,
                    ffmpeg: None,
                    volume: 1.0,
                    track_id: 0,
                    engine: "idle",
                }
            });

            let connect_fut = session.driver.connect(info);
            drop(sessions_guard); // don't hold the lock while the handshake runs

            tokio::time::timeout(Duration::from_secs(15), connect_fut)
                .await
                .map_err(|_| "voice connection timed out".to_string())?
                .map_err(|e| format!("voice connection failed: {e:?}"))?;

            info!(guild = args.guild_id, "voice connected");
            Ok(json!({"connected": true}))
        }

        "disconnect" => {
            let guild_id = require_guild(&msg)?;
            let mut sessions_guard = sessions.lock().await;
            if let Some(mut session) = sessions_guard.remove(&guild_id) {
                session.stop_track();
                session.driver.leave();
            }
            info!(guild = guild_id, "voice disconnected");
            Ok(json!({"disconnected": true}))
        }

        "play" => {
            let args: PlayArgs =
                serde_json::from_value(msg.clone()).map_err(|e| e.to_string())?;
            let guild_id = args.guild_id;

            // Engine selection, most native first (no lock held while the
            // source is opened):
            //   - "dsp":    in-process symphonia decode + native filters/seek
            //   - "direct": songbird's own lazy input (no filters, no seek)
            //   - "ffmpeg": live/HLS streams, unknown filters, or the
            //               PLAYIFY_FORCE_FFMPEG escape hatch
            let force_ffmpeg = std::env::var("PLAYIFY_FORCE_FFMPEG")
                .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                .unwrap_or(false);

            let mut ffmpeg_child: Option<std::process::Child> = None;
            let (input, engine): (Input, &'static str) = 'engine: {
                if args.source_type != "ffmpeg" && !force_ffmpeg {
                    if !args.filter_names.is_empty() || args.seek > 0.0 {
                        if let Some(chain) = FilterChain::from_names(&args.filter_names) {
                            match pipeline::build_dsp_input(
                                &args.source_type,
                                &args.url,
                                chain,
                                args.seek,
                            ) {
                                Ok(input) => break 'engine (input, "dsp"),
                                Err(e) => warn!(
                                    "dsp pipeline unavailable ({e}), falling back to ffmpeg"
                                ),
                            }
                        } else {
                            warn!(
                                "unknown filter in {:?}, falling back to ffmpeg",
                                args.filter_names
                            );
                        }
                    } else if args.source_type == "file" {
                        break 'engine (File::new(args.url.clone()).into(), "direct");
                    } else {
                        break 'engine (
                            HttpRequest::new(http_client.clone(), args.url.clone()).into(),
                            "direct",
                        );
                    }
                }

                let mut child =
                    build_ffmpeg(&args).map_err(|e| format!("failed to spawn ffmpeg: {e}"))?;
                let stdout = child.stdout.take().ok_or("ffmpeg has no stdout")?;
                ffmpeg_child = Some(child);
                (
                    RawAdapter::new(ReadOnlySource::new(stdout), SAMPLE_RATE, CHANNELS).into(),
                    "ffmpeg",
                )
            };

            let mut sessions_guard = sessions.lock().await;
            let session = sessions_guard
                .get_mut(&guild_id)
                .ok_or_else(|| format!("no session for guild {guild_id}"))?;

            // One track per guild: replace whatever is playing.
            session.stop_track();
            session.ffmpeg = ffmpeg_child;
            session.engine = engine;

            session.track_id += 1;
            let track_id = session.track_id;

            let track = Track::from(input).volume(args.volume);
            let handle = session.driver.play_only(track);

            for (event, name) in [
                (Event::Track(TrackEvent::End), "track_end"),
                (Event::Track(TrackEvent::Error), "track_error"),
            ] {
                let _ = handle.add_event(
                    event,
                    Notifier {
                        guild_id,
                        event_name: name,
                        track_id: Some(track_id),
                        events_tx: events_tx.clone(),
                        sessions: sessions.clone(),
                    },
                );
            }

            session.volume = args.volume;
            session.handle = Some(handle);
            info!(guild = guild_id, source = %args.source_type, engine, "playing track");
            Ok(json!({"playing": true, "track_id": track_id, "engine": engine}))
        }

        "stop" => {
            let guild_id = require_guild(&msg)?;
            let mut sessions_guard = sessions.lock().await;
            if let Some(session) = sessions_guard.get_mut(&guild_id) {
                session.stop_track();
            }
            Ok(json!({"stopped": true}))
        }

        "pause" => {
            let guild_id = require_guild(&msg)?;
            with_handle(sessions, guild_id, |h| h.pause()).await?;
            Ok(json!({"paused": true}))
        }

        "resume" => {
            let guild_id = require_guild(&msg)?;
            with_handle(sessions, guild_id, |h| h.play()).await?;
            Ok(json!({"resumed": true}))
        }

        "set_volume" => {
            let guild_id = require_guild(&msg)?;
            let volume = msg
                .get("volume")
                .and_then(Value::as_f64)
                .ok_or("missing volume")? as f32;
            let mut sessions_guard = sessions.lock().await;
            let session = sessions_guard
                .get_mut(&guild_id)
                .ok_or_else(|| format!("no session for guild {guild_id}"))?;
            session.volume = volume;
            if let Some(handle) = &session.handle {
                handle.set_volume(volume).map_err(|e| format!("{e:?}"))?;
            }
            Ok(json!({"volume": volume}))
        }

        "status" => {
            let guild_id = require_guild(&msg)?;
            let sessions_guard = sessions.lock().await;
            let session = sessions_guard.get(&guild_id);
            Ok(json!({
                "connected": session.is_some(),
                "has_track": session.map(|s| s.handle.is_some()).unwrap_or(false),
                "engine": session.map(|s| s.engine).unwrap_or("idle"),
            }))
        }

        other => Err(format!("unknown op: {other}")),
    }
}

fn require_guild(msg: &Value) -> Result<u64, String> {
    msg.get("guild_id")
        .and_then(Value::as_u64)
        .ok_or_else(|| "missing guild_id".to_string())
}

async fn with_handle<F>(sessions: &Sessions, guild_id: u64, f: F) -> Result<(), String>
where
    F: FnOnce(&TrackHandle) -> songbird::tracks::TrackResult<()>,
{
    let sessions_guard = sessions.lock().await;
    let session = sessions_guard
        .get(&guild_id)
        .ok_or_else(|| format!("no session for guild {guild_id}"))?;
    let handle = session
        .handle
        .as_ref()
        .ok_or_else(|| format!("no active track for guild {guild_id}"))?;
    f(handle).map_err(|e| format!("{e:?}"))
}

async fn handle_client(
    stream: TcpStream,
    peer: SocketAddr,
    sessions: Sessions,
    events_tx: broadcast::Sender<String>,
    http_client: reqwest::Client,
    secret: Option<String>,
) {
    let ws = match tokio_tungstenite::accept_async(stream).await {
        Ok(ws) => ws,
        Err(e) => {
            warn!("websocket handshake failed from {peer}: {e}");
            return;
        }
    };
    info!("control client connected: {peer}");

    let (mut tx, mut rx) = ws.split();
    let mut events_rx = events_tx.subscribe();
    let mut authed = secret.is_none();

    loop {
        tokio::select! {
            // Push driver/track events to the client.
            event = events_rx.recv() => {
                match event {
                    Ok(text) => {
                        if authed && tx.send(Message::Text(text)).await.is_err() {
                            break;
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        warn!("client {peer} lagged, dropped {n} events");
                    }
                    Err(broadcast::error::RecvError::Closed) => break,
                }
            }

            // Handle requests from the client.
            frame = rx.next() => {
                let Some(Ok(frame)) = frame else { break };
                let Message::Text(text) = frame else {
                    if matches!(frame, Message::Close(_)) { break; }
                    continue;
                };
                let Ok(msg) = serde_json::from_str::<Value>(&text) else {
                    warn!("client {peer} sent invalid JSON");
                    continue;
                };

                let request_id = msg.get("request_id").cloned().unwrap_or(Value::Null);

                if !authed {
                    let provided = msg.get("secret").and_then(Value::as_str);
                    let ok = msg.get("op").and_then(Value::as_str) == Some("auth")
                        && provided == secret.as_deref();
                    authed = ok;
                    let reply = json!({
                        "op": "response", "request_id": request_id,
                        "ok": ok, "error": if ok { Value::Null } else { json!("auth failed") },
                    });
                    if tx.send(Message::Text(reply.to_string())).await.is_err() || !ok {
                        break;
                    }
                    continue;
                }

                let reply = match handle_request(msg, &sessions, &events_tx, &http_client).await {
                    Ok(data) => json!({
                        "op": "response", "request_id": request_id, "ok": true, "data": data,
                    }),
                    Err(err) => {
                        error!("request failed: {err}");
                        json!({
                            "op": "response", "request_id": request_id, "ok": false, "error": err,
                        })
                    }
                };
                if tx.send(Message::Text(reply.to_string())).await.is_err() {
                    break;
                }
            }
        }
    }

    info!("control client disconnected: {peer}");
    // Voice sessions intentionally survive a control-plane disconnect:
    // the Python bot may simply be restarting and will re-attach.
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,songbird=warn".into()),
        )
        .init();

    let port: u16 = std::env::var("PLAYIFY_NODE_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8791);
    let secret = std::env::var("PLAYIFY_NODE_SECRET").ok().filter(|s| !s.is_empty());

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| panic!("cannot bind {addr}: {e}"));
    info!("playify-rs audio node listening on ws://{addr}");
    if secret.is_none() {
        info!("no PLAYIFY_NODE_SECRET set: accepting unauthenticated local connections");
    }

    let sessions: Sessions = Arc::new(Mutex::new(HashMap::new()));
    let (events_tx, _) = broadcast::channel::<String>(256);
    let http_client = reqwest::Client::new();

    loop {
        tokio::select! {
            accepted = listener.accept() => {
                match accepted {
                    Ok((stream, peer)) => {
                        tokio::spawn(handle_client(
                            stream,
                            peer,
                            sessions.clone(),
                            events_tx.clone(),
                            http_client.clone(),
                            secret.clone(),
                        ));
                    }
                    Err(e) => warn!("accept failed: {e}"),
                }
            }
            _ = tokio::signal::ctrl_c() => {
                info!("shutting down: leaving all voice sessions");
                let mut sessions_guard = sessions.lock().await;
                for (_, mut session) in sessions_guard.drain() {
                    session.stop_track();
                    session.driver.leave();
                }
                break;
            }
        }
    }
}
