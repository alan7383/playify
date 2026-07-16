//! Lyrics via LRCLIB (free, no key): plain text for /lyrics and
//! LRC-synced lines for /karaoke.

use serde_json::Value;

pub struct Lyrics {
    pub plain: Option<String>,
    pub synced: Option<Vec<(f64, String)>>, // (seconds, line)
}

fn clean_title(raw: &str) -> String {
    // Strip the usual YouTube noise so LRCLIB matches: (Official Video), [4K]...
    let mut out = String::new();
    let mut depth_round = 0i32;
    let mut depth_square = 0i32;
    for c in raw.chars() {
        match c {
            '(' => depth_round += 1,
            ')' => depth_round -= 1,
            '[' => depth_square += 1,
            ']' => depth_square -= 1,
            _ if depth_round <= 0 && depth_square <= 0 => out.push(c),
            _ => {}
        }
    }
    out.split(" - ")
        .collect::<Vec<_>>()
        .join(" ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn parse_lrc(lrc: &str) -> Vec<(f64, String)> {
    let mut lines = Vec::new();
    for line in lrc.lines() {
        // [mm:ss.xx] text
        let Some(rest) = line.strip_prefix('[') else { continue };
        let Some((stamp, text)) = rest.split_once(']') else { continue };
        let mut parts = stamp.split(':');
        let (Some(minutes), Some(seconds)) = (parts.next(), parts.next()) else {
            continue;
        };
        let (Ok(minutes), Ok(seconds)) = (minutes.parse::<f64>(), seconds.parse::<f64>()) else {
            continue;
        };
        lines.push((minutes * 60.0 + seconds, text.trim().to_string()));
    }
    lines.sort_by(|a, b| a.0.total_cmp(&b.0));
    lines
}

fn from_record(record: &Value) -> Lyrics {
    Lyrics {
        plain: record
            .get("plainLyrics")
            .and_then(Value::as_str)
            .map(String::from),
        synced: record
            .get("syncedLyrics")
            .and_then(Value::as_str)
            .map(parse_lrc)
            .filter(|lines| !lines.is_empty()),
    }
}

/// Fetches lyrics for a "Title — Artist"-style label (our queue format) or a
/// raw video title.
pub async fn fetch(http: &reqwest::Client, label: &str) -> Result<Lyrics, String> {
    let (title, artist) = match label.split_once(" — ") {
        Some((t, a)) => (clean_title(t), a.to_string()),
        None => (clean_title(label), String::new()),
    };

    // Exact lookup first, then fuzzy search.
    if !artist.is_empty() {
        let url = format!(
            "https://lrclib.net/api/get?track_name={}&artist_name={}",
            urlencode(&title),
            urlencode(&artist),
        );
        if let Ok(response) = http.get(&url).send().await {
            if response.status().is_success() {
                if let Ok(record) = response.json::<Value>().await {
                    return Ok(from_record(&record));
                }
            }
        }
    }

    let url = format!(
        "https://lrclib.net/api/search?q={}",
        urlencode(&format!("{title} {artist}")),
    );
    let results: Value = http
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("lrclib: {e}"))?
        .json()
        .await
        .map_err(|e| format!("lrclib json: {e}"))?;
    let first = results
        .as_array()
        .and_then(|items| items.first())
        .ok_or("no lyrics found")?;
    Ok(from_record(first))
}

fn urlencode(text: &str) -> String {
    let mut out = String::with_capacity(text.len() * 2);
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}
