//! Playify v3 — the full-Rust Discord music bot (experimental).
//!
//! Everything lives in one native process: serenity gateway, poise slash
//! commands, songbird voice (DAVE/E2EE) and the playify-audio DSP engine.
//! yt-dlp runs as a short-lived subprocess per resolution, so there is no
//! resident Python at all.
//!
//! Run with `--selftest` to exercise the full audio path against a real
//! server (join, resolve, play with nightcore, position check, leave)
//! without registering any commands.

mod commands;
mod events;
mod i18n;
mod lyrics;
mod persist;
mod platforms;
mod player;
mod selftest;
mod settings;
mod tui;
mod ytdlp;

use std::sync::OnceLock;

use poise::serenity_prelude as serenity;
use songbird::SerenityInit;
use tracing::{info, warn};

/// Process start time, for /status uptime.
pub static START: OnceLock<std::time::Instant> = OnceLock::new();

pub struct Data {
    pub players: player::Players,
}

pub type Error = Box<dyn std::error::Error + Send + Sync>;
pub type Context<'a> = poise::Context<'a, Data, Error>;

fn kawaii_reply(ctx: Context<'_>, mut reply: poise::CreateReply) -> poise::CreateReply {
    let Some(guild) = ctx.guild_id() else { return reply };
    if !ctx.data().players.settings.get(guild.get()).kawaii {
        return reply;
    }
    if let Some(content) = &mut reply.content {
        if !content.is_empty() && !content.contains('◕') && !content.contains('ヮ') {
            const KAOMOJI: &[&str] =
                &["(◕‿◕)♪", "☆(≧▽≦)☆", "(ﾉ´ヮ`)ﾉ*:･ﾟ✧", "♪(´▽｀)", "(=^･ω･^=)♪"];
            let pick = KAOMOJI[content.len() % KAOMOJI.len()];
            content.push_str(&format!(" {pick}"));
        }
    }
    reply
}

fn load_env() {
    // Works from the workspace dir, the repo root, or next to the binary.
    for path in [".env", "../.env", "../../.env"] {
        if dotenvy::from_filename(path).is_ok() {
            return;
        }
    }
}

#[tokio::main]
async fn main() {
    let tui_mode = std::env::args().any(|arg| arg == "--tui");
    let log_buffer: tui::LogBuffer = Default::default();

    let env_filter = || {
        tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "info,serenity=warn,songbird=warn".into())
    };
    if tui_mode {
        // Logs feed the dashboard's panel instead of stdout.
        tracing_subscriber::fmt()
            .with_env_filter(env_filter())
            .with_ansi(false)
            .without_time()
            .with_writer(tui::LogWriter(log_buffer.clone()))
            .init();
    } else {
        tracing_subscriber::fmt().with_env_filter(env_filter()).init();
    }
    load_env();

    let token = std::env::var("DISCORD_TOKEN").expect("DISCORD_TOKEN missing (.env)");

    if std::env::args().any(|arg| arg == "--selftest") {
        selftest::run(token).await;
        return;
    }

    // Offline platform-resolution check: --resolve <url>
    if let Some(position) = std::env::args().position(|arg| arg == "--resolve") {
        let url = std::env::args().nth(position + 1).expect("--resolve <url>");
        let http = reqwest::Client::new();
        match platforms::expand(&http, &url).await {
            Some(Ok(tracks)) => {
                println!("RESOLVE OK: {} track(s)", tracks.len());
                for track in tracks.iter().take(5) {
                    println!("  - {} => {}", track.title, track.webpage_url);
                }
            }
            Some(Err(e)) => println!("RESOLVE FAILED: {e}"),
            None => match ytdlp::resolve(&url).await {
                Ok(tracks) => println!("RESOLVE OK (yt-dlp): {} track(s)", tracks.len()),
                Err(e) => println!("RESOLVE FAILED (yt-dlp): {e}"),
            },
        }
        return;
    }

    let _ = START.set(std::time::Instant::now());
    let intents = serenity::GatewayIntents::GUILDS | serenity::GatewayIntents::GUILD_VOICE_STATES;
    let settings = settings::Settings::load();
    let players = player::Players::new(reqwest::Client::new(), settings);
    let players_for_events = players.clone();

    let framework = poise::Framework::builder()
        .options(poise::FrameworkOptions {
            commands: commands::all(),
            // Full-coverage kawaii locale: every reply from every command
            // passes through here, so /kaomoji affects all of them (v2's
            // i18n has exactly two locales: en-US and en-x-kawaii).
            reply_callback: Some(kawaii_reply),
            // Channel allowlist, mirroring v2's admin allowlist.
            command_check: Some(|ctx: Context<'_>| {
                Box::pin(async move {
                    let Some(guild) = ctx.guild_id() else { return Ok(true) };
                    let allowed = ctx
                        .data()
                        .players
                        .settings
                        .get(guild.get())
                        .allowed_channels;
                    if allowed.is_empty()
                        || ctx.command().name == "allowlist"
                        || allowed.contains(&ctx.channel_id().get())
                    {
                        Ok(true)
                    } else {
                        let _ = ctx
                            .send(
                                poise::CreateReply::default()
                                    .content("❌ Playify commands are not allowed in this channel.")
                                    .ephemeral(true),
                            )
                            .await;
                        Ok(false)
                    }
                })
            }),
            on_error: |error| {
                Box::pin(async move {
                    match error {
                        poise::FrameworkError::Command { error, ctx, .. } => {
                            warn!("command error: {error}");
                            let _ = ctx.say(format!("❌ {error}")).await;
                        }
                        other => {
                            if let Err(e) = poise::builtins::on_error(other).await {
                                warn!("error handler failed: {e}");
                            }
                        }
                    }
                })
            },
            ..Default::default()
        })
        .setup(move |ctx, ready, framework| {
            Box::pin(async move {
                // Guild-scoped registration is instant (global takes ~1h).
                for guild in &ready.guilds {
                    poise::builtins::register_in_guild(
                        ctx,
                        &framework.options().commands,
                        guild.id,
                    )
                    .await?;
                }
                info!(
                    "Playify v3 online as {} ({} guilds, {} commands)",
                    ready.user.name,
                    ready.guilds.len(),
                    framework.options().commands.len(),
                );
                if let Some(usage) = memory_stats::memory_stats() {
                    info!("RSS at ready: {:.0} MB", usage.physical_mem as f64 / 1048576.0);
                }
                Ok(Data { players })
            })
        })
        .build();

    let players_for_shutdown = players_for_events.clone();
    let mut client = serenity::ClientBuilder::new(&token, intents)
        .framework(framework)
        .event_handler(events::VoiceEvents {
            players: players_for_events,
        })
        .register_songbird()
        .await
        .expect("client build failed");

    // Optional terminal dashboard; quitting it shuts the bot down cleanly.
    let tui_done: std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>> = if tui_mode {
        let status: tui::StatusRef = Default::default();
        let quit = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        tokio::spawn(tui::status_updater(
            players_for_shutdown.clone(),
            status.clone(),
        ));
        let handle = tokio::task::spawn_blocking(move || {
            tui::run_blocking(log_buffer, status, quit);
        });
        Box::pin(async move {
            let _ = handle.await;
        })
    } else {
        Box::pin(std::future::pending())
    };

    // Save playback state on Ctrl-C / TUI quit so the next boot resumes.
    tokio::select! {
        result = client.start() => {
            if let Err(e) = result {
                eprintln!("client error: {e}");
            }
        }
        _ = tokio::signal::ctrl_c() => {
            info!("shutting down: saving playback state");
            persist::save(&players_for_shutdown).await;
        }
        _ = tui_done => {
            persist::save(&players_for_shutdown).await;
        }
    }
}
