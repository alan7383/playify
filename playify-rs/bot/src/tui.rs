//! Terminal dashboard (--tui), pixel-faithful port of v2's Rich dashboard:
//! same palette (deep blues, navy borders, ice accents), same layout
//! (header / Bot Status + Now Playing side by side / logs / hotkeys bar),
//! same ASCII icons, full-log mode on L with scrolling.

use std::{
    collections::VecDeque,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};

use ratatui::{
    crossterm::event::{self, Event, KeyCode, KeyModifiers},
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
    widgets::{Block, BorderType, Borders, Paragraph},
    Frame,
};

// ─── v2 palette (src/tui/theme.py) ───────────────────────────────────────────
const BLUE_NAVY: Color = Color::Rgb(0x1B, 0x3A, 0x5C);
const BLUE_DARK: Color = Color::Rgb(0x0D, 0x21, 0x37);
const BLUE_LIGHT: Color = Color::Rgb(0x5D, 0xAD, 0xE2);
const BLUE_ICE: Color = Color::Rgb(0x85, 0xC1, 0xE9);
const WHITE: Color = Color::Rgb(0xEC, 0xF0, 0xF1);
const GRAY: Color = Color::Rgb(0x7F, 0x8C, 0x8D);
const GRAY_DARK: Color = Color::Rgb(0x56, 0x65, 0x73);
const RED: Color = Color::Rgb(0xE7, 0x4C, 0x3C);
const GREEN: Color = Color::Rgb(0x27, 0xAE, 0x60);
const YELLOW: Color = Color::Rgb(0xF1, 0xC4, 0x0F);

const VERSION: &str = "3.0.0";

pub type LogBuffer = Arc<Mutex<VecDeque<String>>>;

/// tracing writer that feeds the dashboard's log panel.
#[derive(Clone)]
pub struct LogWriter(pub LogBuffer);

impl std::io::Write for LogWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let text = String::from_utf8_lossy(buf);
        let mut logs = self.0.lock().expect("log buffer");
        for line in text.lines() {
            if !line.trim().is_empty() {
                logs.push_back(line.to_string());
                if logs.len() > 500 {
                    logs.pop_front();
                }
            }
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for LogWriter {
    type Writer = LogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

#[derive(Default, Clone)]
pub struct Status {
    pub online: bool,
    pub servers: usize,
    pub active_players: usize,
    pub queued: usize,
    /// (title, paused) of the most recent active player.
    pub current: Option<(String, bool)>,
}

pub type StatusRef = Arc<Mutex<Status>>;

/// Async side: refreshes the snapshot the drawing thread reads.
pub async fn status_updater(players: crate::player::Players, status: StatusRef) {
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        let mut active = 0;
        let mut queued = 0;
        let mut current = None;
        for (_, player_ref) in players.snapshot().await {
            let player = player_ref.lock().await;
            queued += player.queue.len();
            if let Some(track) = &player.current {
                active += 1;
                if current.is_none() {
                    current = Some((track.title.clone(), player.paused));
                }
            }
        }
        let servers = players
            .discord_cache
            .get()
            .map(|cache| cache.guild_count())
            .unwrap_or(0);
        let mut snapshot = status.lock().expect("status lock");
        snapshot.online = players.manager.get().is_some();
        snapshot.servers = servers;
        snapshot.active_players = active;
        snapshot.queued = queued;
        snapshot.current = current;
    }
}

// ─── Drawing ─────────────────────────────────────────────────────────────────

fn navy_block(title: &str) -> Block<'_> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(BLUE_NAVY))
        .title(Span::styled(
            title.to_string(),
            Style::default().fg(BLUE_LIGHT).add_modifier(Modifier::BOLD),
        ))
}

fn draw_header(frame: &mut Frame, area: Rect) {
    let title = Line::from(vec![
        Span::styled("♪ ", Style::default().fg(BLUE_ICE).add_modifier(Modifier::BOLD)),
        Span::styled("PLAYIFY", Style::default().fg(BLUE_LIGHT).add_modifier(Modifier::BOLD)),
        Span::styled(" DASHBOARD", Style::default().fg(BLUE_ICE).add_modifier(Modifier::BOLD)),
        Span::styled("  ♪", Style::default().fg(BLUE_ICE).add_modifier(Modifier::BOLD)),
    ]);
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(BLUE_NAVY))
        .title_bottom(
            Line::from(Span::styled(
                format!(" v{VERSION} — full Rust "),
                Style::default().fg(GRAY).add_modifier(Modifier::BOLD),
            ))
            .right_aligned(),
        );
    frame.render_widget(
        Paragraph::new(title).alignment(Alignment::Center).block(block),
        area,
    );
}

fn stat_line<'a>(label: &'a str, value: String, value_style: Style) -> Line<'a> {
    Line::from(vec![
        Span::styled(format!("  {label:<10}"), Style::default().fg(GRAY)),
        Span::styled(format!(" {value}"), value_style),
    ])
}

fn draw_status(frame: &mut Frame, area: Rect, status: &Status) {
    let bold_white = Style::default().fg(WHITE).add_modifier(Modifier::BOLD);
    let uptime = crate::START.get().map(|s| s.elapsed().as_secs()).unwrap_or(0);
    let uptime_text = if uptime < 60 {
        format!("{uptime}s")
    } else if uptime < 3600 {
        format!("{}m {}s", uptime / 60, uptime % 60)
    } else {
        format!("{}h {}m", uptime / 3600, (uptime % 3600) / 60)
    };
    let rss = memory_stats::memory_stats()
        .map(|m| m.physical_mem as f64 / 1048576.0)
        .unwrap_or(0.0);

    let mut lines = vec![Line::default()];
    lines[0] = if status.online {
        Line::from(vec![
            Span::styled("  * ", Style::default().fg(GREEN).add_modifier(Modifier::BOLD)),
            Span::styled("Online", Style::default().fg(GREEN).add_modifier(Modifier::BOLD)),
        ])
    } else {
        Line::from(vec![
            Span::styled("  * ", Style::default().fg(YELLOW).add_modifier(Modifier::BOLD)),
            Span::styled("Starting...", Style::default().fg(YELLOW).add_modifier(Modifier::BOLD)),
        ])
    };
    lines.push(Line::default());
    lines.push(stat_line("Uptime", uptime_text, bold_white));
    lines.push(stat_line("Memory", format!("{rss:.0} MB"), bold_white));
    lines.push(stat_line("Servers", status.servers.to_string(), bold_white));
    lines.push(stat_line("Players", status.active_players.to_string(), bold_white));
    lines.push(stat_line("Queued", status.queued.to_string(), bold_white));
    lines.push(stat_line("FFmpeg", "0 (native)".into(), bold_white));
    lines.push(stat_line("Resolver", "yt-dlp".into(), bold_white));
    lines.push(stat_line("Engine", "Rust DSP".into(), bold_white));

    frame.render_widget(
        Paragraph::new(Text::from(lines)).block(navy_block(" >> Bot Status ")),
        area,
    );
}

fn draw_music(frame: &mut Frame, area: Rect, status: &Status) {
    let mut lines = Vec::new();
    match &status.current {
        Some((title, paused)) => {
            let (icon, state, color) = if *paused {
                ("|| ", "Paused", YELLOW)
            } else {
                ("> ", "Now Playing", GREEN)
            };
            lines.push(Line::from(vec![
                Span::styled(format!("  {icon}"), Style::default().fg(color).add_modifier(Modifier::BOLD)),
                Span::styled(state, Style::default().fg(color).add_modifier(Modifier::BOLD)),
            ]));
            lines.push(Line::default());
            let mut title = title.clone();
            if title.len() > 30 {
                title.truncate(27);
                title.push_str("...");
            }
            lines.push(Line::from(vec![
                Span::styled("  ♪ ", Style::default().fg(BLUE_LIGHT).add_modifier(Modifier::BOLD)),
                Span::styled(title, Style::default().fg(WHITE).add_modifier(Modifier::BOLD)),
            ]));
            if status.active_players > 1 {
                lines.push(Line::from(Span::styled(
                    format!("    +{} other server(s)", status.active_players - 1),
                    Style::default().fg(BLUE_ICE),
                )));
            }
        }
        None => {
            lines.push(Line::default());
            lines.push(Line::from(vec![
                Span::styled("  || ", Style::default().fg(GRAY)),
                Span::styled("Nothing playing", Style::default().fg(GRAY).add_modifier(Modifier::ITALIC)),
            ]));
            lines.push(Line::default());
            lines.push(Line::from(Span::styled(
                "  Waiting for music...",
                Style::default().fg(GRAY_DARK),
            )));
        }
    }
    frame.render_widget(
        Paragraph::new(Text::from(lines)).block(navy_block(" ♪ Now Playing ")),
        area,
    );
}

fn colorize_log(line: &str, width: usize) -> Line<'_> {
    let mut text = line.to_string();
    if width > 8 && text.len() > width - 5 {
        text.truncate(width - 8);
        text.push_str("...");
    }
    let lower = line.to_lowercase();
    let style = if lower.contains("error") {
        Style::default().fg(RED)
    } else if lower.contains("warn") {
        Style::default().fg(YELLOW)
    } else if lower.contains("info") {
        Style::default().fg(BLUE_LIGHT)
    } else if lower.contains("debug") {
        Style::default().fg(GRAY_DARK)
    } else {
        Style::default().fg(GRAY)
    };
    Line::from(Span::styled(format!("  {text}"), style))
}

fn draw_logs(frame: &mut Frame, area: Rect, logs: &[String], full: bool, offset: usize) {
    let visible = area.height.saturating_sub(2) as usize;
    let width = area.width.saturating_sub(4) as usize;
    let mut lines: Vec<Line> = Vec::new();

    if full {
        let max_offset = logs.len().saturating_sub(visible.saturating_sub(1));
        let offset = offset.min(max_offset);
        lines.push(Line::from(Span::styled(
            format!(
                "  [{}-{}/{}]  (up/down to scroll, L to exit)",
                offset + 1,
                (offset + visible.saturating_sub(1)).min(logs.len()),
                logs.len()
            ),
            Style::default().fg(GRAY_DARK).add_modifier(Modifier::ITALIC),
        )));
        for line in logs.iter().skip(offset).take(visible.saturating_sub(1)) {
            lines.push(colorize_log(line, width));
        }
    } else if logs.is_empty() {
        lines.push(Line::from(Span::styled(
            "  Waiting for logs...",
            Style::default().fg(GRAY_DARK).add_modifier(Modifier::ITALIC),
        )));
    } else {
        for line in logs.iter().rev().take(visible).rev() {
            lines.push(colorize_log(line, width));
        }
    }

    let title = if full { " # Logs (FULL VIEW) " } else { " # Logs " };
    frame.render_widget(Paragraph::new(Text::from(lines)).block(navy_block(title)), area);
}

fn draw_hotkeys(frame: &mut Frame, area: Rect, full: bool) {
    let chip = Style::default()
        .fg(BLUE_ICE)
        .bg(BLUE_NAVY)
        .add_modifier(Modifier::BOLD);
    let desc = Style::default().fg(GRAY);
    let hotkeys: &[(&str, &str)] = if full {
        &[("^/v", "Scroll"), ("L", "Exit Logs"), ("Q", "Quit")]
    } else {
        &[("L", "Full Logs"), ("S", "Save State"), ("Q", "Quit")]
    };
    let mut spans = vec![Span::raw("  ")];
    for (index, (key, text)) in hotkeys.iter().enumerate() {
        spans.push(Span::styled(format!(" {key} "), chip));
        spans.push(Span::styled(format!(" {text}"), desc));
        if index < hotkeys.len() - 1 {
            spans.push(Span::raw("   "));
        }
    }
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(BLUE_DARK));
    frame.render_widget(
        Paragraph::new(Line::from(spans)).alignment(Alignment::Center).block(block),
        area,
    );
}

/// Blocking side: owns the terminal until Q/Esc/Ctrl-C. `save_requested`
/// is set when the user presses S; the async side persists and clears it.
pub fn run_blocking(
    logs: LogBuffer,
    status: StatusRef,
    quit: Arc<AtomicBool>,
    save_requested: Arc<AtomicBool>,
) {
    let mut terminal = ratatui::init();
    let mut full_logs = false;
    let mut scroll: usize = 0;

    loop {
        let snapshot = status.lock().expect("status lock").clone();
        let log_lines: Vec<String> = logs.lock().expect("log buffer").iter().cloned().collect();

        let _ = terminal.draw(|frame| {
            if full_logs {
                let chunks = Layout::default()
                    .direction(Direction::Vertical)
                    .constraints([Constraint::Length(3), Constraint::Min(3), Constraint::Length(3)])
                    .split(frame.area());
                draw_header(frame, chunks[0]);
                draw_logs(frame, chunks[1], &log_lines, true, scroll);
                draw_hotkeys(frame, chunks[2], true);
            } else {
                let chunks = Layout::default()
                    .direction(Direction::Vertical)
                    .constraints([
                        Constraint::Length(3),
                        Constraint::Length(13),
                        Constraint::Min(3),
                        Constraint::Length(3),
                    ])
                    .split(frame.area());
                draw_header(frame, chunks[0]);
                let columns = Layout::default()
                    .direction(Direction::Horizontal)
                    .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
                    .split(chunks[1]);
                draw_status(frame, columns[0], &snapshot);
                draw_music(frame, columns[1], &snapshot);
                draw_logs(frame, chunks[2], &log_lines, false, 0);
                draw_hotkeys(frame, chunks[3], false);
            }
        });

        if event::poll(Duration::from_millis(250)).unwrap_or(false) {
            if let Ok(Event::Key(key)) = event::read() {
                match key.code {
                    KeyCode::Char('q') | KeyCode::Char('Q') | KeyCode::Esc => break,
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => break,
                    KeyCode::Char('l') | KeyCode::Char('L') => {
                        full_logs = !full_logs;
                        scroll = usize::MAX; // snap to the latest lines
                    }
                    KeyCode::Char('s') | KeyCode::Char('S') => {
                        save_requested.store(true, Ordering::Relaxed);
                    }
                    KeyCode::Up if full_logs => scroll = scroll.saturating_sub(3),
                    KeyCode::Down if full_logs => scroll = scroll.saturating_add(3),
                    _ => {}
                }
            }
        }
        if quit.load(Ordering::Relaxed) {
            break;
        }
    }

    ratatui::restore();
}
