//! Track resolution via the yt-dlp CLI, spawned per request.
//!
//! No resident Python: yt-dlp runs as a short-lived subprocess and its
//! memory is fully returned when the extraction finishes. This is the
//! trade the full-Rust bot makes versus v2's warm worker pool — a little
//! more latency per resolution, zero standing cost.

use std::{path::PathBuf, sync::OnceLock, time::Duration};

use serde_json::Value;
use tokio::process::Command;
use tracing::debug;

const FORMAT: &str = "bestaudio[acodec=opus]/bestaudio/best";
const PLAYLIST_CAP: usize = 200;

#[derive(Debug, Clone)]
pub struct Resolved {
    pub title: String,
    /// Canonical page URL, used to re-resolve a fresh stream URL at play
    /// time (YouTube stream URLs expire).
    pub webpage_url: String,
    /// Direct audio URL; None for lazily-resolved playlist entries.
    pub stream_url: Option<String>,
    pub duration: f64,
    pub is_live: bool,
    pub thumbnail: Option<String>,
}

fn ytdlp_binary() -> &'static PathBuf {
    static BINARY: OnceLock<PathBuf> = OnceLock::new();
    BINARY.get_or_init(|| {
        if let Ok(path) = std::env::var("PLAYIFY_YTDLP") {
            return PathBuf::from(path);
        }
        let exe = if cfg!(windows) { "yt-dlp.exe" } else { "yt-dlp" };
        // The repo's venv has yt-dlp installed; prefer it so v3 needs no
        // separate install next to a v2 checkout.
        for candidate in [
            PathBuf::from("../.venv/Scripts").join(exe),
            PathBuf::from(".venv/Scripts").join(exe),
            PathBuf::from("../.venv/bin").join(exe),
            PathBuf::from(".venv/bin").join(exe),
        ] {
            if candidate.exists() {
                return candidate;
            }
        }
        PathBuf::from(exe) // PATH fallback
    })
}

fn looks_like_url(query: &str) -> bool {
    query.starts_with("http://") || query.starts_with("https://")
}

fn looks_like_playlist(url: &str) -> bool {
    url.contains("list=") || url.contains("/playlist") || url.contains("/sets/")
}

async fn run_ytdlp(args: &[&str]) -> Result<Value, String> {
    let mut command = Command::new(ytdlp_binary());
    command
        .args(["-J", "--no-warnings", "--no-color", "-f", FORMAT])
        .args(args)
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let output = tokio::time::timeout(Duration::from_secs(60), command.output())
        .await
        .map_err(|_| "yt-dlp timed out".to_string())?
        .map_err(|e| format!("cannot run yt-dlp ({e}); set PLAYIFY_YTDLP"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let last = stderr.lines().last().unwrap_or("unknown yt-dlp error");
        return Err(last.to_string());
    }
    serde_json::from_slice(&output.stdout).map_err(|e| format!("bad yt-dlp json: {e}"))
}

fn entry_to_resolved(entry: &Value, lazy: bool) -> Option<Resolved> {
    let webpage_url = entry
        .get("webpage_url")
        .or_else(|| entry.get("url"))
        .and_then(Value::as_str)?
        .to_string();
    Some(Resolved {
        title: entry
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("Unknown Title")
            .to_string(),
        stream_url: if lazy {
            None
        } else {
            entry.get("url").and_then(Value::as_str).map(String::from)
        },
        duration: entry.get("duration").and_then(Value::as_f64).unwrap_or(0.0),
        is_live: entry.get("is_live").and_then(Value::as_bool).unwrap_or(false)
            || entry.get("live_status").and_then(Value::as_str) == Some("is_live"),
        thumbnail: entry
            .get("thumbnail")
            .and_then(Value::as_str)
            .map(String::from),
        webpage_url,
    })
}

/// Resolves a query (URL, playlist URL, or free-text search) into tracks.
pub async fn resolve(query: &str) -> Result<Vec<Resolved>, String> {
    let query = query.trim();

    if looks_like_url(query) && looks_like_playlist(query) {
        // Playlists resolve flat (title + page URL only); each entry gets a
        // fresh stream URL right before it plays.
        let info = run_ytdlp(&["--flat-playlist", query]).await?;
        let entries = info
            .get("entries")
            .and_then(Value::as_array)
            .ok_or("playlist has no entries")?;
        let tracks: Vec<Resolved> = entries
            .iter()
            .take(PLAYLIST_CAP)
            .filter_map(|entry| entry_to_resolved(entry, true))
            .collect();
        debug!("resolved playlist: {} tracks", tracks.len());
        return Ok(tracks);
    }

    let target = if looks_like_url(query) {
        query.to_string()
    } else {
        format!("ytsearch1:{query}")
    };
    let info = run_ytdlp(&["--no-playlist", &target]).await?;

    // Searches come back as a one-entry playlist.
    if let Some(entries) = info.get("entries").and_then(Value::as_array) {
        let first = entries.first().ok_or("no result")?;
        return entry_to_resolved(first, false)
            .map(|r| vec![r])
            .ok_or_else(|| "unusable search result".to_string());
    }
    entry_to_resolved(&info, false)
        .map(|r| vec![r])
        .ok_or_else(|| "unusable track data".to_string())
}

/// Re-resolves a single track's fresh stream URL (YouTube URLs expire).
/// Also accepts `ytsearch1:` targets (lazy platform/playlist entries).
pub async fn fresh_stream_url(webpage_url: &str) -> Result<Resolved, String> {
    let info = run_ytdlp(&["--no-playlist", webpage_url]).await?;
    if let Some(entries) = info.get("entries").and_then(Value::as_array) {
        let first = entries.first().ok_or("no result")?;
        return entry_to_resolved(first, false).ok_or_else(|| "unusable track data".to_string());
    }
    entry_to_resolved(&info, false).ok_or_else(|| "unusable track data".to_string())
}

/// Fast flat search returning several candidates (title + page URL only);
/// the chosen one resolves fresh at play time.
pub async fn search_flat(query: &str, count: usize) -> Result<Vec<Resolved>, String> {
    let target = format!("ytsearch{count}:{query}");
    let info = run_ytdlp(&["--flat-playlist", &target]).await?;
    Ok(info
        .get("entries")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| entry_to_resolved(entry, true))
                .collect()
        })
        .unwrap_or_default())
}

/// Autoplay: fetches the YouTube Mix (radio) for a seed video, excluding
/// the seed itself. SoundCloud stations work the same way through their
/// discover sets.
pub async fn autoplay_seeds(seed_url: &str) -> Result<Vec<Resolved>, String> {
    let mix_url = if seed_url.contains("youtube.com") || seed_url.contains("youtu.be") {
        let video_id = seed_url
            .split(['=', '/'])
            .last()
            .map(|id| id.split('&').next().unwrap_or(id))
            .filter(|id| !id.is_empty())
            .ok_or("cannot extract video id")?;
        format!("https://www.youtube.com/watch?v={video_id}&list=RD{video_id}")
    } else if seed_url.contains("soundcloud.com") {
        // Resolve the numeric track id, then use its station playlist.
        let info = run_ytdlp(&["--no-playlist", seed_url]).await?;
        let id = info
            .get("id")
            .and_then(Value::as_str)
            .ok_or("no soundcloud id")?
            .to_string();
        format!("https://soundcloud.com/discover/sets/track-stations:{id}")
    } else {
        return Err("autoplay needs a YouTube or SoundCloud seed".into());
    };

    let info = run_ytdlp(&["--flat-playlist", &mix_url]).await?;
    let entries = info
        .get("entries")
        .and_then(Value::as_array)
        .ok_or("mix has no entries")?;
    Ok(entries
        .iter()
        .skip(1) // the seed itself
        .take(25)
        .filter_map(|entry| entry_to_resolved(entry, true))
        .collect())
}
