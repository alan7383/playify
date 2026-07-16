//! Slash commands for Playify v3.

use poise::serenity_prelude as serenity;
use rand::seq::SliceRandom;

use crate::player::{self, FILTERS};
use crate::{lyrics, platforms, ytdlp};
use crate::{Context, Error};

/// Appends the kawaii suffix when /kaomoji mode is on for the guild.
fn kawaii(ctx: &Context<'_>, base: String) -> String {
    let guild_id = ctx.guild_id().map(|g| g.get()).unwrap_or(0);
    if ctx.data().players.settings.get(guild_id).kawaii {
        format!("{base} (◕‿◕)♪")
    } else {
        base
    }
}

fn format_duration(seconds: f64) -> String {
    let total = seconds.max(0.0) as u64;
    if total >= 3600 {
        format!("{}:{:02}:{:02}", total / 3600, (total % 3600) / 60, total % 60)
    } else {
        format!("{}:{:02}", total / 60, total % 60)
    }
}

/// Joins the author's voice channel; returns the guild id. Also records
/// the command channel as home for the controller panel.
async fn ensure_voice(ctx: &Context<'_>) -> Result<u64, Error> {
    let kawaii_mode = ctx
        .guild_id()
        .map(|g| ctx.data().players.settings.get(g.get()).kawaii)
        .unwrap_or(false);
    let (guild_id, channel_id) = {
        let guild = ctx.guild().ok_or("This command only works in a server.")?;
        let channel = guild
            .voice_states
            .get(&ctx.author().id)
            .and_then(|vs| vs.channel_id)
            .ok_or_else(|| crate::i18n::t(kawaii_mode, "error.no_voice_channel"))?;
        (guild.id, channel)
    };

    let manager = songbird::get(ctx.serenity_context())
        .await
        .ok_or("voice manager missing")?;
    if manager.get(guild_id).is_none() {
        manager
            .join(guild_id, channel_id)
            .await
            .map_err(|_| crate::i18n::t(kawaii_mode, "error.connection"))?;
    }

    let player_ref = ctx.data().players.get(guild_id.get()).await;
    player_ref.lock().await.text_channel = Some(ctx.channel_id().get());
    Ok(guild_id.get())
}

/// Play a song or playlist from a URL or search terms.
#[poise::command(slash_command, guild_only)]
pub async fn play(
    ctx: Context<'_>,
    #[description = "URL (YouTube, SoundCloud, direct audio...) or search terms"] query: String,
) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ensure_voice(&ctx).await?;
    let data = ctx.data();

    // Spotify / Deezer / Apple Music / Tidal / Amazon Music resolve to
    // metadata first, then play from YouTube. Everything else is yt-dlp.
    let tracks = match platforms::expand(&data.players.http, &query).await {
        Some(result) => result.map_err(Error::from)?,
        None => ytdlp::resolve(&query).await.map_err(Error::from)?,
    };
    let count = tracks.len();
    let first_title = tracks.first().map(|t| t.title.clone()).unwrap_or_default();

    let player_ref = data.players.get(guild_id).await;
    let was_idle = {
        let mut player = player_ref.lock().await;
        let was_idle = player.current.is_none();
        player.queue.extend(tracks);
        was_idle
    };

    let manager = songbird::get(ctx.serenity_context()).await.unwrap();
    if was_idle {
        match player::play_next(&manager, &data.players, guild_id).await {
            Some(track) => {
                ctx.say(format!("▶ Now playing: **{}**", track.title)).await?;
            }
            None => {
                ctx.say("Nothing could be played from that query.").await?;
            }
        }
    } else if count > 1 {
        ctx.say(format!("➕ Queued **{count}** tracks.")).await?;
    } else {
        ctx.say(format!("➕ Queued: **{first_title}**")).await?;
    }
    Ok(())
}

/// Skip the current track, or jump to a queue position.
#[poise::command(slash_command, guild_only)]
pub async fn skip(
    ctx: Context<'_>,
    #[description = "Queue position to jump to (skips everything before it)"]
    #[min = 1]
    to: Option<usize>,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let handle = {
        let mut player = player_ref.lock().await;
        player.loop_current = false;
        if let Some(position) = to {
            let drop_count = position.saturating_sub(1).min(player.queue.len());
            player.queue.drain(..drop_count);
        }
        player.handle.take()
    };
    match handle {
        Some(handle) => {
            let _ = handle.stop(); // End event advances the queue
            ctx.say(kawaii(&ctx, "⏭ Skipped.".into())).await?;
        }
        None => {
            ctx.say("Nothing is playing.").await?;
        }
    }
    Ok(())
}

/// Jump to a specific track number in the queue.
#[poise::command(slash_command, guild_only)]
pub async fn jumpto(
    ctx: Context<'_>,
    #[description = "Queue position to jump to"]
    #[min = 1]
    position: usize,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let handle = {
        let mut player = player_ref.lock().await;
        player.loop_current = false;
        let drop_count = position.saturating_sub(1).min(player.queue.len());
        player.queue.drain(..drop_count);
        player.handle.take()
    };
    match handle {
        Some(handle) => {
            let _ = handle.stop();
            ctx.say(format!("⤵ Jumping to track {position}.")).await?;
        }
        None => {
            ctx.say("Nothing is playing.").await?;
        }
    }
    Ok(())
}

/// Replay the previously played track.
#[poise::command(slash_command, guild_only)]
pub async fn previous(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let handle = {
        let mut player = player_ref.lock().await;
        let Some(last) = player.history.pop() else {
            drop(player);
            ctx.say("No previous track.").await?;
            return Ok(());
        };
        // Requeue current after the previous one so nothing is lost.
        if let Some(current) = player.current.take() {
            player.queue.push_front(current);
        }
        player.queue.push_front(last);
        player.loop_current = false;
        player.handle.take()
    };
    match handle {
        Some(handle) => {
            let _ = handle.stop(); // End advances into the previous track
        }
        None => {
            let manager = songbird::get(ctx.serenity_context()).await.unwrap();
            player::play_next(&manager, &ctx.data().players, guild_id).await;
        }
    }
    ctx.say("⏮ Playing the previous track.").await?;
    Ok(())
}

/// Stop playback, clear the queue and disconnect (like v2's /stop).
#[poise::command(slash_command, guild_only)]
pub async fn stop(ctx: Context<'_>) -> Result<(), Error> {
    let guild = ctx.guild_id().ok_or("server only")?;
    let guild_id = guild.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    {
        let mut player = player_ref.lock().await;
        player.queue.clear();
        player.loop_current = false;
        player.autoplay = false;
        player.current = None;
        player.epoch += 1; // orphan the End event: nothing should advance
        if let Some(handle) = player.handle.take() {
            let _ = handle.stop();
        }
        player.started_at = None;
    }
    ctx.data().players.remove(guild_id).await;
    let manager = songbird::get(ctx.serenity_context()).await.unwrap();
    if manager.get(guild).is_some() {
        let _ = manager.remove(guild).await;
    }
    ctx.say(kawaii(&ctx, "⏹ Stopped and disconnected.".into())).await?;
    Ok(())
}

/// Clear the queue without stopping the current track.
#[poise::command(slash_command, guild_only)]
pub async fn clearqueue(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let count = {
        let mut player = player_ref.lock().await;
        let count = player.queue.len();
        player.queue.clear();
        count
    };
    ctx.say(format!("🧹 Cleared {count} queued tracks.")).await?;
    Ok(())
}

/// Remove a track from the queue (by position, or pick from a menu).
#[poise::command(slash_command, guild_only)]
pub async fn remove(
    ctx: Context<'_>,
    #[description = "Queue position to remove (omit for an interactive menu)"]
    #[min = 1]
    position: Option<usize>,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;

    // Direct removal by index.
    if let Some(position) = position {
        let removed = player_ref.lock().await.queue.remove(position - 1);
        match removed {
            Some(track) => ctx.say(format!("🗑 Removed: **{}**", track.title)).await?,
            None => ctx.say("No track at that position.").await?,
        };
        return Ok(());
    }

    // Interactive select menu (up to 25 entries, Discord's limit).
    let titles: Vec<String> = {
        let player = player_ref.lock().await;
        player.queue.iter().take(25).map(|t| t.title.clone()).collect()
    };
    if titles.is_empty() {
        ctx.say("The queue is empty.").await?;
        return Ok(());
    }
    let options: Vec<serenity::CreateSelectMenuOption> = titles
        .iter()
        .enumerate()
        .map(|(index, title)| {
            let mut label = format!("{}. {title}", index + 1);
            label.truncate(95);
            serenity::CreateSelectMenuOption::new(label, index.to_string())
        })
        .collect();
    let menu = serenity::CreateSelectMenu::new(
        "v3_remove",
        serenity::CreateSelectMenuKind::String { options },
    )
    .placeholder("Pick the track to remove");
    let reply = ctx
        .send(
            poise::CreateReply::default()
                .content("🗑 Which track should be removed?")
                .components(vec![serenity::CreateActionRow::SelectMenu(menu)]),
        )
        .await?;
    let message = reply.message().await?;

    let Some(interaction) = message
        .await_component_interaction(ctx.serenity_context().shard.clone())
        .timeout(std::time::Duration::from_secs(60))
        .await
    else {
        reply
            .edit(ctx, poise::CreateReply::default().content("⏳ Expired.").components(vec![]))
            .await?;
        return Ok(());
    };
    let chosen = match &interaction.data.kind {
        serenity::ComponentInteractionDataKind::StringSelect { values } => {
            values.first().and_then(|v| v.parse::<usize>().ok())
        }
        _ => None,
    };
    interaction
        .create_response(ctx.http(), serenity::CreateInteractionResponse::Acknowledge)
        .await?;
    let text = match chosen {
        // Re-check the title at removal time: the queue may have shifted.
        Some(index) => match player_ref.lock().await.queue.remove(index) {
            Some(track) => format!("🗑 Removed: **{}**", track.title),
            None => "That track is no longer in the queue.".to_string(),
        },
        None => "Nothing removed.".to_string(),
    };
    reply
        .edit(ctx, poise::CreateReply::default().content(text).components(vec![]))
        .await?;
    Ok(())
}

/// Pause the current track.
#[poise::command(slash_command, guild_only)]
pub async fn pause(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let mut player = player_ref.lock().await;
    match &player.handle {
        Some(handle) if !player.paused => {
            let _ = handle.pause();
            let position = player.position();
            player.paused = true;
            player.start_offset = position;
            player.started_at = None;
            drop(player);
            ctx.say("⏸ Paused.").await?;
        }
        _ => {
            drop(player);
            ctx.say("Nothing to pause.").await?;
        }
    }
    Ok(())
}

/// Resume a paused track.
#[poise::command(slash_command, guild_only)]
pub async fn resume(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let mut player = player_ref.lock().await;
    match &player.handle {
        Some(handle) if player.paused => {
            let _ = handle.play();
            player.paused = false;
            player.started_at = Some(std::time::Instant::now());
            drop(player);
            ctx.say("▶ Resumed.").await?;
        }
        _ => {
            drop(player);
            ctx.say("Nothing is paused.").await?;
        }
    }
    Ok(())
}

const QUEUE_PAGE_SIZE: usize = 10;

fn queue_page_content(
    current: &Option<ytdlp::Resolved>,
    position: f64,
    tracks: &[ytdlp::Resolved],
    page: usize,
) -> String {
    let total_pages = tracks.len().div_ceil(QUEUE_PAGE_SIZE).max(1);
    let mut lines = Vec::new();
    if let Some(current) = current {
        lines.push(format!(
            "**Now:** {} `[{} / {}]`",
            current.title,
            format_duration(position),
            format_duration(current.duration),
        ));
    }
    for (index, track) in tracks
        .iter()
        .enumerate()
        .skip(page * QUEUE_PAGE_SIZE)
        .take(QUEUE_PAGE_SIZE)
    {
        lines.push(format!("`{}.` {}", index + 1, track.title));
    }
    if tracks.len() > QUEUE_PAGE_SIZE {
        lines.push(format!("*page {}/{} · {} tracks*", page + 1, total_pages, tracks.len()));
    }
    if lines.is_empty() {
        "The queue is empty.".to_string()
    } else {
        lines.join("\n")
    }
}

/// Show the queue with interactive pages.
#[poise::command(slash_command, guild_only)]
pub async fn queue(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let (current, position, tracks) = {
        let player = player_ref.lock().await;
        (
            player.current.clone(),
            player.position(),
            player.queue.iter().cloned().collect::<Vec<_>>(),
        )
    };

    let mut page = 0usize;
    let total_pages = tracks.len().div_ceil(QUEUE_PAGE_SIZE).max(1);
    let paginated = total_pages > 1;

    let components = if paginated {
        vec![serenity::CreateActionRow::Buttons(vec![
            serenity::CreateButton::new("v3q_prev").emoji('◀'),
            serenity::CreateButton::new("v3q_next").emoji('▶'),
        ])]
    } else {
        vec![]
    };
    let reply = ctx
        .send(
            poise::CreateReply::default()
                .content(queue_page_content(&current, position, &tracks, page))
                .components(components),
        )
        .await?;
    if !paginated {
        return Ok(());
    }

    let message = reply.message().await?;
    while let Some(interaction) = message
        .await_component_interaction(ctx.serenity_context().shard.clone())
        .timeout(std::time::Duration::from_secs(120))
        .await
    {
        match interaction.data.custom_id.as_str() {
            "v3q_prev" => page = page.checked_sub(1).unwrap_or(total_pages - 1),
            "v3q_next" => page = (page + 1) % total_pages,
            _ => {}
        }
        interaction
            .create_response(ctx.http(), serenity::CreateInteractionResponse::Acknowledge)
            .await?;
        reply
            .edit(
                ctx,
                poise::CreateReply::default()
                    .content(queue_page_content(&current, position, &tracks, page)),
            )
            .await?;
    }
    Ok(())
}

/// Show the current track and position.
#[poise::command(slash_command, guild_only)]
pub async fn nowplaying(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let player = player_ref.lock().await;
    let message = match &player.current {
        Some(track) => {
            let filters = if player.filters.is_empty() {
                String::new()
            } else {
                format!(" · filters: {}", player.filter_names().join(", "))
            };
            format!(
                "🎵 **{}** `[{} / {}]`{}{}",
                track.title,
                format_duration(player.position()),
                format_duration(track.duration),
                if player.paused { " ⏸" } else { "" },
                filters,
            )
        }
        None => "Nothing is playing.".to_string(),
    };
    drop(player);
    ctx.say(message).await?;
    Ok(())
}

/// Set the playback volume (0-200%).
#[poise::command(slash_command, guild_only)]
pub async fn volume(
    ctx: Context<'_>,
    #[description = "Volume percentage (0-200)"]
    #[min = 0]
    #[max = 200]
    level: u16,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let mut player = player_ref.lock().await;
    player.volume = f32::from(level) / 100.0;
    if let Some(handle) = &player.handle {
        let _ = handle.set_volume(player.volume);
    }
    drop(player);
    ctx.say(format!("🔊 Volume set to {level}%.")).await?;
    Ok(())
}

/// Seek to a position in the current track (in seconds).
#[poise::command(slash_command, guild_only)]
pub async fn seek(
    ctx: Context<'_>,
    #[description = "Position in seconds"]
    #[min = 0]
    position: u32,
) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let data = ctx.data();
    let player_ref = data.players.get(guild_id).await;
    let current = player_ref.lock().await.current.clone();
    let Some(track) = current else {
        ctx.say("Nothing is playing.").await?;
        return Ok(());
    };
    let manager = songbird::get(ctx.serenity_context()).await.unwrap();
    player::start_track(&manager, &data.players, guild_id, track, f64::from(position))
        .await
        .map_err(Error::from)?;
    ctx.say(format!("⏩ Seeked to {}.", format_duration(f64::from(position))))
        .await?;
    Ok(())
}

/// Toggle an audio filter (nightcore, bassboost, reverb, 8d...).
#[poise::command(slash_command, guild_only)]
pub async fn filter(
    ctx: Context<'_>,
    #[description = "Filter name, or 'none' to clear"]
    #[autocomplete = "filter_autocomplete"]
    name: String,
) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let data = ctx.data();
    let player_ref = data.players.get(guild_id).await;

    let name = name.to_lowercase();
    let (current, position, active) = {
        let mut player = player_ref.lock().await;
        if name == "none" {
            player.filters.clear();
        } else if FILTERS.contains(&name.as_str()) {
            if !player.filters.remove(&name) {
                player.filters.insert(name.clone());
            }
        } else {
            drop(player);
            ctx.say(format!("Unknown filter. Available: {}, none", FILTERS.join(", ")))
                .await?;
            return Ok(());
        }
        (
            player.current.clone(),
            player.position(),
            player.filter_names(),
        )
    };

    // Restart at the current position so the change is heard immediately.
    if let Some(track) = current {
        let manager = songbird::get(ctx.serenity_context()).await.unwrap();
        player::start_track(&manager, &data.players, guild_id, track, position)
            .await
            .map_err(Error::from)?;
    }

    let label = if active.is_empty() {
        "none".to_string()
    } else {
        active.join(", ")
    };
    ctx.say(format!("🎛 Active filters: **{label}**")).await?;
    Ok(())
}

async fn filter_autocomplete(_ctx: Context<'_>, partial: &str) -> Vec<String> {
    FILTERS
        .iter()
        .copied()
        .chain(std::iter::once("none"))
        .filter(|f| f.starts_with(&partial.to_lowercase()))
        .map(String::from)
        .collect()
}

/// Toggle looping of the current track.
#[poise::command(slash_command, guild_only, rename = "loop")]
pub async fn loop_track(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let enabled = {
        let mut player = player_ref.lock().await;
        player.loop_current = !player.loop_current;
        player.loop_current
    };
    ctx.say(if enabled { "🔁 Loop enabled." } else { "Loop disabled." })
        .await?;
    Ok(())
}

/// Shuffle the queue.
#[poise::command(slash_command, guild_only)]
pub async fn shuffle(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let count = {
        let mut player = player_ref.lock().await;
        let mut tracks: Vec<_> = player.queue.drain(..).collect();
        tracks.shuffle(&mut rand::thread_rng());
        let count = tracks.len();
        player.queue.extend(tracks);
        count
    };
    ctx.say(format!("🔀 Shuffled {count} tracks.")).await?;
    Ok(())
}

/// Disconnect from the voice channel.
#[poise::command(slash_command, guild_only)]
pub async fn leave(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?;
    let manager = songbird::get(ctx.serenity_context()).await.unwrap();
    ctx.data().players.remove(guild_id.get()).await;
    if manager.get(guild_id).is_some() {
        manager.remove(guild_id).await?;
        ctx.say("👋 Left the voice channel.").await?;
    } else {
        ctx.say("Not in a voice channel.").await?;
    }
    Ok(())
}

/// Add a song to play right after the current one.
#[poise::command(slash_command, guild_only)]
pub async fn playnext(
    ctx: Context<'_>,
    #[description = "URL or search terms"] query: String,
) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ensure_voice(&ctx).await?;
    let data = ctx.data();
    let tracks = match platforms::expand(&data.players.http, &query).await {
        Some(result) => result.map_err(Error::from)?,
        None => ytdlp::resolve(&query).await.map_err(Error::from)?,
    };
    let first_title = tracks.first().map(|t| t.title.clone()).unwrap_or_default();
    let player_ref = data.players.get(guild_id).await;
    let was_idle = {
        let mut player = player_ref.lock().await;
        for track in tracks.into_iter().rev() {
            player.queue.push_front(track);
        }
        player.current.is_none()
    };
    if was_idle {
        let manager = songbird::get(ctx.serenity_context()).await.unwrap();
        player::play_next(&manager, &data.players, guild_id).await;
        ctx.say(format!("▶ Now playing: **{first_title}**")).await?;
    } else {
        ctx.say(format!("⏫ Playing next: **{first_title}**")).await?;
    }
    Ok(())
}

/// Search YouTube and pick from the top results.
#[poise::command(slash_command, guild_only)]
pub async fn search(
    ctx: Context<'_>,
    #[description = "Search terms"] query: String,
) -> Result<(), Error> {
    ctx.defer().await?;
    let results = ytdlp::search_flat(&query, 5).await.map_err(Error::from)?;
    if results.is_empty() {
        ctx.say("No results.").await?;
        return Ok(());
    }

    let options: Vec<serenity::CreateSelectMenuOption> = results
        .iter()
        .enumerate()
        .map(|(index, track)| {
            let mut label = track.title.clone();
            label.truncate(95);
            serenity::CreateSelectMenuOption::new(label, index.to_string())
        })
        .collect();
    let menu = serenity::CreateSelectMenu::new(
        "v3_search",
        serenity::CreateSelectMenuKind::String { options },
    )
    .placeholder("Pick a track");

    let reply = ctx
        .send(
            poise::CreateReply::default()
                .content(format!("🔎 Results for **{query}**:"))
                .components(vec![serenity::CreateActionRow::SelectMenu(menu)]),
        )
        .await?;
    let message = reply.message().await?;

    let Some(interaction) = message
        .await_component_interaction(ctx.serenity_context().shard.clone())
        .timeout(std::time::Duration::from_secs(60))
        .await
    else {
        reply
            .edit(ctx, poise::CreateReply::default().content("⏳ Search expired.").components(vec![]))
            .await?;
        return Ok(());
    };
    let chosen = match &interaction.data.kind {
        serenity::ComponentInteractionDataKind::StringSelect { values } => values
            .first()
            .and_then(|v| v.parse::<usize>().ok())
            .and_then(|i| results.get(i).cloned()),
        _ => None,
    };
    interaction
        .create_response(ctx.http(), serenity::CreateInteractionResponse::Acknowledge)
        .await?;

    let Some(track) = chosen else { return Ok(()) };
    let guild_id = ensure_voice(&ctx).await?;
    let data = ctx.data();
    let player_ref = data.players.get(guild_id).await;
    let was_idle = {
        let mut player = player_ref.lock().await;
        player.queue.push_back(track.clone());
        player.current.is_none()
    };
    if was_idle {
        let manager = songbird::get(ctx.serenity_context()).await.unwrap();
        player::play_next(&manager, &data.players, guild_id).await;
    }
    reply
        .edit(
            ctx,
            poise::CreateReply::default()
                .content(format!("▶ Selected: **{}**", track.title))
                .components(vec![]),
        )
        .await?;
    Ok(())
}

/// Play uploaded audio/video files.
#[poise::command(slash_command, guild_only, rename = "play-files")]
pub async fn play_files(
    ctx: Context<'_>,
    #[description = "Audio or video file"] file: serenity::Attachment,
    #[description = "Another file"] file2: Option<serenity::Attachment>,
    #[description = "Another file"] file3: Option<serenity::Attachment>,
    #[description = "Another file"] file4: Option<serenity::Attachment>,
    #[description = "Another file"] file5: Option<serenity::Attachment>,
) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ensure_voice(&ctx).await?;
    let data = ctx.data();

    let attachments: Vec<serenity::Attachment> = [Some(file), file2, file3, file4, file5]
        .into_iter()
        .flatten()
        .collect();
    let count = attachments.len();
    let player_ref = data.players.get(guild_id).await;
    let was_idle = {
        let mut player = player_ref.lock().await;
        for attachment in attachments {
            // Discord CDN URLs stream directly; webpage_url == stream_url
            // marks the entry as needing no re-resolution.
            player.queue.push_back(ytdlp::Resolved {
                title: attachment.filename.clone(),
                webpage_url: attachment.url.clone(),
                stream_url: Some(attachment.url.clone()),
                duration: 0.0,
                is_live: false,
                thumbnail: None,
            });
        }
        player.current.is_none()
    };
    if was_idle {
        let manager = songbird::get(ctx.serenity_context()).await.unwrap();
        player::play_next(&manager, &data.players, guild_id).await;
    }
    ctx.say(format!("📁 Queued {count} file(s).")).await?;
    Ok(())
}

/// Get lyrics for the current song.
#[poise::command(slash_command, guild_only)]
pub async fn lyrics_cmd(ctx: Context<'_>) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let data = ctx.data();
    let current = data.players.get(guild_id).await.lock().await.current.clone();
    let Some(track) = current else {
        ctx.say("Nothing is playing.").await?;
        return Ok(());
    };
    let lyrics = lyrics::fetch(&data.players.http, &track.title)
        .await
        .map_err(Error::from)?;
    let Some(mut text) = lyrics.plain else {
        ctx.say("No lyrics found for this track.").await?;
        return Ok(());
    };
    if text.len() > 1900 {
        text.truncate(1900);
        text.push_str("\n…");
    }
    ctx.say(format!("📜 **{}**\n\n{text}", track.title)).await?;
    Ok(())
}

/// Karaoke mode: live synced lyrics.
#[poise::command(slash_command, guild_only)]
pub async fn karaoke(ctx: Context<'_>) -> Result<(), Error> {
    ctx.defer().await?;
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let data = ctx.data();
    let player_ref = data.players.get(guild_id).await;
    let current = player_ref.lock().await.current.clone();
    let Some(track) = current else {
        ctx.say("Nothing is playing.").await?;
        return Ok(());
    };

    let lyrics = lyrics::fetch(&data.players.http, &track.title)
        .await
        .map_err(Error::from)?;
    let Some(lines) = lyrics.synced else {
        ctx.say("No synced lyrics found for this track.").await?;
        return Ok(());
    };

    let reply = ctx.say(format!("🎤 Karaoke: **{}**", track.title)).await?;
    let message = reply.message().await?.into_owned();
    let http = ctx.serenity_context().http.clone();
    let title = track.title.clone();
    let players = data.players.clone();

    tokio::spawn(async move {
        let mut last_index = usize::MAX;
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            let player_ref = players.get(guild_id).await;
            let (position, playing) = {
                let player = player_ref.lock().await;
                match &player.current {
                    Some(current) if current.title == title => (player.position(), true),
                    _ => (0.0, false),
                }
            };
            if !playing {
                let _ = message
                    .channel_id
                    .edit_message(
                        &http,
                        message.id,
                        serenity::EditMessage::new().content("🎤 Karaoke session finished."),
                    )
                    .await;
                return;
            }
            let index = lines
                .iter()
                .rposition(|(time, _)| *time <= position)
                .unwrap_or(usize::MAX);
            if index == last_index {
                continue;
            }
            last_index = index;
            let mut content = format!("🎤 **{title}**\n\n");
            let window_start = index.saturating_sub(1).min(lines.len().saturating_sub(1));
            for (offset, (_, line)) in lines
                .iter()
                .enumerate()
                .skip(window_start)
                .take(4)
            {
                if offset == index {
                    content.push_str(&format!("**➤ {line}**\n"));
                } else {
                    content.push_str(&format!("{line}\n"));
                }
            }
            let _ = message
                .channel_id
                .edit_message(&http, message.id, serenity::EditMessage::new().content(content))
                .await;
        }
    });
    Ok(())
}

/// Enable/disable autoplay of similar songs when the queue is empty.
#[poise::command(slash_command, guild_only)]
pub async fn autoplay(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let player_ref = ctx.data().players.get(guild_id).await;
    let enabled = {
        let mut player = player_ref.lock().await;
        player.autoplay = !player.autoplay;
        player.autoplay
    };
    ctx.say(if enabled {
        "♾ Autoplay enabled: similar tracks will keep the music going."
    } else {
        "Autoplay disabled."
    })
    .await?;
    Ok(())
}

#[derive(poise::ChoiceParameter)]
pub enum Mode247 {
    #[name = "normal"]
    Normal,
    #[name = "auto"]
    Auto,
    #[name = "off"]
    Off,
}

/// 24/7 mode: keep the bot in the channel and the music rolling.
#[poise::command(slash_command, guild_only, rename = "24_7")]
pub async fn twenty_four_seven(ctx: Context<'_>, mode: Mode247) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let value = match mode {
        Mode247::Normal => "normal",
        Mode247::Auto => "auto",
        Mode247::Off => "off",
    };
    ctx.data()
        .players
        .settings
        .update(guild_id, |s| s.mode_24_7 = value.to_string());
    ctx.say(match value {
        "normal" => "📻 24/7 normal: staying connected, finished tracks loop back into the queue.",
        "auto" => "♾ 24/7 auto: staying connected with autoplay when the queue runs dry.",
        _ => "24/7 disabled.",
    })
    .await?;
    Ok(())
}

/// Set the default volume used when the bot joins (persisted).
#[poise::command(slash_command, guild_only)]
pub async fn defaultvolume(
    ctx: Context<'_>,
    #[description = "Default volume percentage (0-200)"]
    #[min = 0]
    #[max = 200]
    level: u16,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let volume = f32::from(level) / 100.0;
    ctx.data()
        .players
        .settings
        .update(guild_id, |s| s.default_volume = Some(volume));
    // Apply immediately too, like v2.
    let player_ref = ctx.data().players.get(guild_id).await;
    {
        let mut player = player_ref.lock().await;
        player.volume = volume;
        if let Some(handle) = &player.handle {
            let _ = handle.set_volume(volume);
        }
    }
    ctx.say(format!("🔊 Default volume set to {level}%.")).await?;
    Ok(())
}

/// Refresh the voice connection without losing the queue.
#[poise::command(slash_command, guild_only)]
pub async fn reconnect(ctx: Context<'_>) -> Result<(), Error> {
    ctx.defer().await?;
    let guild = ctx.guild_id().ok_or("server only")?;
    let guild_id = guild.get();
    let data = ctx.data();
    let manager = songbird::get(ctx.serenity_context()).await.unwrap();

    let channel = manager
        .get(guild)
        .and_then(|call| call.try_lock().ok().and_then(|c| c.current_channel()))
        .ok_or("Not connected to a voice channel.")?;

    let player_ref = data.players.get(guild_id).await;
    let (current, position) = {
        let player = player_ref.lock().await;
        (player.current.clone(), player.position())
    };

    manager.remove(guild).await.ok();
    tokio::time::sleep(std::time::Duration::from_millis(750)).await;
    manager
        .join(guild, serenity::ChannelId::new(channel.0.get()))
        .await?;

    if let Some(track) = current {
        player::start_track(&manager, &data.players, guild_id, track, position)
            .await
            .map_err(Error::from)?;
    }
    ctx.say("🔄 Voice connection refreshed.").await?;
    Ok(())
}

/// Bot diagnostics: uptime, memory, versions.
#[poise::command(slash_command)]
pub async fn status(ctx: Context<'_>) -> Result<(), Error> {
    let uptime = crate::START.get().map(|s| s.elapsed().as_secs()).unwrap_or(0);
    let rss = memory_stats::memory_stats()
        .map(|m| m.physical_mem as f64 / 1048576.0)
        .unwrap_or(0.0);
    let players = ctx.data().players.active_count().await;
    let guilds = ctx.serenity_context().cache.guild_count();
    ctx.say(format!(
        "**Playify v3** `{}` — full Rust engine\n\
         Uptime: {}h {}m · RSS: {rss:.0} MB · Guilds: {guilds} · Active players: {players}\n\
         Audio: songbird (DAVE/E2EE) + native DSP · Resolver: yt-dlp subprocess",
        env!("CARGO_PKG_VERSION"),
        uptime / 3600,
        (uptime % 3600) / 60,
    ))
    .await?;
    Ok(())
}

/// Ways to support Playify's creator.
#[poise::command(slash_command)]
pub async fn support(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say(
        "💙 Playify is free and open source: https://github.com/alan7383/playify\n\
         Star the repo, report bugs, or sponsor the author — every bit helps!",
    )
    .await?;
    Ok(())
}

/// Toggle kawaii replies for this server.
#[poise::command(slash_command, guild_only)]
pub async fn kaomoji(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let settings = &ctx.data().players.settings;
    let enabled = !settings.get(guild_id).kawaii;
    settings.update(guild_id, |s| s.kawaii = enabled);
    ctx.say(if enabled {
        "Kawaii mode enabled! (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    } else {
        "Kawaii mode disabled."
    })
    .await?;
    Ok(())
}

#[derive(poise::ChoiceParameter)]
pub enum AllowlistAction {
    #[name = "add"]
    Add,
    #[name = "remove"]
    Remove,
    #[name = "list"]
    List,
    #[name = "clear"]
    Clear,
}

/// Restrict Playify commands to specific channels.
#[poise::command(slash_command, guild_only, default_member_permissions = "MANAGE_CHANNELS")]
pub async fn allowlist(
    ctx: Context<'_>,
    action: AllowlistAction,
    #[description = "Channel (for add/remove; defaults to this one)"]
    channel: Option<serenity::GuildChannel>,
) -> Result<(), Error> {
    let guild_id = ctx.guild_id().ok_or("server only")?.get();
    let settings = &ctx.data().players.settings;
    let target = channel.map(|c| c.id.get()).unwrap_or(ctx.channel_id().get());
    let message = match action {
        AllowlistAction::Add => {
            settings.update(guild_id, |s| {
                if !s.allowed_channels.contains(&target) {
                    s.allowed_channels.push(target);
                }
            });
            format!("✅ <#{target}> added to the allowlist.")
        }
        AllowlistAction::Remove => {
            settings.update(guild_id, |s| s.allowed_channels.retain(|c| *c != target));
            format!("Removed <#{target}> from the allowlist.")
        }
        AllowlistAction::Clear => {
            settings.update(guild_id, |s| s.allowed_channels.clear());
            "Allowlist cleared: commands allowed everywhere.".to_string()
        }
        AllowlistAction::List => {
            let channels = settings.get(guild_id).allowed_channels;
            if channels.is_empty() {
                "No allowlist: commands allowed everywhere.".to_string()
            } else {
                format!(
                    "Allowed channels: {}",
                    channels
                        .iter()
                        .map(|c| format!("<#{c}>"))
                        .collect::<Vec<_>>()
                        .join(", ")
                )
            }
        }
    };
    ctx.say(message).await?;
    Ok(())
}

pub fn all() -> Vec<poise::Command<crate::Data, Error>> {
    let mut lyrics_command = lyrics_cmd();
    lyrics_command.name = "lyrics".to_string();
    vec![
        play(),
        playnext(),
        play_files(),
        search(),
        skip(),
        jumpto(),
        previous(),
        stop(),
        clearqueue(),
        remove(),
        pause(),
        resume(),
        queue(),
        nowplaying(),
        volume(),
        defaultvolume(),
        seek(),
        filter(),
        loop_track(),
        shuffle(),
        autoplay(),
        twenty_four_seven(),
        reconnect(),
        lyrics_command,
        karaoke(),
        status(),
        support(),
        kaomoji(),
        allowlist(),
        leave(),
    ]
}
