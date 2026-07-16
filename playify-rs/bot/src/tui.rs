//! Terminal dashboard (--tui): live status + logs, in the spirit of v2's
//! Rich dashboard, built on ratatui. Q quits (state is saved first).

use std::{
    collections::VecDeque,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};

use ratatui::{
    crossterm::event::{self, Event, KeyCode},
    layout::{Constraint, Direction, Layout},
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Borders, List, ListItem, Paragraph},
};

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
                if logs.len() > 300 {
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
    pub active_players: usize,
    pub now_playing: Vec<String>,
}

pub type StatusRef = Arc<Mutex<Status>>;

/// Async side: refreshes the status snapshot the drawing thread reads.
pub async fn status_updater(players: crate::player::Players, status: StatusRef) {
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        let mut now_playing = Vec::new();
        for (guild_id, player_ref) in players.snapshot().await {
            let player = player_ref.lock().await;
            if let Some(track) = &player.current {
                let mut title = track.title.clone();
                title.truncate(60);
                now_playing.push(format!(
                    "{guild_id}: {title}{}",
                    if player.paused { " ⏸" } else { "" }
                ));
            }
        }
        let mut snapshot = status.lock().expect("status lock");
        snapshot.online = players.manager.get().is_some();
        snapshot.active_players = now_playing.len();
        snapshot.now_playing = now_playing;
    }
}

/// Blocking side: owns the terminal until Q/Esc/Ctrl-C.
pub fn run_blocking(logs: LogBuffer, status: StatusRef, quit: Arc<AtomicBool>) {
    let mut terminal = ratatui::init();

    loop {
        let snapshot = status.lock().expect("status lock").clone();
        let log_lines: Vec<String> = logs.lock().expect("log buffer").iter().cloned().collect();

        let _ = terminal.draw(|frame| {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(3),
                    Constraint::Length(2 + snapshot.now_playing.len().max(1) as u16),
                    Constraint::Min(3),
                    Constraint::Length(1),
                ])
                .split(frame.area());

            let uptime = crate::START
                .get()
                .map(|s| s.elapsed().as_secs())
                .unwrap_or(0);
            let rss = memory_stats::memory_stats()
                .map(|m| m.physical_mem as f64 / 1048576.0)
                .unwrap_or(0.0);
            let header = Paragraph::new(format!(
                "  {}  ·  uptime {}h{:02}m  ·  RSS {rss:.0} MB  ·  players {}",
                if snapshot.online { "● ONLINE" } else { "○ starting…" },
                uptime / 3600,
                (uptime % 3600) / 60,
                snapshot.active_players,
            ))
            .style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD))
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" ♪ PLAYIFY v3 — full Rust "),
            );
            frame.render_widget(header, chunks[0]);

            let playing_items: Vec<ListItem> = if snapshot.now_playing.is_empty() {
                vec![ListItem::new("  nothing playing")]
            } else {
                snapshot
                    .now_playing
                    .iter()
                    .map(|line| ListItem::new(format!("  ▶ {line}")))
                    .collect()
            };
            frame.render_widget(
                List::new(playing_items)
                    .block(Block::default().borders(Borders::ALL).title(" Now Playing ")),
                chunks[1],
            );

            let visible = chunks[2].height.saturating_sub(2) as usize;
            let items: Vec<ListItem> = log_lines
                .iter()
                .rev()
                .take(visible)
                .rev()
                .map(|line| {
                    let style = if line.contains("ERROR") {
                        Style::default().fg(Color::Red)
                    } else if line.contains("WARN") {
                        Style::default().fg(Color::Yellow)
                    } else {
                        Style::default().fg(Color::Gray)
                    };
                    ListItem::new(Line::styled(line.clone(), style))
                })
                .collect();
            frame.render_widget(
                List::new(items).block(Block::default().borders(Borders::ALL).title(" Logs ")),
                chunks[2],
            );

            frame.render_widget(
                Paragraph::new("  Q quit (saves playback state)")
                    .style(Style::default().fg(Color::DarkGray)),
                chunks[3],
            );
        });

        if event::poll(Duration::from_millis(250)).unwrap_or(false) {
            if let Ok(Event::Key(key)) = event::read() {
                match key.code {
                    KeyCode::Char('q') | KeyCode::Char('Q') | KeyCode::Esc => break,
                    KeyCode::Char('c')
                        if key
                            .modifiers
                            .contains(ratatui::crossterm::event::KeyModifiers::CONTROL) =>
                    {
                        break
                    }
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
