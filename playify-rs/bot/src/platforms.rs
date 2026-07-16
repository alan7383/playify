//! Music-platform URL expansion.
//!
//! Same strategy as the Python bot: Spotify/Deezer/Apple Music/Tidal/Amazon
//! links are resolved into (title, artist) metadata, then each track becomes
//! a lazy `ytsearch1:` entry played from YouTube. SoundCloud, Bandcamp,
//! Twitch, YouTube and direct links go straight through yt-dlp and never
//! reach this module.

use serde_json::Value;
use tracing::debug;

use crate::ytdlp::Resolved;

const TRACK_CAP: usize = 200;
const USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
                          (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

/// A track known only by metadata; played via YouTube search.
fn search_entry(title: &str, artist: &str) -> Resolved {
    let label = if artist.is_empty() {
        title.to_string()
    } else {
        format!("{title} — {artist}")
    };
    Resolved {
        title: label,
        webpage_url: format!("ytsearch1:{title} {artist}"),
        stream_url: None,
        duration: 0.0,
        is_live: false,
        thumbnail: None,
    }
}

async fn fetch_text(http: &reqwest::Client, url: &str) -> Result<String, String> {
    http.get(url)
        .header("User-Agent", USER_AGENT)
        .header("Accept-Language", "en")
        .send()
        .await
        .map_err(|e| format!("request failed: {e}"))?
        .text()
        .await
        .map_err(|e| format!("read failed: {e}"))
}

fn extract_ld_json(html: &str) -> Vec<Value> {
    let mut blocks = Vec::new();
    let mut rest = html;
    while let Some(start) = rest.find("application/ld+json") {
        rest = &rest[start..];
        let Some(open) = rest.find('>') else { break };
        rest = &rest[open + 1..];
        let Some(close) = rest.find("</script>") else { break };
        if let Ok(value) = serde_json::from_str::<Value>(rest[..close].trim()) {
            blocks.push(value);
        }
        rest = &rest[close..];
    }
    blocks
}

fn meta_content(html: &str, property: &str) -> Option<String> {
    // <meta property="og:title" content="..."> (attribute order varies)
    for chunk in html.split("<meta ").skip(1) {
        let tag = &chunk[..chunk.find('>').unwrap_or(chunk.len())];
        if tag.contains(&format!("\"{property}\"")) || tag.contains(&format!("'{property}'")) {
            if let Some(pos) = tag.find("content=") {
                let rest = &tag[pos + 8..];
                let quote = rest.chars().next()?;
                let rest = &rest[1..];
                let end = rest.find(quote)?;
                return Some(html_unescape(&rest[..end]));
            }
        }
    }
    None
}

fn html_unescape(text: &str) -> String {
    text.replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&#x27;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
}

/// Pulls (title, artist) pairs out of schema.org MusicAlbum / MusicPlaylist /
/// MusicRecording JSON-LD, used by Apple Music, Amazon Music and Tidal pages.
fn tracks_from_ld(blocks: &[Value]) -> Vec<(String, String)> {
    fn name_of(value: &Value) -> Option<String> {
        value.get("name").and_then(Value::as_str).map(String::from)
    }
    fn artist_of(value: &Value) -> String {
        let by = value.get("byArtist");
        match by {
            Some(Value::Array(artists)) => artists
                .iter()
                .filter_map(name_of)
                .collect::<Vec<_>>()
                .join(", "),
            Some(object) => name_of(object).unwrap_or_default(),
            None => String::new(),
        }
    }

    let mut out = Vec::new();
    for block in blocks {
        // Sometimes wrapped in an array or @graph.
        let candidates: Vec<&Value> = match block {
            Value::Array(items) => items.iter().collect(),
            other => other
                .get("@graph")
                .and_then(Value::as_array)
                .map(|graph| graph.iter().collect())
                .unwrap_or_else(|| vec![other]),
        };
        for item in candidates {
            let kind = item.get("@type").and_then(Value::as_str).unwrap_or("");
            match kind {
                "MusicRecording" => {
                    if let Some(title) = name_of(item) {
                        out.push((title, artist_of(item)));
                    }
                }
                "MusicAlbum" | "MusicPlaylist" => {
                    let album_artist = artist_of(item);
                    let tracks = item
                        .get("track")
                        .or_else(|| item.get("tracks"))
                        .and_then(Value::as_array);
                    if let Some(tracks) = tracks {
                        for track in tracks.iter().take(TRACK_CAP) {
                            if let Some(title) = name_of(track) {
                                let artist = {
                                    let a = artist_of(track);
                                    if a.is_empty() { album_artist.clone() } else { a }
                                };
                                out.push((title, artist));
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        if !out.is_empty() {
            break;
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Spotify: the /embed/ pages ship the entity as JSON (no API key needed),
// which is exactly what SpotifyScraper does on the Python side.
// ---------------------------------------------------------------------------

async fn spotify(http: &reqwest::Client, url: &str) -> Result<Vec<Resolved>, String> {
    let (kind, id) = ["track", "album", "playlist"]
        .iter()
        .find_map(|kind| {
            let marker = format!("/{kind}/");
            url.find(&marker).map(|pos| {
                let id: String = url[pos + marker.len()..]
                    .chars()
                    .take_while(|c| c.is_ascii_alphanumeric())
                    .collect();
                (*kind, id)
            })
        })
        .ok_or("unsupported Spotify link (use track/album/playlist)")?;
    if id.is_empty() {
        return Err("cannot parse Spotify id".into());
    }

    let html = fetch_text(http, &format!("https://open.spotify.com/embed/{kind}/{id}")).await?;
    let json_start = html
        .find("__NEXT_DATA__")
        .and_then(|pos| html[pos..].find('>').map(|open| pos + open + 1))
        .ok_or("Spotify embed data not found")?;
    let json_end = html[json_start..]
        .find("</script>")
        .ok_or("Spotify embed data truncated")?;
    let data: Value = serde_json::from_str(html[json_start..json_start + json_end].trim())
        .map_err(|e| format!("Spotify embed json: {e}"))?;

    let entity = data
        .pointer("/props/pageProps/state/data/entity")
        .ok_or("Spotify entity missing")?;

    if kind == "track" {
        let title = entity.get("name").and_then(Value::as_str).ok_or("no title")?;
        let artist = entity
            .get("artists")
            .and_then(Value::as_array)
            .map(|artists| {
                artists
                    .iter()
                    .filter_map(|a| a.get("name").and_then(Value::as_str))
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        return Ok(vec![search_entry(title, &artist)]);
    }

    let track_list = entity
        .get("trackList")
        .and_then(Value::as_array)
        .ok_or("Spotify track list missing")?;
    let tracks: Vec<Resolved> = track_list
        .iter()
        .take(TRACK_CAP)
        .filter_map(|track| {
            let title = track.get("title").and_then(Value::as_str)?;
            let artist = track.get("subtitle").and_then(Value::as_str).unwrap_or("");
            Some(search_entry(title, artist))
        })
        .collect();
    debug!("spotify {kind}: {} tracks", tracks.len());
    if tracks.is_empty() {
        Err("empty Spotify track list".into())
    } else {
        Ok(tracks)
    }
}

// ---------------------------------------------------------------------------
// Deezer: fully public JSON API.
// ---------------------------------------------------------------------------

async fn deezer(http: &reqwest::Client, url: &str) -> Result<Vec<Resolved>, String> {
    // Short links redirect to the real page.
    let resolved_url = if url.contains("page.link") {
        http.get(url)
            .header("User-Agent", USER_AGENT)
            .send()
            .await
            .map_err(|e| format!("deezer redirect: {e}"))?
            .url()
            .to_string()
    } else {
        url.to_string()
    };

    let (kind, id) = ["track", "album", "playlist"]
        .iter()
        .find_map(|kind| {
            let marker = format!("/{kind}/");
            resolved_url.find(&marker).map(|pos| {
                let id: String = resolved_url[pos + marker.len()..]
                    .chars()
                    .take_while(char::is_ascii_digit)
                    .collect();
                (*kind, id)
            })
        })
        .ok_or("unsupported Deezer link")?;

    let api = format!("https://api.deezer.com/{kind}/{id}");
    let data: Value = http
        .get(&api)
        .send()
        .await
        .map_err(|e| format!("deezer api: {e}"))?
        .json()
        .await
        .map_err(|e| format!("deezer json: {e}"))?;

    if kind == "track" {
        let title = data.get("title").and_then(Value::as_str).ok_or("no title")?;
        let artist = data
            .pointer("/artist/name")
            .and_then(Value::as_str)
            .unwrap_or("");
        return Ok(vec![search_entry(title, artist)]);
    }

    let tracks: Vec<Resolved> = data
        .pointer("/tracks/data")
        .and_then(Value::as_array)
        .ok_or("deezer track list missing")?
        .iter()
        .take(TRACK_CAP)
        .filter_map(|track| {
            let title = track.get("title").and_then(Value::as_str)?;
            let artist = track
                .pointer("/artist/name")
                .and_then(Value::as_str)
                .unwrap_or("");
            Some(search_entry(title, artist))
        })
        .collect();
    if tracks.is_empty() {
        Err("empty Deezer track list".into())
    } else {
        Ok(tracks)
    }
}

// ---------------------------------------------------------------------------
// Apple Music / Amazon Music / Tidal: JSON-LD first, og: meta fallback.
// ---------------------------------------------------------------------------

async fn scraped_page(
    http: &reqwest::Client,
    url: &str,
    label: &str,
) -> Result<Vec<Resolved>, String> {
    let html = fetch_text(http, url).await?;

    let from_ld = tracks_from_ld(&extract_ld_json(&html));
    if !from_ld.is_empty() {
        return Ok(from_ld
            .iter()
            .take(TRACK_CAP)
            .map(|(title, artist)| search_entry(title, artist))
            .collect());
    }

    // Fallback: og:title like "Song by Artist on Apple Music" or
    // "Song - song by Artist | Spotify"-style titles.
    let title = meta_content(&html, "og:title")
        .or_else(|| {
            html.find("<title>").and_then(|start| {
                html[start + 7..]
                    .find("</title>")
                    .map(|end| html_unescape(&html[start + 7..start + 7 + end]))
            })
        })
        .ok_or_else(|| format!("cannot extract metadata from this {label} page"))?;

    let cleaned = title
        .split(" on Apple Music")
        .next()
        .unwrap_or(&title)
        .split(" | ")
        .next()
        .unwrap_or(&title)
        .trim()
        .to_string();
    let (song, artist) = match cleaned.split_once(" by ") {
        Some((song, artist)) => (song.trim().to_string(), artist.trim().to_string()),
        None => (cleaned, String::new()),
    };
    Ok(vec![search_entry(&song, &artist)])
}

/// Expands a platform URL into playable entries, or None if the URL does
/// not belong to a metadata platform (caller then goes through yt-dlp).
pub async fn expand(http: &reqwest::Client, url: &str) -> Option<Result<Vec<Resolved>, String>> {
    if url.contains("open.spotify.com") || url.contains("spotify.link") {
        Some(spotify(http, url).await.map_err(|e| format!("Spotify: {e}")))
    } else if url.contains("deezer.com") || url.contains("deezer.page.link") {
        Some(deezer(http, url).await.map_err(|e| format!("Deezer: {e}")))
    } else if url.contains("music.apple.com") {
        Some(scraped_page(http, url, "Apple Music").await)
    } else if url.contains("music.amazon") {
        Some(scraped_page(http, url, "Amazon Music").await)
    } else if url.contains("tidal.com") {
        Some(scraped_page(http, url, "Tidal").await)
    } else {
        None
    }
}
