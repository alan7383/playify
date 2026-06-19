"""Playify TUI — Animated splash screen with ASCII art logo."""

import time
import sys
import os
from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
from rich.live import Live
from rich.table import Table
from rich.columns import Columns
from rich import box

from .theme import (
    PLAYIFY_THEME, GRADIENT_COLORS, VERSION,
    ICON_CHECK, ICON_CROSS, ICON_SPARK,
    BLUE, BLUE_NAVY, BLUE_LIGHT, BLUE_ICE, BLUE_PALE,
    GREEN, RED, GRAY, WHITE
)

# ─── ASCII Art Logo ──────────────────────────────────────────────────────────
LOGO_LINES = [
    "██████╗ ██╗      █████╗ ██╗   ██╗██╗███████╗██╗   ██╗",
    "██╔══██╗██║     ██╔══██╗╚██╗ ██╔╝██║██╔════╝╚██╗ ██╔╝",
    "██████╔╝██║     ███████║ ╚████╔╝ ██║█████╗   ╚████╔╝ ",
    "██╔═══╝ ██║     ██╔══██║  ╚██╔╝  ██║██╔══╝    ╚██╔╝  ",
    "██║     ███████╗██║  ██║   ██║   ██║██║        ██║   ",
    "╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝╚═╝        ╚═╝   ",
]

TAGLINE = "- Your Discord Music Bot -"


def _gradient_line(line: str, line_index: int, total_lines: int) -> Text:
    """Apply a vertical gradient color to a single line of ASCII art."""
    ratio = line_index / max(total_lines - 1, 1)
    idx = int(ratio * (len(GRADIENT_COLORS) - 1))
    color = GRADIENT_COLORS[idx]
    return Text(line, style=f"bold {color}")


def _build_logo() -> Text:
    """Build the full colored logo as a Rich Text object."""
    result = Text()
    for i, line in enumerate(LOGO_LINES):
        colored = _gradient_line(line, i, len(LOGO_LINES))
        result.append(colored)
        result.append("\n")
    return result


def _build_splash_panel(step_statuses: list[tuple[str, str | None]] | None = None) -> Panel:
    """Build the full splash screen panel with optional setup steps."""
    content = Text()

    # Logo
    logo = _build_logo()
    content.append(logo)

    # Tagline
    content.append("\n")
    tagline = Text(TAGLINE, style=f"italic {BLUE_ICE}")
    content.append_text(tagline)
    content.append("\n")

    # Version badge
    version_text = Text(f"v{VERSION}", style=f"bold {BLUE_LIGHT}")
    content.append_text(version_text)
    content.append("\n\n")

    # Step statuses (if provided)
    if step_statuses:
        for label, status in step_statuses:
            if status == "ok":
                icon = Text(f"  [{ICON_CHECK}] ", style=f"bold {GREEN}")
                msg = Text(label, style=f"{GREEN}")
            elif status == "fail":
                icon = Text(f"  [{ICON_CROSS}] ", style=f"bold {RED}")
                msg = Text(label, style=f"{RED}")
            elif status == "work":
                icon = Text(f"  [..] ", style=f"bold {BLUE_ICE}")
                msg = Text(label, style=f"{BLUE_ICE}")
            else:
                icon = Text("  [ ] ", style=f"{GRAY}")
                msg = Text(label, style=f"{GRAY}")
            line = Text()
            line.append_text(icon)
            line.append_text(msg)
            content.append_text(line)
            content.append("\n")

    panel = Panel(
        Align.center(content),
        border_style=BLUE_NAVY,
        padding=(1, 3),
        title=Text(f" {ICON_SPARK} PLAYIFY {ICON_SPARK} ", style=f"bold {BLUE_LIGHT}"),
        subtitle=Text(f" Discord Music Bot ", style=f"italic {GRAY}"),
    )
    return panel


def show_splash(console: Console) -> None:
    """Display the animated splash screen."""
    console.clear()

    # Phase 1: Show the logo with a "typing" animation per line
    lines_so_far: list[str] = []
    for i, line in enumerate(LOGO_LINES):
        lines_so_far.append(line)
        console.clear()

        content = Text()
        for j, l in enumerate(lines_so_far):
            colored = _gradient_line(l, j, len(LOGO_LINES))
            content.append(colored)
            content.append("\n")

        panel = Panel(
            Align.center(content),
            border_style=BLUE_NAVY,
            padding=(1, 3),
        )
        console.print("\n")
        console.print(Align.center(panel))
        time.sleep(0.08)

    # Phase 2: Full splash with tagline
    time.sleep(0.3)
    console.clear()
    console.print("\n")
    console.print(Align.center(_build_splash_panel()))
    time.sleep(1.0)


def show_setup_progress(console: Console, steps: list[tuple[str, str | None]]) -> None:
    """Show the splash panel with current setup progress."""
    console.clear()
    console.print("\n")
    console.print(Align.center(_build_splash_panel(steps)))
