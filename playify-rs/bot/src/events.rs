//! Voice-state events: auto-pause when the bot is alone, resume when a
//! human comes back, idle disconnect (unless 24/7), and 24/7 auto-reconnect
//! after an unexpected disconnect.

use poise::serenity_prelude as serenity;
use serenity::prelude::*;
use tracing::info;

use crate::player::Players;

pub struct VoiceEvents {
    pub players: Players,
}

fn humans_in_channel(
    ctx: &serenity::Context,
    guild_id: serenity::GuildId,
    channel_id: serenity::ChannelId,
) -> usize {
    ctx.cache
        .guild(guild_id)
        .map(|guild| {
            guild
                .voice_states
                .values()
                .filter(|vs| {
                    vs.channel_id == Some(channel_id)
                        && vs
                            .user_id
                            .to_user_cached(&ctx.cache)
                            .map(|u| !u.bot)
                            .unwrap_or(true)
                })
                .count()
        })
        .unwrap_or(0)
}

#[serenity::async_trait]
impl EventHandler for VoiceEvents {
    async fn voice_state_update(
        &self,
        ctx: serenity::Context,
        _old: Option<serenity::VoiceState>,
        new: serenity::VoiceState,
    ) {
        let Some(guild_id) = new.guild_id else { return };
        let Some(manager) = songbird::get(&ctx).await else { return };
        let Some(call) = manager.get(guild_id) else { return };
        let Some(bot_channel) = call.lock().await.current_channel() else {
            return;
        };
        let bot_channel = serenity::ChannelId::new(bot_channel.0.get());

        let humans = humans_in_channel(&ctx, guild_id, bot_channel);
        let players = self.players.clone();
        let player_ref = players.get(guild_id.get()).await;

        if humans == 0 {
            // Alone: pause playback; leave after 60 s unless in 24/7 mode.
            {
                let mut player = player_ref.lock().await;
                if !player.paused {
                    if let Some(handle) = &player.handle {
                        let _ = handle.pause();
                        let position = player.position();
                        player.paused = true;
                        player.start_offset = position;
                        player.started_at = None;
                        info!(guild = guild_id.get(), "alone: paused playback");
                    }
                }
            }
            let mode = players.settings.get(guild_id.get()).mode_24_7;
            if mode == "off" || mode.is_empty() {
                let ctx = ctx.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                    if humans_in_channel(&ctx, guild_id, bot_channel) == 0 {
                        if let Some(manager) = songbird::get(&ctx).await {
                            if manager.get(guild_id).is_some() {
                                info!(guild = guild_id.get(), "alone for 60s: leaving");
                                players.remove(guild_id.get()).await;
                                let _ = manager.remove(guild_id).await;
                            }
                        }
                    }
                });
            }
        } else {
            // A human is (back) in the channel: resume if we auto-paused.
            let mut player = player_ref.lock().await;
            if player.paused {
                if let Some(handle) = &player.handle {
                    let _ = handle.play();
                    player.paused = false;
                    player.started_at = Some(std::time::Instant::now());
                    info!(guild = guild_id.get(), "human joined: resumed playback");
                }
            }
        }
    }
}
