"""Playify TUI — Main entry point (python -m src.tui)."""

import sys
import os
import time
import shutil
import subprocess
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    DownloadColumn, TransferSpeedColumn, TimeRemainingColumn,
)
from .theme import (
    PLAYIFY_THEME, ICON_CHECK, ICON_CROSS, ICON_SPARK, ICON_ROCKET, ICON_ARROW,
    BLUE, BLUE_NAVY, BLUE_LIGHT, BLUE_ICE, GREEN, RED, YELLOW, GRAY, GRAY_DARK, WHITE
)
from .splash import show_splash, show_setup_progress
from .wizard import run_wizard
from .bot_process import BotProcess
from .dashboard import run_dashboard
from .platform_utils import is_windows, install_ffmpeg, get_ffmpeg_executable_name

def _find_python() -> str:
    """Find the correct Python executable (prefer venv)."""
    project_root = Path(__file__).resolve().parents[2]
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _check_env(project_root: Path) -> bool:
    """Check if .env exists and has a Discord token."""
    env_path = project_root / ".env"
    if not env_path.exists():
        return False
    content = env_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("DISCORD_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if token and len(token) > 10 and token != "your_token_here":
                return True
    return False


def _check_ffmpeg(project_root: Path) -> bool:
    """Check if FFmpeg is available."""
    ffmpeg_name = get_ffmpeg_executable_name()
    return (project_root / "bin" / ffmpeg_name).exists()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Main TUI entry point."""
    console = Console(theme=PLAYIFY_THEME, highlight=False)

    # Enable virtual terminal processing on Windows for ANSI colors
    if is_windows():
        os.system("")  # Triggers VT100 mode on Windows 10+pleted=1)

    console.print(f"\n  [bold {GREEN}][{ICON_CHECK}] FFmpeg installed successfully![/]\n")
    time.sleep(1)
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Main TUI entry point."""
    console = Console(theme=PLAYIFY_THEME, highlight=False)

    # Enable virtual terminal processing on Windows for ANSI colors
    if os.name == "nt":
        os.system("")  # Triggers VT100 mode on Windows 10+

    project_root = Path(__file__).resolve().parents[2]

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1: Animated Splash Screen
    # ═══════════════════════════════════════════════════════════════════════
    show_splash(console)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1.5: Auto-Updater Check
    # ═══════════════════════════════════════════════════════════════════════
    from .updater import check_for_updates, run_update_wizard
    has_update, latest_sha, commit_msg = check_for_updates(project_root)
    if has_update:
        should_restart = run_update_wizard(console, project_root, latest_sha, commit_msg)
        if should_restart:
            sys.exit(0)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Pre-flight checks with visual progress
    # ═══════════════════════════════════════════════════════════════════════
    steps = [
        ("Python environment", None),
        ("FFmpeg audio engine", None),
        ("Bot configuration (.env)", None),
    ]

    # Check 1: Python
    steps[0] = ("Python environment", "work")
    show_setup_progress(console, steps)
    time.sleep(0.4)

    python_exe = _find_python()
    if python_exe:
        steps[0] = (f"Python environment ({python_exe.split(os.sep)[-1]})", "ok")
    else:
        steps[0] = ("Python environment -- NOT FOUND", "fail")
        show_setup_progress(console, steps)
        console.print(f"\n  [{RED}]Cannot find Python. Please run start.bat first.[/]")
        console.input(f"\n  [{GRAY}]Press Enter to exit...[/]")
        sys.exit(1)

    # Check 2: FFmpeg
    steps[1] = ("FFmpeg audio engine", "work")
    show_setup_progress(console, steps)
    time.sleep(0.4)

    if _check_ffmpeg(project_root):
        steps[1] = ("FFmpeg audio engine", "ok")
    else:
        steps[1] = ("FFmpeg audio engine -- installing...", "work")
        show_setup_progress(console, steps)
        time.sleep(0.5)

        # Launch the animated FFmpeg installer
        console.clear()
        success = install_ffmpeg(console, project_root)

        if success and _check_ffmpeg(project_root):
            steps[1] = ("FFmpeg audio engine", "ok")
        else:
            ffmpeg_exe_name = get_ffmpeg_executable_name()
            steps[1] = ("FFmpeg audio engine -- INSTALL FAILED", "fail")
            show_setup_progress(console, steps)
            console.print(f"\n  [{RED}]FFmpeg could not be installed automatically.[/]")
            console.print(f"  [{GRAY}]Please download FFmpeg manually and place {ffmpeg_exe_name} in the bin/ folder.[/]")
            console.input(f"\n  [{GRAY}]Press Enter to exit...[/]")
            sys.exit(1)

    # Check 3: Configuration
    steps[2] = ("Bot configuration (.env)", "work")
    show_setup_progress(console, steps)
    time.sleep(0.4)

    has_config = _check_env(project_root)
    if has_config:
        steps[2] = ("Bot configuration (.env)", "ok")
    else:
        steps[2] = ("Bot configuration (.env) -- needs setup", "fail")

    show_setup_progress(console, steps)
    time.sleep(0.8)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 3: Configuration Wizard (if needed)
    # ═══════════════════════════════════════════════════════════════════════
    if not has_config:
        success = run_wizard(console, project_root)
        if not success:
            console.print(f"\n  [{RED}]Configuration is required to run Playify.[/]")
            console.input(f"\n  [{GRAY}]Press Enter to exit...[/]")
            sys.exit(1)

        if not _check_env(project_root):
            console.print(f"\n  [{RED}]Discord token is still missing.[/]")
            console.input(f"\n  [{GRAY}]Press Enter to exit...[/]")
            sys.exit(1)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 4: Launch Bot & Dashboard
    # ═══════════════════════════════════════════════════════════════════════
    console.clear()
    console.print(f"\n  [{BLUE_LIGHT}]{ICON_ROCKET} Launching Playify bot...[/]\n")

    bot = BotProcess(project_root, python_exe)
    bot.start()
    time.sleep(1)

    if not bot.is_running:
        console.print(f"\n  [{RED}][{ICON_CROSS}] Failed to start the bot process.[/]")
        for line in bot.get_recent_logs(10):
            console.print(f"  [{RED}]{line}[/]")
        console.input(f"\n  [{GRAY}]Press Enter to exit...[/]")
        sys.exit(1)

    # Main loop: Dashboard <-> Config <-> Restart
    while True:
        action = run_dashboard(console, bot, project_root)

        if action == "quit":
            console.clear()
            console.print(f"\n  [{BLUE_LIGHT}]{ICON_SPARK} Shutting down Playify...[/]")
            bot.stop()
            console.print(f"  [{GREEN}][{ICON_CHECK}] Bot stopped successfully.[/]")
            console.print(f"  [{BLUE_ICE}]Thanks for using Playify! {ICON_SPARK}[/]\n")
            break

        elif action == "config":
            bot.stop()
            console.clear()
            run_wizard(console, project_root)
            console.clear()
            console.print(f"\n  [{BLUE_LIGHT}]{ICON_ROCKET} Relaunching Playify bot...[/]\n")
            bot = BotProcess(project_root, python_exe)
            bot.start()
            time.sleep(1)

        elif action == "settings":
            bot.stop()
            console.clear()
            from .settings_menu import run_settings_menu
            run_settings_menu()
            console.clear()
            console.print(f"\n  [{BLUE_LIGHT}]{ICON_ROCKET} Relaunching Playify bot with new settings...[/]\n")
            bot = BotProcess(project_root, python_exe)
            bot.start()
            time.sleep(1)

        elif action == "restart":
            console.clear()
            console.print(f"\n  [{YELLOW}]{ICON_SPARK} Restarting Playify bot...[/]\n")
            bot.stop()
            time.sleep(1)
            bot = BotProcess(project_root, python_exe)
            bot.start()
            time.sleep(1)
            
        elif action == "update":
            bot.stop()
            console.clear()
            from .updater import check_for_updates, run_update_wizard
            console.print(f"\n  [{BLUE_LIGHT}]{ICON_SPARK} Checking for updates...[/]\n")
            has_update, latest_sha, commit_msg = check_for_updates(project_root)
            if has_update:
                should_restart = run_update_wizard(console, project_root, latest_sha, commit_msg)
                if should_restart:
                    sys.exit(0)
            else:
                console.print(f"  [{GREEN}]Playify is up to date![/]")
                time.sleep(2)
            
            console.clear()
            console.print(f"\n  [{BLUE_LIGHT}]{ICON_ROCKET} Relaunching Playify bot...[/]\n")
            bot = BotProcess(project_root, python_exe)
            bot.start()
            time.sleep(1)


if __name__ == "__main__":
    main()
