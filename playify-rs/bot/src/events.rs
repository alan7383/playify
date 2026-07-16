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
    async fn cache_ready(&self, ctx: serenity::Context, _guilds: Vec<serenity::GuildId>) {
        // Wire background access (controller posts, persistence) and
        // restore the pre-restart playback state.
        if let Some(manager) = songbird::get(&ctx).await {
            let _ = self.players.manager.set(manager);
        }
        let _ = self.players.discord_http.set(ctx.http.clone());
        let _ = self.players.discord_cache.set(ctx.cache.clone());

        static RESTORED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        if !RESTORED.swap(true, std::sync::atomic::Ordering::SeqCst) {
            crate::persist::restore(&self.players).await;
            let players = self.players.clone();
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                    crate::persist::save(&players).await;
                }
            });
        }
    }

    async fn interaction_create(&self, ctx: serenity::Context, interaction: serenity::Interaction) {
        let Some(component) = interaction.as_message_component() else { return };
        let custom_id = component.data.custom_id.as_str();
        if !custom_id.starts_with("v3ctl_") {
            return;
        }
        let Some(guild_id) = component.guild_id else { return };
        let _ = component
            .create_response(&ctx.http, serenity::CreateInteractionResponse::Acknowledge)
            .await;

        let Some(manager) = songbird::get(&ctx).await else { return };
        let players = self.players.clone();
        let player_ref = players.get(guild_id.get()).await;

        match custom_id {
            "v3ctl_pause" => {
                let mut player = player_ref.lock().await;
                if let Some(handle) = &player.handle {
                    if player.paused {
                        let _ = handle.play();
                        player.paused = false;
                        player.started_at = Some(std::time::Instant::now());
                    } else {
                        let _ = handle.pause();
                        let position = player.position();
                        player.paused = true;
                        player.start_offset = position;
                        player.started_at = None;
                    }
                }
            }
            "v3ctl_skip" => {
                let handle = {
                    let mut player = player_ref.lock().await;
                    player.loop_current = false;
                    player.handle.take()
                };
                if let Some(handle) = handle {
                    let _ = handle.stop();
                }
            }
            "v3ctl_stop" => {
                let controller = {
                    let mut player = player_ref.lock().await;
                    player.queue.clear();
                    player.epoch += 1;
                    if let Some(handle) = player.handle.take() {
                        let _ = handle.stop();
                    }
                    player.controller.take()
                };
                if let Some((channel, message)) = controller {
                    let _ = serenity::ChannelId::new(channel)
                        .delete_message(&ctx.http, serenity::MessageId::new(message))
                        .await;
                }
                players.remove(guild_id.get()).await;
                let _ = manager.remove(guild_id).await;
            }
            "v3ctl_loop" => {
                let mut player = player_ref.lock().await;
                player.loop_current = !player.loop_current;
            }
            "v3ctl_shuffle" => {
                use rand::seq::SliceRandom;
                let mut player = player_ref.lock().await;
                let mut tracks: Vec<_> = player.queue.drain(..).collect();
                tracks.shuffle(&mut rand::thread_rng());
                player.queue.extend(tracks);
            }
            _ => {}
        }
    }

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
