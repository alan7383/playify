"""Playify TUI — Interactive setup wizard for .env configuration."""

import os
import re
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.table import Table
from rich import box

from .theme import (
    PLAYIFY_THEME, ICON_CHECK, ICON_CROSS, ICON_ARROW, ICON_GEAR, ICON_SPARK,
    BLUE, BLUE_NAVY, BLUE_LIGHT, BLUE_ICE, BLUE_PALE, BLUE_ELECTRIC,
    GREEN, RED, YELLOW, GRAY, WHITE
)


# ─── Field definitions ───────────────────────────────────────────────────────

FIELDS = [
    {
        "key": "DISCORD_TOKEN",
        "label": "Discord Bot Token",
        "required": True,
        "hint": "Get one at: https://discord.com/developers/applications",
        "help": "Create an Application -> Bot -> Copy Token",
        "validate": lambda v: len(v) > 50,
        "error_msg": "Token seems too short. Discord tokens are usually 70+ characters.",
    },
    {
        "key": "SPOTIFY_CLIENT_ID",
        "label": "Spotify Client ID",
        "required": False,
        "hint": "Get one at: https://developer.spotify.com/dashboard",
        "help": "Create an App -> Settings -> Client ID",
        "validate": lambda v: len(v) == 32 if v else True,
        "error_msg": "Spotify Client IDs are exactly 32 characters.",
    },
    {
        "key": "SPOTIFY_CLIENT_SECRET",
        "label": "Spotify Client Secret",
        "required": False,
        "hint": "Same dashboard as Client ID",
        "help": "Create an App -> Settings -> View Client Secret",
        "validate": lambda v: len(v) == 32 if v else True,
        "error_msg": "Spotify Client Secrets are exactly 32 characters.",
    },
    {
        "key": "GENIUS_TOKEN",
        "label": "Genius API Token (for lyrics)",
        "required": False,
        "hint": "Get one at: https://genius.com/api-clients",
        "help": "Create an API Client -> Generate Access Token",
        "validate": lambda v: True,
        "error_msg": "",
    },
]


def _build_field_panel(field: dict, step: int, total: int) -> Panel:
    """Build a styled panel for a single config field prompt."""
    content = Text()

    # Step counter
    content.append(f"  Step {step}/{total}", style=f"bold {BLUE_ICE}")
    content.append("\n\n")

    # Field name
    if field["required"]:
        required_tag = Text(" REQUIRED ", style=f"bold white on red")
    else:
        required_tag = Text(" OPTIONAL ", style=f"bold white on {GRAY[1:]}")

    content.append(f"  {ICON_GEAR} ", style=f"{BLUE_LIGHT}")
    content.append(field["label"], style=f"bold {WHITE}")
    content.append("  ")
    content.append_text(required_tag)
    content.append("\n\n")

    # Hint
    content.append(f"  {ICON_ARROW} ", style=f"{BLUE_ICE}")
    content.append(field["hint"], style=f"italic {GRAY}")
    content.append("\n")
    content.append(f"    {field['help']}", style=f"{GRAY}")
    content.append("\n\n")

    # Prompt instruction
    if field["required"]:
        content.append("  Paste your token below:", style=f"{BLUE_ICE}")
    else:
        content.append("  Paste your token below (or press Enter to skip):", style=f"{BLUE_ICE}")
    content.append("\n")

    return Panel(
        content,
        border_style=BLUE_NAVY,
        title=Text(f" {ICON_SPARK} Configuration {ICON_SPARK} ", style=f"bold {BLUE_LIGHT}"),
        padding=(1, 2),
    )


def _build_summary_panel(values: dict) -> Panel:
    """Build a summary panel showing all configured values."""
    table = Table(
        show_header=True,
        header_style=f"bold {BLUE_LIGHT}",
        border_style=BLUE_NAVY,
        box=box.ROUNDED,
        expand=True,
        padding=(0, 2),
    )
    table.add_column("Setting", style=f"bold {WHITE}", ratio=1)
    table.add_column("Status", style=f"{WHITE}", ratio=2)

    for field in FIELDS:
        val = values.get(field["key"], "")
        if val:
            # Mask the token for display
            if len(val) > 10:
                display = val[:6] + "*" * (len(val) - 10) + val[-4:]
            else:
                display = "*" * len(val)
            status = Text(f"[{ICON_CHECK}] Configured", style=f"bold {GREEN}")
            detail = Text(f"  ({display})", style=f"{GRAY}")
        else:
            if field["required"]:
                status = Text(f"[{ICON_CROSS}] Missing!", style=f"bold {RED}")
                detail = Text("")
            else:
                status = Text(f"[ ] Skipped", style=f"{GRAY}")
                detail = Text("")

        row_status = Text()
        row_status.append_text(status)
        if detail.plain:
            row_status.append("\n")
            row_status.append_text(detail)

        table.add_row(field["label"], row_status)

    return Panel(
        table,
        border_style=BLUE_NAVY,
        title=Text(f" {ICON_SPARK} Configuration Summary {ICON_SPARK} ", style=f"bold {BLUE_LIGHT}"),
        padding=(1, 2),
    )


def _save_env(values: dict, env_path: Path) -> None:
    """Save values to .env file."""
    lines = [
        "# Playify - Bot Configuration",
        "",
        "# [REQUIRED] Discord Bot Token",
        "# Get one at: https://discord.com/developers/applications",
        f"DISCORD_TOKEN={values.get('DISCORD_TOKEN', '')}",
        "",
        "# [OPTIONAL] Spotify Integration",
        "# Get these at: https://developer.spotify.com/dashboard",
        f"SPOTIFY_CLIENT_ID={values.get('SPOTIFY_CLIENT_ID', '')}",
        f"SPOTIFY_CLIENT_SECRET={values.get('SPOTIFY_CLIENT_SECRET', '')}",
        "",
        "# [OPTIONAL] Genius Lyrics",
        "# Get one at: https://genius.com/api-clients",
        f"GENIUS_TOKEN={values.get('GENIUS_TOKEN', '')}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")


def _load_existing_env(env_path: Path) -> dict:
    """Load existing .env values."""
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    return values


def run_wizard(console: Console, project_root: Path) -> bool:
    """
    Run the interactive setup wizard.
    Returns True if configuration was completed, False if user cancelled.
    """
    env_path = project_root / ".env"
    existing = _load_existing_env(env_path)

    # If .env exists and has a token, ask if reconfiguration is wanted
    if existing.get("DISCORD_TOKEN") and len(existing["DISCORD_TOKEN"]) > 10:
        console.print()
        token = existing["DISCORD_TOKEN"]
        masked = token[:6] + "*" * 20 + token[-4:]
        panel = Panel(
            Text.from_markup(
                f"  [bold {BLUE_LIGHT}]A configuration already exists.[/]\n\n"
                f"  [{GRAY}]Current token: {masked}[/]\n\n"
                f"  [bold {BLUE_ICE}]Do you want to reconfigure? (y/N)[/]"
            ),
            border_style=BLUE_NAVY,
            title=Text(f" {ICON_GEAR} Existing Configuration ", style=f"bold {BLUE_LIGHT}"),
            padding=(1, 2),
        )
        console.print(Align.center(panel, width=70))
        console.print()

        try:
            answer = console.input(f"  [{BLUE_ICE}]>[/] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return True

        if answer not in ("y", "yes", "o", "oui"):
            return True  # Keep existing config

    values = {}
    total = len(FIELDS)

    for i, field in enumerate(FIELDS, 1):
        while True:
            console.clear()
            console.print("\n")
            console.print(Align.center(_build_field_panel(field, i, total), width=70))
            console.print()

            try:
                raw = console.input(f"  [bold {BLUE_ICE}]>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print(f"\n  [{RED}]Setup cancelled.[/]\n")
                return False

            # Allow skipping optional fields
            if not raw and not field["required"]:
                values[field["key"]] = ""
                break

            # Required field must have a value
            if not raw and field["required"]:
                console.print(f"\n  [{RED}]  [{ICON_CROSS}] This field is required![/]")
                console.input(f"  [{GRAY}]Press Enter to try again...[/]")
                continue

            # Validate
            if field["validate"] and not field["validate"](raw):
                console.print(f"\n  [{YELLOW}]  [{ICON_CROSS}] {field['error_msg']}[/]")
                console.print(f"  [{GRAY}]Value entered: {raw[:20]}{'...' if len(raw) > 20 else ''}[/]")
                console.input(f"\n  [{GRAY}]Press Enter to try again (or paste the correct value)...[/]")
                continue

            values[field["key"]] = raw
            break

    # Show summary
    console.clear()
    console.print("\n")
    console.print(Align.center(_build_summary_panel(values), width=70))
    console.print()

    # Confirm save
    console.print(Align.center(
        Text.from_markup(f"  [bold {BLUE_ICE}]Save this configuration? (Y/n)[/]"),
        width=70
    ))
    console.print()

    try:
        confirm = console.input(f"  [bold {BLUE_ICE}]>[/] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        confirm = "y"

    if confirm in ("", "y", "yes", "o", "oui"):
        _save_env(values, env_path)
        console.print(f"\n  [bold {GREEN}][{ICON_CHECK}] Configuration saved successfully![/]\n")
        import time
        time.sleep(1)
        return True
    else:
        console.print(f"\n  [{RED}][{ICON_CROSS}] Configuration not saved.[/]\n")
        return False
