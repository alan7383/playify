//! Live self-test: exercises the whole v3 audio path against a real
//! Discord server without registering slash commands.
//!
//! Scenario: log in, join the voice channel that has a human in it,
//! resolve a YouTube URL through the yt-dlp subprocess, play it through
//! the native DSP pipeline (nightcore), verify sustained playback and
//! position, then leave and exit with a status code.

use std::sync::Arc;

use poise::serenity_prelude as serenity;
use serenity::prelude::*;
use songbird::SerenityInit;
use tracing::info;

use crate::player::{self, Players};
use crate::ytdlp;

const TEST_URL: &str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

struct SelfTest {
    players: Players,
}

#[serenity::async_trait]
impl EventHandler for SelfTest {
    // cache_ready fires once the GUILD_CREATE burst has been processed;
    // plain ready() runs before guilds (and voice states) are cached.
    async fn cache_ready(&self, ctx: serenity::Context, _guilds: Vec<serenity::GuildId>) {
        info!("selftest: cache ready");
        let result = run_scenario(&ctx, &self.players).await;
        match result {
            Ok(()) => {
                println!("SELFTEST: ALL PASS");
                std::process::exit(0);
            }
            Err(e) => {
                println!("SELFTEST: FAILED: {e}");
                std::process::exit(1);
            }
        }
    }
}

async fn run_scenario(ctx: &serenity::Context, players: &Players) -> Result<(), String> {
    let manager = songbird::get(ctx).await.ok_or("no songbird manager")?;

    // Pick the first guild and prefer a voice channel with a human in it.
    let guild_id = ctx
        .cache
        .guilds()
        .first()
        .copied()
        .ok_or("bot is in no guild")?;
    let (channel_id, channel_name) = {
        let guild = ctx.cache.guild(guild_id).ok_or("guild not cached")?;
        let mut chosen = None;
        for (channel_id, channel) in &guild.channels {
            if channel.kind != serenity::ChannelType::Voice {
                continue;
            }
            let humans = guild
                .voice_states
                .values()
                .filter(|vs| vs.channel_id == Some(*channel_id))
                .count();
            if humans > 0 {
                chosen = Some((*channel_id, channel.name.clone()));
                break;
            }
            if chosen.is_none() {
                chosen = Some((*channel_id, channel.name.clone()));
            }
        }
        chosen.ok_or("no voice channel")?
    };
    println!("SELFTEST: joining '{channel_name}'");

    let started = std::time::Instant::now();
    manager
        .join(guild_id, channel_id)
        .await
        .map_err(|e| format!("voice join failed: {e}"))?;
    println!(
        "SELFTEST: voice connected (DAVE) in {:.2}s",
        started.elapsed().as_secs_f64()
    );

    // Resolve through the yt-dlp subprocess.
    let started = std::time::Instant::now();
    let tracks = ytdlp::resolve(TEST_URL).await?;
    let track = tracks.into_iter().next().ok_or("no track resolved")?;
    println!(
        "SELFTEST: resolved '{}' in {:.1}s",
        track.title,
        started.elapsed().as_secs_f64()
    );

    // Play with the nightcore filter -> native DSP pipeline, no FFmpeg.
    {
        let player_ref = players.get(guild_id.get()).await;
        player_ref
            .lock()
            .await
            .filters
            .insert("nightcore".to_string());
    }
    player::start_track(&manager, players, guild_id.get(), track, 0.0).await?;
    println!("SELFTEST: playing with nightcore (native DSP)");

    tokio::time::sleep(std::time::Duration::from_secs(8)).await;
    let player_ref = players.get(guild_id.get()).await;
    let (position, still_playing) = {
        let player = player_ref.lock().await;
        (player.position(), player.handle.is_some())
    };
    println!("SELFTEST: after 8s, position={position:.1}s, playing={still_playing}");
    if !still_playing || position < 5.0 {
        return Err("playback did not sustain".into());
    }

    if let Some(usage) = memory_stats::memory_stats() {
        println!(
            "SELFTEST: total process RSS: {:.0} MB (gateway + voice + DSP, no Python)",
            usage.physical_mem as f64 / 1048576.0
        );
    }

    let manager_guild = songbird::id::GuildId::from(guild_id);
    let _ = manager_guild; // (join/remove take serenity ids directly)
    manager
        .remove(guild_id)
        .await
        .map_err(|e| format!("leave failed: {e}"))?;
    println!("SELFTEST: left voice channel cleanly");
    Ok(())
}

pub async fn run(token: String) {
    let intents = serenity::GatewayIntents::GUILDS | serenity::GatewayIntents::GUILD_VOICE_STATES;
    let players = Players::new(reqwest::Client::new(), crate::settings::Settings::load());

    let mut client = serenity::ClientBuilder::new(&token, intents)
        .event_handler(SelfTest { players })
        .register_songbird()
        .await
        .expect("client build failed");

    let _ = client.start().await;
}

// serenity::async_trait is a re-export; silence unused warning when the
// macro resolves differently across versions.
use std::marker::PhantomData as _PhantomData;
type _Unused = _PhantomData<Arc<()>>;
