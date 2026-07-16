//! Per-guild playback state and the track-advance loop.

use std::{
    collections::{BTreeSet, HashMap, VecDeque},
    sync::Arc,
    time::Instant,
};

use async_trait::async_trait;
use playify_audio::{
    dsp::{effective_speed, FilterChain},
    pipeline,
};
use poise::serenity_prelude as serenity;
use songbird::{
    events::{Event, EventContext, EventHandler, TrackEvent},
    input::{HlsRequest, HttpRequest, Input},
    tracks::Track,
    Songbird,
};
use std::sync::OnceLock;
use tracing::{info, warn};

use crate::ytdlp::{self, Resolved};

pub const FILTERS: &[&str] = &[
    "slowed", "spedup", "nightcore", "reverb", "8d", "muffled", "bassboost", "earrape",
];

#[derive(Default)]
pub struct GuildPlayer {
    pub queue: VecDeque<Resolved>,
    pub current: Option<Resolved>,
    pub handle: Option<songbird::tracks::TrackHandle>,
    pub volume: f32,
    pub paused: bool,
    pub loop_current: bool,
    pub autoplay: bool,
    pub filters: BTreeSet<String>,
    /// Recently finished tracks (autoplay seed, /previous).
    pub history: Vec<Resolved>,
    /// Seek offset the current playback started from.
    pub start_offset: f64,
    pub started_at: Option<Instant>,
    /// Bumped on deliberate replacements (seek/filter restart) so the
    /// replaced track's End event does not advance the queue.
    pub epoch: u64,
    /// Channel of the last music command; the controller panel lives there.
    pub text_channel: Option<u64>,
    /// (channel_id, message_id) of the posted controller panel.
    pub controller: Option<(u64, u64)>,
}

impl GuildPlayer {
    fn new() -> Self {
        GuildPlayer {
            volume: 1.0,
            ..Default::default()
        }
    }

    /// Current position in the song, accounting for filter speed.
    pub fn position(&self) -> f64 {
        let speed = effective_speed(&self.filter_names());
        match self.started_at {
            Some(started) if !self.paused => {
                self.start_offset + started.elapsed().as_secs_f64() * speed
            }
            _ => self.start_offset,
        }
    }

    pub fn filter_names(&self) -> Vec<String> {
        self.filters.iter().cloned().collect()
    }
}

pub type PlayerRef = Arc<tokio::sync::Mutex<GuildPlayer>>;

#[derive(Clone)]
pub struct Players {
    inner: Arc<tokio::sync::Mutex<HashMap<u64, PlayerRef>>>,
    pub http: reqwest::Client,
    pub settings: crate::settings::Settings,
    /// Set once at startup; lets background tasks reach Discord and voice.
    pub manager: Arc<OnceLock<Arc<Songbird>>>,
    pub discord_http: Arc<OnceLock<Arc<serenity::Http>>>,
}

impl Players {
    pub fn new(http: reqwest::Client, settings: crate::settings::Settings) -> Self {
        Players {
            inner: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
            http,
            settings,
            manager: Arc::new(OnceLock::new()),
            discord_http: Arc::new(OnceLock::new()),
        }
    }

    pub async fn snapshot(&self) -> Vec<(u64, PlayerRef)> {
        self.inner
            .lock()
            .await
            .iter()
            .map(|(id, player)| (*id, player.clone()))
            .collect()
    }

    /// Number of guilds with an active track (for /status).
    pub async fn active_count(&self) -> usize {
        let refs: Vec<PlayerRef> = self.inner.lock().await.values().cloned().collect();
        let mut count = 0;
        for player_ref in refs {
            if player_ref.lock().await.current.is_some() {
                count += 1;
            }
        }
        count
    }

    pub async fn get(&self, guild_id: u64) -> PlayerRef {
        self.inner
            .lock()
            .await
            .entry(guild_id)
            .or_insert_with(|| {
                let mut player = GuildPlayer::new();
                // Per-server default volume (persisted, like v2's /defaultvolume).
                if let Some(volume) = self.settings.get(guild_id).default_volume {
                    player.volume = volume;
                }
                Arc::new(tokio::sync::Mutex::new(player))
            })
            .clone()
    }

    pub async fn remove(&self, guild_id: u64) {
        self.inner.lock().await.remove(&guild_id);
    }
}

/// Builds the input for a track: HLS for live streams, the native DSP
/// pipeline when filters or a seek are active, lazy HTTP otherwise.
fn build_input(
    http: &reqwest::Client,
    stream_url: &str,
    filter_names: &[String],
    seek: f64,
    is_live: bool,
) -> Result<Input, String> {
    if is_live {
        // Live = HLS segments; no seeking, filters unsupported for now.
        return Ok(HlsRequest::new(http.clone(), stream_url.to_string()).into());
    }
    if !filter_names.is_empty() || seek > 0.0 {
        let chain = FilterChain::from_names(filter_names)
            .ok_or_else(|| format!("unknown filter in {filter_names:?}"))?;
        pipeline::build_dsp_input("http", stream_url, chain, seek)
    } else {
        Ok(HttpRequest::new(http.clone(), stream_url.to_string()).into())
    }
}

/// Posts (or replaces) the controller panel: a now-playing embed with
/// pause/skip/stop/loop/shuffle buttons, in the last command channel.
async fn update_controller(players: &Players, guild_id: u64, track: &Resolved) {
    let Some(http) = players.discord_http.get().cloned() else { return };
    let player_ref = players.get(guild_id).await;
    let pinned = players.settings.get(guild_id).controller_channel;
    let (channel, old_controller, volume, filters) = {
        let player = player_ref.lock().await;
        let Some(channel) = pinned.or(player.text_channel) else { return };
        (
            channel,
            player.controller,
            player.volume,
            player.filter_names(),
        )
    };

    if let Some((old_channel, old_message)) = old_controller {
        let _ = serenity::ChannelId::new(old_channel)
            .delete_message(&http, serenity::MessageId::new(old_message))
            .await;
    }

    let mut description = format!("**{}**", track.title);
    if track.duration > 0.0 {
        description.push_str(&format!(" `({}:{:02})`", track.duration as u64 / 60, track.duration as u64 % 60));
    }
    description.push_str(&format!("\n🔊 {:.0}%", volume * 100.0));
    if !filters.is_empty() {
        description.push_str(&format!(" · 🎛 {}", filters.join(", ")));
    }
    let mut embed = serenity::CreateEmbed::new()
        .title("🎵 Now Playing")
        .description(description)
        .color(0x1B3A5C);
    if let Some(thumbnail) = &track.thumbnail {
        embed = embed.thumbnail(thumbnail.clone());
    }

    let buttons = serenity::CreateActionRow::Buttons(vec![
        serenity::CreateButton::new("v3ctl_pause")
            .emoji('⏯')
            .style(serenity::ButtonStyle::Secondary),
        serenity::CreateButton::new("v3ctl_skip")
            .emoji('⏭')
            .style(serenity::ButtonStyle::Primary),
        serenity::CreateButton::new("v3ctl_stop")
            .emoji('⏹')
            .style(serenity::ButtonStyle::Danger),
        serenity::CreateButton::new("v3ctl_loop")
            .emoji('🔁')
            .style(serenity::ButtonStyle::Secondary),
        serenity::CreateButton::new("v3ctl_shuffle")
            .emoji('🔀')
            .style(serenity::ButtonStyle::Secondary),
    ]);

    let message = serenity::ChannelId::new(channel)
        .send_message(
            &http,
            serenity::CreateMessage::new().embed(embed).components(vec![buttons]),
        )
        .await;
    if let Ok(message) = message {
        player_ref.lock().await.controller = Some((channel, message.id.get()));
    }
}

/// Starts (or restarts, when seeking) playback of a resolved track.
pub async fn start_track(
    manager: &Arc<Songbird>,
    players: &Players,
    guild_id: u64,
    track: Resolved,
    seek: f64,
) -> Result<(), String> {
    // Fresh stream URL: extractor URLs expire, direct links pass through.
    let stream_url = match &track.stream_url {
        Some(url) if track.webpage_url == *url => url.clone(),
        _ => {
            ytdlp::fresh_stream_url(&track.webpage_url)
                .await?
                .stream_url
                .ok_or("no stream url after resolution")?
        }
    };

    let call = manager
        .get(songbird::id::GuildId::from(
            std::num::NonZeroU64::new(guild_id).ok_or("bad guild id")?,
        ))
        .ok_or("not connected to a voice channel")?;

    let player_ref = players.get(guild_id).await;
    let (volume, filter_names, epoch) = {
        let mut player = player_ref.lock().await;
        player.epoch += 1;
        player.current = Some(track.clone());
        player.start_offset = seek;
        player.started_at = Some(Instant::now());
        player.paused = false;
        (player.volume, player.filter_names(), player.epoch)
    };

    let input = build_input(&players.http, &stream_url, &filter_names, seek, track.is_live)?;

    let handle = {
        let mut call_lock = call.lock().await;
        call_lock.play_only(Track::new(input).volume(volume))
    };
    let _ = handle.add_event(
        Event::Track(TrackEvent::End),
        TrackEndAdvance {
            manager: manager.clone(),
            players: players.clone(),
            guild_id,
            epoch,
        },
    );

    player_ref.lock().await.handle = Some(handle);
    info!(guild = guild_id, title = %track.title, seek, "playing");

    // Refresh the controller panel only on real track starts, not seeks.
    if seek == 0.0 {
        let players = players.clone();
        let track = track.clone();
        tokio::spawn(async move {
            update_controller(&players, guild_id, &track).await;
        });
    }
    Ok(())
}

/// Pops and plays the next queue entry, honoring loop mode, 24/7 radio
/// requeueing and autoplay. Returns the started track, if any.
pub async fn play_next(
    manager: &Arc<Songbird>,
    players: &Players,
    guild_id: u64,
) -> Option<Resolved> {
    let mode_24_7 = players.settings.get(guild_id).mode_24_7;
    let player_ref = players.get(guild_id).await;

    let next = {
        let mut player = player_ref.lock().await;
        if player.loop_current {
            player.current.clone()
        } else {
            // Archive the finished track: /previous + autoplay seed, and
            // 24/7 "normal" mode requeues it at the back (radio behavior).
            if let Some(finished) = player.current.take() {
                player.history.push(finished.clone());
                if player.history.len() > 50 {
                    player.history.remove(0);
                }
                if mode_24_7 == "normal" && !player.autoplay {
                    player.queue.push_back(finished);
                }
            }
            player.queue.pop_front()
        }
    };

    // Queue dried up: autoplay from the last played track's mix.
    let next = match next {
        Some(track) => Some(track),
        None => {
            let (autoplay, seed) = {
                let player = player_ref.lock().await;
                let autoplay = player.autoplay || mode_24_7 == "auto";
                let seed = player.history.last().map(|t| t.webpage_url.clone());
                (autoplay, seed)
            };
            match (autoplay, seed) {
                (true, Some(seed)) => match ytdlp::autoplay_seeds(&seed).await {
                    Ok(mix) if !mix.is_empty() => {
                        info!(guild = guild_id, "autoplay: queued {} similar tracks", mix.len());
                        let mut player = player_ref.lock().await;
                        player.queue.extend(mix);
                        player.queue.pop_front()
                    }
                    Ok(_) => None,
                    Err(e) => {
                        warn!(guild = guild_id, "autoplay failed: {e}");
                        None
                    }
                },
                _ => None,
            }
        }
    };

    let Some(track) = next else {
        let mut player = player_ref.lock().await;
        player.handle = None;
        player.started_at = None;
        return None;
    };
    match start_track(manager, players, guild_id, track.clone(), 0.0).await {
        Ok(()) => Some(track),
        Err(e) => {
            warn!(guild = guild_id, "cannot play '{}': {e}, skipping", track.title);
            // Skip broken entries rather than stalling the queue.
            Box::pin(play_next(manager, players, guild_id)).await
        }
    }
}

struct TrackEndAdvance {
    manager: Arc<Songbird>,
    players: Players,
    guild_id: u64,
    epoch: u64,
}

#[async_trait]
impl EventHandler for TrackEndAdvance {
    async fn act(&self, _ctx: &EventContext<'_>) -> Option<Event> {
        {
            let player_ref = self.players.get(self.guild_id).await;
            let player = player_ref.lock().await;
            if player.epoch != self.epoch {
                return None; // deliberately replaced (seek/filter change)
            }
        }
        play_next(&self.manager, &self.players, self.guild_id).await;
        None
    }
}
