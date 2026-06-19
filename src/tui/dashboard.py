"""Playify TUI — Live dashboard with bot status, music info, and logs."""

import time
import sys
import os
import threading
if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty
    import select
from pathlib import Path
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.columns import Columns
from rich import box

from .theme import (
    PLAYIFY_THEME, VERSION,
    ICON_CHECK, ICON_CROSS, ICON_MUSIC, ICON_PLAY, ICON_PAUSE, ICON_STOP,
    ICON_LOOP, ICON_VOLUME, ICON_ONLINE, ICON_OFFLINE, ICON_ARROW,
    ICON_SPARK, ICON_WARN, ICON_GEAR, ICON_ROCKET,
    BLUE, BLUE_NAVY, BLUE_LIGHT, BLUE_ICE, BLUE_PALE, BLUE_DARK, BLUE_MID,
    GREEN, RED, YELLOW, GRAY, GRAY_DARK, WHITE,
)
from .bot_process import BotProcess


# ─── Log Coloring ─────────────────────────────────────────────────────────────

def _colorize_log(line: str, max_width: int = 0) -> Text:
    """Colorize a single log line based on its level, truncated to max_width."""
    # Truncate long lines to prevent wrapping
    if max_width > 0 and len(line) > max_width:
        line = line[:max_width - 3] + "..."

    text = Text(overflow="ellipsis", no_wrap=True)

    lower = line.lower()
    if " - error - " in lower or "error" in lower[:30]:
        text.append(line, style=f"{RED}")
    elif " - warning - " in lower or "warning" in lower[:30]:
        text.append(line, style=f"{YELLOW}")
    elif " - info - " in lower or "info" in lower[:30]:
        text.append(line, style=f"{BLUE_LIGHT}")
    elif " - debug - " in lower:
        text.append(line, style=f"{GRAY_DARK}")
    else:
        text.append(line, style=f"{GRAY}")

    return text


# ─── Dashboard Components ─────────────────────────────────────────────────────

def _build_header() -> Panel:
    """Build the top header bar."""
    header_text = Text()
    header_text.append(f"  {ICON_MUSIC} ", style=f"bold {BLUE_ICE}")
    header_text.append("PLAYIFY", style=f"bold {BLUE_LIGHT}")
    header_text.append(" DASHBOARD", style=f"bold {BLUE_ICE}")
    header_text.append(f"  {ICON_MUSIC}", style=f"bold {BLUE_ICE}")

    version = Text(f" v{VERSION} ", style=f"bold {GRAY}")

    return Panel(
        Align.center(header_text),
        border_style=BLUE_NAVY,
        padding=(0, 0),
        subtitle=version,
    )


def _build_status_panel(bot: BotProcess) -> Panel:
    """Build the bot status panel."""
    content = Text()

    # Online/Offline status
    if bot.is_online:
        content.append(f"  {ICON_ONLINE} ", style=f"bold {GREEN}")
        content.append("Online\n", style=f"bold {GREEN}")
    elif bot.is_running:
        content.append(f"  {ICON_ONLINE} ", style=f"bold {YELLOW}")
        content.append("Starting...\n", style=f"bold {YELLOW}")
    else:
        content.append(f"  {ICON_OFFLINE} ", style=f"bold {RED}")
        content.append("Offline\n", style=f"bold {RED}")

    content.append("\n")

    # Stats
    stats = [
        ("Uptime", bot.uptime_str if bot.is_running else "--"),
        ("Memory", f"{bot.memory_mb:.0f} MB" if bot.memory_mb > 0 else "--"),
        ("Servers", str(bot.guild_count) if bot.is_online else "--"),
        ("Players", str(bot.active_players) if bot.is_online else "--"),
        ("Queued", str(bot.queued_songs) if bot.is_online else "--"),
        ("FFmpeg", str(bot.ffmpeg_processes) if bot.is_running else "--"),
        ("URL Cache", str(bot.url_cache_size) if bot.is_online else "--"),
        ("Crashes", str(bot.crash_count) if bot.crash_count > 0 else "0"),
    ]

    for label, value in stats:
        content.append(f"  {label:<10}", style=f"{GRAY}")
        val_style = f"bold {WHITE}"
        if label == "Crashes" and bot.crash_count > 0:
            val_style = f"bold {RED}"
        content.append(f" {value}\n", style=val_style)

    return Panel(
        content,
        title=Text(f" {ICON_ROCKET} Bot Status ", style=f"bold {BLUE_LIGHT}"),
        border_style=BLUE_NAVY,
        padding=(1, 1),
        expand=True,
    )


def _build_music_panel(bot: BotProcess) -> Panel:
    """Build the now playing panel."""
    content = Text()

    if bot.current_song:
        content.append(f"  {ICON_PLAY} ", style=f"bold {GREEN}")
        content.append("Now Playing\n\n", style=f"bold {GREEN}")
        content.append(f"  {ICON_MUSIC} ", style=f"bold {BLUE_LIGHT}")
        # Truncate long titles
        title = bot.current_song
        if len(title) > 30:
            title = title[:27] + "..."
        content.append(f"{title}\n", style=f"bold {WHITE}")
        if bot.current_artist:
            content.append(f"    {bot.current_artist}\n", style=f"{BLUE_ICE}")
    else:
        content.append(f"\n  {ICON_PAUSE} ", style=f"{GRAY}")
        content.append("Nothing playing\n", style=f"italic {GRAY}")
        content.append(f"\n  Waiting for music...\n", style=f"{GRAY_DARK}")

    content.append("\n")

    return Panel(
        content,
        title=Text(f" {ICON_MUSIC} Now Playing ", style=f"bold {BLUE_LIGHT}"),
        border_style=BLUE_NAVY,
        padding=(1, 1),
        expand=True,
    )


def _build_logs_panel(
    bot: BotProcess,
    max_log_lines: int = 8,
    term_width: int = 120,
    full_mode: bool = False,
    scroll_offset: int = 0,
) -> Panel:
    """Build the logs panel with recent log lines, capped to max_log_lines."""
    # Reserve space for panel borders + padding (4 chars each side)
    usable_width = max(40, term_width - 8)

    if full_mode:
        all_logs = bot.get_all_logs()
        total = len(all_logs)
        max_offset = max(0, total - max_log_lines)
        offset = min(scroll_offset, max_offset)
        visible_logs = all_logs[offset:offset + max_log_lines]

        content = Text()
        if total > max_log_lines:
            info_line = f"  [{offset + 1}-{min(offset + max_log_lines, total)}/{total}]  (up/down to scroll, L to exit)"
            content.append(info_line, style=f"italic {GRAY_DARK}")
            content.append("\n")

        for line in visible_logs:
            colored = _colorize_log(line, usable_width)
            content.append("  ")
            content.append_text(colored)
            content.append("\n")

        # Pad remaining lines so the panel stays a fixed height
        visible_count = len(visible_logs) + (1 if total > max_log_lines else 0)
        for _ in range(max_log_lines - visible_count + 1):
            content.append("\n")

        title_extra = " (FULL VIEW)"
    else:
        logs = bot.get_recent_logs(max_log_lines)
        content = Text()
        if not logs:
            content.append(f"  Waiting for logs...", style=f"italic {GRAY_DARK}")
            content.append("\n")

        for line in logs:
            colored = _colorize_log(line, usable_width)
            content.append("  ")
            content.append_text(colored)
            content.append("\n")

        # Pad remaining lines so the panel stays a fixed height
        for _ in range(max_log_lines - len(logs)):
            content.append("\n")

        title_extra = ""

    return Panel(
        content,
        title=Text(f" # Logs{title_extra} ", style=f"bold {BLUE_LIGHT}"),
        border_style=BLUE_NAVY,
        padding=(0, 1),
        expand=True,
    )


def _build_hotkeys_bar(full_log_mode: bool = False) -> Panel:
    """Build the bottom hotkeys bar."""
    keys = Text()
    keys.append("  ")

    if full_log_mode:
        hotkeys = [
            ("^/v", "Scroll"),
            ("L", "Exit Logs"),
            ("Q", "Quit"),
        ]
    else:
        hotkeys = [
            ("L", "Full Logs"),
            ("C", "Config"),
            ("S", "Settings"),
            ("U", "Update"),
            ("R", "Restart Bot"),
            ("Q", "Quit"),
        ]

    for i, (key, desc) in enumerate(hotkeys):
        keys.append(f" {key} ", style=f"bold {BLUE_ICE} on #1B3A5C")
        keys.append(f" {desc}", style=f"{GRAY}")
        if i < len(hotkeys) - 1:
            keys.append("   ", style=f"{GRAY_DARK}")

    return Panel(
        Align.center(keys),
        border_style=BLUE_DARK,
        padding=(0, 0),
    )


def _build_crash_panel(bot: BotProcess) -> Panel:
    """Build a crash notification panel."""
    content = Text()
    content.append(f"\n  {ICON_WARN} ", style=f"bold {RED}")
    content.append("Bot has stopped!", style=f"bold {RED}")
    content.append(f"\n\n  Exit code: ", style=f"{GRAY}")
    content.append(f"{bot.last_exit_code}\n", style=f"bold {YELLOW}")

    # Show last few log lines for context
    last_logs = bot.get_recent_logs(5)
    if last_logs:
        content.append(f"\n  Last log output:\n", style=f"{GRAY}")
        for line in last_logs:
            content.append(f"  {line}\n", style=f"{RED}")

    content.append(f"\n  Press ", style=f"{WHITE}")
    content.append("R", style=f"bold {BLUE_ICE}")
    content.append(" to restart or ", style=f"{WHITE}")
    content.append("Q", style=f"bold {BLUE_ICE}")
    content.append(" to quit.\n", style=f"{WHITE}")

    return Panel(
        content,
        title=Text(f" {ICON_WARN} Bot Crashed ", style=f"bold {RED}"),
        border_style=RED,
        padding=(1, 2),
    )


# ─── Height Calculation ──────────────────────────────────────────────────────

# Fixed heights of dashboard components (in terminal rows):
# Header panel:   3 lines (border top + content + border bottom)
# Status/Music:  12 lines (border + padding + 8 content lines + padding + border)
# Hotkeys bar:    3 lines (border top + content + border bottom)
# Logs panel:     2 lines for borders + 1 for padding
#
# Available for log lines = terminal_height - header(3) - status(12) - hotkeys(3) - logs_border(2)

HEADER_HEIGHT = 3
STATUS_PANEL_HEIGHT = 15
HOTKEYS_HEIGHT = 3
LOGS_BORDER_HEIGHT = 2  # top border + bottom border of the log panel


def _calc_log_lines(term_height: int, full_mode: bool = False) -> int:
    """Calculate how many log lines fit in the available terminal space."""
    if full_mode:
        # In full mode: header + logs + hotkeys
        overhead = HEADER_HEIGHT + HOTKEYS_HEIGHT + LOGS_BORDER_HEIGHT + 1
    else:
        # Normal mode: header + status panels + logs + hotkeys
        overhead = HEADER_HEIGHT + STATUS_PANEL_HEIGHT + HOTKEYS_HEIGHT + LOGS_BORDER_HEIGHT + 1

    available = term_height - overhead
    return max(3, available)  # Minimum 3 log lines


# ─── Main Dashboard ──────────────────────────────────────────────────────────

def _build_dashboard(
    bot: BotProcess,
    term_width: int = 120,
    term_height: int = 30,
    full_log_mode: bool = False,
    scroll_offset: int = 0,
) -> Group:
    """Build the complete dashboard layout, fitted to terminal size."""
    header = _build_header()

    if not bot.is_running and bot.last_exit_code is not None:
        crash = _build_crash_panel(bot)
        hotkeys = _build_hotkeys_bar(full_log_mode)
        return Group(header, crash, hotkeys)

    max_log_lines = _calc_log_lines(term_height, full_log_mode)

    if full_log_mode:
        logs = _build_logs_panel(
            bot,
            max_log_lines=max_log_lines,
            term_width=term_width,
            full_mode=True,
            scroll_offset=scroll_offset,
        )
        hotkeys = _build_hotkeys_bar(full_log_mode=True)
        return Group(header, logs, hotkeys)

    # Normal dashboard view
    side_by_side = Table(show_header=False, box=None, expand=True, padding=0)
    side_by_side.add_column(ratio=1)
    side_by_side.add_column(ratio=1)
    side_by_side.add_row(
        _build_status_panel(bot),
        _build_music_panel(bot),
    )

    logs = _build_logs_panel(
        bot,
        max_log_lines=max_log_lines,
        term_width=term_width,
    )
    hotkeys = _build_hotkeys_bar()

    return Group(header, side_by_side, logs, hotkeys)


def _get_keypress() -> str | None:
    """Non-blocking keypress detection (Cross-platform)."""
    if os.name == "nt":
        if msvcrt.kbhit():
            key = msvcrt.getch()
            # Handle special keys (arrows)
            if key in (b"\x00", b"\xe0"):
                special = msvcrt.getch()
                if special == b"H":  # Up arrow
                    return "UP"
                elif special == b"P":  # Down arrow
                    return "DOWN"
                return None
            try:
                return key.decode("utf-8").lower()
            except UnicodeDecodeError:
                return None
        return None
    else:
        if select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)
            if key == '\x1b':
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    if sys.stdin.read(1) == '[':
                        if select.select([sys.stdin], [], [], 0.05)[0]:
                            arrow = sys.stdin.read(1)
                            if arrow == 'A': return "UP"
                            if arrow == 'B': return "DOWN"
            return key.lower()
        return None


def run_dashboard(console: Console, bot: BotProcess, project_root: Path) -> str:
    """
    Run the live dashboard.
    Returns: "quit", "config", or "restart"
    """
    full_log_mode = False
    scroll_offset = 0

    if os.name != "nt":
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

    try:
        with Live(
            _build_dashboard(bot),
            console=console,
            refresh_per_second=2,
            screen=True,
        ) as live:
            while True:
                try:
                    # Get current terminal dimensions for dynamic sizing
                    term_width = console.size.width
                    term_height = console.size.height

                    max_log_lines = _calc_log_lines(term_height, full_log_mode)

                    live.update(
                        _build_dashboard(
                            bot,
                            term_width=term_width,
                            term_height=term_height,
                            full_log_mode=full_log_mode,
                            scroll_offset=scroll_offset,
                        )
                    )

                    key = _get_keypress()
                    if key:
                        if key == "q":
                            return "quit"
                        elif key == "l":
                            full_log_mode = not full_log_mode
                            if full_log_mode:
                                total = len(bot.get_all_logs())
                                fl_max = _calc_log_lines(term_height, True)
                                scroll_offset = max(0, total - fl_max)
                            else:
                                scroll_offset = 0
                        elif key == "c":
                            return "config"
                        elif key == "s":
                            return "settings"
                        elif key == "u":
                            return "update"
                        elif key == "r":
                            return "restart"
                        elif key == "UP" and full_log_mode:
                            scroll_offset = max(0, scroll_offset - 3)
                        elif key == "DOWN" and full_log_mode:
                            total = len(bot.get_all_logs())
                            scroll_offset = min(max(0, total - max_log_lines), scroll_offset + 3)

                    time.sleep(0.1)

                except KeyboardInterrupt:
                    return "quit"
    finally:
        if os.name != "nt":
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
