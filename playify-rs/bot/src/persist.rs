//! Queue persistence: playback state survives bot restarts, like v2's
//! SQLite playback_state (JSON here). Saved every 60 s and on Ctrl-C;
//! restored once the gateway cache is ready.

use std::{collections::HashMap, path::PathBuf};

use poise::serenity_prelude as serenity;
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use crate::player::{self, Players};
use crate::ytdlp::Resolved;

#[derive(Serialize, Deserialize)]
struct GuildState {
    voice_channel: u64,
    text_channel: Option<u64>,
    current: Option<Resolved>,
    position: f64,
    queue: Vec<Resolved>,
    volume: f32,
    filters: Vec<String>,
    loop_current: bool,
    autoplay: bool,
}

fn state_path() -> PathBuf {
    ["../data", "data"]
        .iter()
        .map(PathBuf::from)
        .find(|dir| dir.is_dir())
        .unwrap_or_else(|| PathBuf::from("data"))
        .join("v3_state.json")
}

pub async fn save(players: &Players) {
    let Some(manager) = players.manager.get() else { return };
    let mut states: HashMap<u64, GuildState> = HashMap::new();

    for (guild_id, player_ref) in players.snapshot().await {
        let Some(call) = manager.get(serenity::GuildId::new(guild_id)) else {
            continue;
        };
        let Some(channel) = call.lock().await.current_channel() else {
            continue;
        };
        let player = player_ref.lock().await;
        if player.current.is_none() && player.queue.is_empty() {
            continue;
        }
        states.insert(
            guild_id,
            GuildState {
                voice_channel: channel.0.get(),
                text_channel: player.text_channel,
                current: player.current.clone(),
                position: player.position(),
                queue: player.queue.iter().cloned().collect(),
                volume: player.volume,
                filters: player.filter_names(),
                loop_current: player.loop_current,
                autoplay: player.autoplay,
            },
        );
    }

    let path = state_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match serde_json::to_string(&states) {
        Ok(json) => {
            if let Err(e) = std::fs::write(&path, json) {
                warn!("state save failed: {e}");
            }
        }
        Err(e) => warn!("state serialize failed: {e}"),
    }
}

pub async fn restore(players: &Players) {
    let Some(manager) = players.manager.get() else { return };
    let Ok(text) = std::fs::read_to_string(state_path()) else {
        return;
    };
    let Ok(states) = serde_json::from_str::<HashMap<u64, GuildState>>(&text) else {
        return;
    };
    let _ = std::fs::remove_file(state_path()); // consumed: no double-restore

    for (guild_id, state) in states {
        info!(guild = guild_id, "restoring playback state");
        if manager
            .join(
                serenity::GuildId::new(guild_id),
                serenity::ChannelId::new(state.voice_channel),
            )
            .await
            .is_err()
        {
            warn!(guild = guild_id, "restore: cannot rejoin voice channel");
            continue;
        }
        let player_ref = players.get(guild_id).await;
        {
            let mut player = player_ref.lock().await;
            player.queue = state.queue.into();
            player.volume = state.volume;
            player.filters = state.filters.into_iter().collect();
            player.loop_current = state.loop_current;
            player.autoplay = state.autoplay;
            player.text_channel = state.text_channel;
        }
        match state.current {
            Some(track) => {
                let seek = if track.is_live { 0.0 } else { state.position };
                if let Err(e) =
                    player::start_track(manager, players, guild_id, track, seek).await
                {
                    warn!(guild = guild_id, "restore: start failed: {e}");
                    player::play_next(manager, players, guild_id).await;
                }
            }
            None => {
                player::play_next(manager, players, guild_id).await;
            }
        }
    }
}
