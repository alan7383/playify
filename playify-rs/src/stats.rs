//! Node statistics: shared counters, the `stats` WebSocket op payload, and
//! a minimal Prometheus-format HTTP endpoint (`GET /metrics`).
//!
//! The endpoint binds 127.0.0.1 only, like the control plane. It exists for
//! two consumers: the Playify TUI dashboard (polls it to display node
//! activity next to the Python process stats) and any standard monitoring
//! stack (Prometheus/Grafana) a self-hoster might already run.

use std::{
    collections::BTreeMap,
    sync::atomic::{AtomicU64, Ordering},
    time::Instant,
};

use serde_json::{json, Value};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};
use tracing::{info, warn};

use crate::Sessions;

pub struct NodeMetrics {
    started: Instant,
    tracks_played: AtomicU64,
}

impl NodeMetrics {
    pub fn new() -> Self {
        NodeMetrics {
            started: Instant::now(),
            tracks_played: AtomicU64::new(0),
        }
    }

    pub fn track_started(&self) {
        self.tracks_played.fetch_add(1, Ordering::Relaxed);
    }
}

struct Snapshot {
    uptime_seconds: u64,
    memory_bytes: u64,
    sessions: usize,
    tracks_playing: usize,
    tracks_played_total: u64,
    engines: BTreeMap<&'static str, usize>,
}

async fn snapshot(sessions: &Sessions, metrics: &NodeMetrics) -> Snapshot {
    let sessions_guard = sessions.lock().await;
    let mut engines: BTreeMap<&'static str, usize> = BTreeMap::new();
    let mut tracks_playing = 0;
    for session in sessions_guard.values() {
        if session.handle.is_some() {
            tracks_playing += 1;
            *engines.entry(session.engine).or_insert(0) += 1;
        }
    }
    Snapshot {
        uptime_seconds: metrics.started.elapsed().as_secs(),
        memory_bytes: memory_stats::memory_stats()
            .map(|m| m.physical_mem as u64)
            .unwrap_or(0),
        sessions: sessions_guard.len(),
        tracks_playing,
        tracks_played_total: metrics.tracks_played.load(Ordering::Relaxed),
        engines,
    }
}

/// JSON payload for the `stats` WebSocket op.
pub async fn stats_json(sessions: &Sessions, metrics: &NodeMetrics) -> Value {
    let snap = snapshot(sessions, metrics).await;
    json!({
        "version": env!("CARGO_PKG_VERSION"),
        "uptime_seconds": snap.uptime_seconds,
        "memory_bytes": snap.memory_bytes,
        "sessions": snap.sessions,
        "tracks_playing": snap.tracks_playing,
        "tracks_played_total": snap.tracks_played_total,
        "engines": snap.engines.iter().map(|(k, v)| (k.to_string(), json!(v))).collect::<serde_json::Map<_, _>>(),
    })
}

/// Prometheus text exposition (format 0.0.4).
async fn prometheus_text(sessions: &Sessions, metrics: &NodeMetrics) -> String {
    let snap = snapshot(sessions, metrics).await;
    let mut out = String::with_capacity(1024);

    out.push_str("# HELP playify_node_info Node build information.\n");
    out.push_str("# TYPE playify_node_info gauge\n");
    out.push_str(&format!(
        "playify_node_info{{version=\"{}\"}} 1\n",
        env!("CARGO_PKG_VERSION")
    ));

    out.push_str("# HELP playify_node_uptime_seconds Seconds since the node started.\n");
    out.push_str("# TYPE playify_node_uptime_seconds counter\n");
    out.push_str(&format!(
        "playify_node_uptime_seconds {}\n",
        snap.uptime_seconds
    ));

    out.push_str("# HELP playify_node_memory_bytes Resident memory of the node process.\n");
    out.push_str("# TYPE playify_node_memory_bytes gauge\n");
    out.push_str(&format!("playify_node_memory_bytes {}\n", snap.memory_bytes));

    out.push_str("# HELP playify_node_sessions Voice sessions currently held.\n");
    out.push_str("# TYPE playify_node_sessions gauge\n");
    out.push_str(&format!("playify_node_sessions {}\n", snap.sessions));

    out.push_str("# HELP playify_node_tracks_playing Tracks currently playing.\n");
    out.push_str("# TYPE playify_node_tracks_playing gauge\n");
    out.push_str(&format!(
        "playify_node_tracks_playing {}\n",
        snap.tracks_playing
    ));

    out.push_str("# HELP playify_node_tracks_played_total Tracks started since node launch.\n");
    out.push_str("# TYPE playify_node_tracks_played_total counter\n");
    out.push_str(&format!(
        "playify_node_tracks_played_total {}\n",
        snap.tracks_played_total
    ));

    out.push_str(
        "# HELP playify_node_engine_tracks Currently playing tracks by decode engine.\n",
    );
    out.push_str("# TYPE playify_node_engine_tracks gauge\n");
    for engine in ["direct", "dsp", "ffmpeg"] {
        let count = snap.engines.get(engine).copied().unwrap_or(0);
        out.push_str(&format!(
            "playify_node_engine_tracks{{engine=\"{engine}\"}} {count}\n"
        ));
    }

    out
}

/// Serves `GET /metrics` over plain HTTP/1.1, one request per connection.
/// Deliberately tiny: no routing framework needed for a single local path.
pub async fn run_metrics_server(port: u16, sessions: Sessions, metrics: std::sync::Arc<NodeMetrics>) {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let listener = match TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            warn!("metrics endpoint disabled: cannot bind {addr}: {e}");
            return;
        }
    };
    info!("metrics endpoint listening on http://{addr}/metrics");

    loop {
        let Ok((mut stream, _)) = listener.accept().await else {
            continue;
        };
        let sessions = sessions.clone();
        let metrics = metrics.clone();
        tokio::spawn(async move {
            let mut buf = [0u8; 1024];
            let Ok(n) = stream.read(&mut buf).await else {
                return;
            };
            let request = String::from_utf8_lossy(&buf[..n]);
            let first_line = request.lines().next().unwrap_or_default();

            let (status, body) = if first_line.starts_with("GET /metrics") {
                (
                    "200 OK",
                    prometheus_text(&sessions, &metrics).await,
                )
            } else {
                ("404 Not Found", "not found\n".to_string())
            };

            let response = format!(
                "HTTP/1.1 {status}\r\n\
                 Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n\
                 Content-Length: {}\r\n\
                 Connection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes()).await;
            let _ = stream.shutdown().await;
        });
    }
}
