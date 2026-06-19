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

# ─── FFmpeg Installer ─────────────────────────────────────────────────────────

FFMPEG_URL = "https://files.catbox.moe/j21oqj.7z"
FFMPEG_ARCHIVE = "ffmpeg.7z"


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
    ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return (project_root / "bin" / ffmpeg_name).exists()


def _find_7zip() -> str | None:
    """Find a 7-Zip executable on the system."""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", ""), "7-Zip", "7z.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "7-Zip", "7z.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _install_ffmpeg(console: Console, project_root: Path) -> bool:
    """
    Download and install FFmpeg with animated progress bars.
    Returns True on success, False on failure.
    """
    is_windows = os.name == "nt"
    ffmpeg_url = FFMPEG_URL if is_windows else "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    archive_path = project_root / (FFMPEG_ARCHIVE if is_windows else "ffmpeg.tar.xz")

    bin_dir = project_root / "bin"
    bin_dir.mkdir(exist_ok=True)
    seven_zr = bin_dir / "7zr.exe"

    console.print()

    # ── Step 1: Download FFmpeg archive ───────────────────────────────────
    header = Panel(
        Text.from_markup(
            f"  [bold {BLUE_LIGHT}]FFmpeg is required for music playback.[/]\n"
            f"  [{GRAY}]This is a one-time setup step.[/]\n\n"
            f"  [{BLUE_ICE}]{ICON_ARROW} Downloading from: [{GRAY}]{ffmpeg_url}[/][/]"
        ),
        border_style=BLUE_NAVY,
        title=Text(f" {ICON_SPARK} FFmpeg Installation {ICON_SPARK} ", style=f"bold {BLUE_LIGHT}"),
        padding=(1, 2),
    )
    console.print(Align.center(header, width=80))
    console.print()

    try:
        req = urllib.request.Request(ffmpeg_url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

        with urllib.request.urlopen(req, timeout=120) as response:
            total_size = int(response.headers.get("Content-Length", 0))

            with Progress(
                SpinnerColumn(style=BLUE_LIGHT, spinner_name="dots"),
                TextColumn(f"[bold {BLUE_ICE}]Downloading FFmpeg[/]"),
                BarColumn(bar_width=40, style=BLUE_NAVY, complete_style=BLUE_LIGHT, finished_style=GREEN, pulse_style=BLUE_LIGHT),
                TextColumn(f"[bold {BLUE_ICE}]" + "{task.percentage:>3.0f}%" + "[/]"),
                console=console,
                expand=False,
            ) as progress:
                task = progress.add_task("download", total=total_size or None)

                with open(archive_path, "wb") as f:
                    chunk_size = 65536
                    downloaded = 0
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress.update(task, completed=downloaded)

            # Update total if we didn't know it
            if total_size == 0:
                progress.update(task, total=downloaded, completed=downloaded)

    except Exception as e:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Download failed: {e}[/]")
        if archive_path.exists():
            archive_path.unlink()
        return False

    # Verify archive size (must be > 1 MB)
    if archive_path.stat().st_size < 1_048_576:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Downloaded file is too small -- server may have returned an error page.[/]")
        archive_path.unlink()
        return False

    console.print(f"  [bold {GREEN}][{ICON_CHECK}] Download complete![/]\n")
    time.sleep(0.3)

    # ── Step 2: Extract FFmpeg ────────────────────────────────────────────
    with Progress(
        SpinnerColumn(style=BLUE_LIGHT, spinner_name="dots"),
        TextColumn(f"[bold {BLUE_ICE}]Extracting FFmpeg...[/]"),
        BarColumn(bar_width=40, style=BLUE_NAVY, complete_style=BLUE_LIGHT, finished_style=GREEN, pulse_style=BLUE_LIGHT),
        console=console,
    ) as progress:
        task = progress.add_task("extract", total=None)

        extracted = False

        if is_windows:
            # Try 1: System 7-Zip
            seven_zip = _find_7zip()
            if seven_zip:
                result = subprocess.run(
                    [seven_zip, "x", str(archive_path), f"-o{project_root}", "-y"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    extracted = True

            # Try 2: Download 7zr.exe standalone
            if not extracted:
                if not seven_zr.exists():
                    progress.update(task, description=f"[bold {BLUE_ICE}]Downloading 7zr.exe extractor...[/]")
                    try:
                        req = urllib.request.Request("https://www.7-zip.org/a/7zr.exe")
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            with open(seven_zr, "wb") as f:
                                f.write(resp.read())
                    except Exception:
                        pass

                if seven_zr.exists():
                    progress.update(task, description=f"[bold {BLUE_ICE}]Extracting with 7zr.exe...[/]")
                    result = subprocess.run(
                        [str(seven_zr), "x", str(archive_path), f"-o{project_root}", "-y"],
                        capture_output=True, text=True,
                    )
                    if result.returncode == 0:
                        extracted = True

            # Try 3: tar (Windows 11)
            if not extracted:
                progress.update(task, description=f"[bold {BLUE_ICE}]Trying tar extraction...[/]")
                result = subprocess.run(
                    ["tar", "-xf", str(archive_path), "-C", str(project_root)],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    extracted = True
        else:
            # Linux: Extract tar.xz directly via python
            try:
                import tarfile
                with tarfile.open(archive_path, 'r:xz') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith("/ffmpeg") and member.isfile():
                            f_in = tar.extractfile(member)
                            if f_in:
                                with open(bin_dir / "ffmpeg", 'wb') as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                                extracted = True
                                break
            except Exception:
                pass

        progress.update(task, total=1, completed=1 if extracted else 0)

    if not extracted:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Could not extract FFmpeg.[/]")
        if is_windows:
            console.print(f"  [{GRAY}]Please install 7-Zip from https://www.7-zip.org/ and try again.[/]")
        archive_path.unlink(missing_ok=True)
        return False

    console.print(f"  [bold {GREEN}][{ICON_CHECK}] Extraction complete![/]\n")
    time.sleep(0.3)

    # ── Step 3: Move ffmpeg to bin/ ───────────────────────────────────────
    found = False
    if is_windows:
        with Progress(
            SpinnerColumn(style=BLUE_LIGHT, spinner_name="dots"),
            TextColumn(f"[bold {BLUE_ICE}]Locating ffmpeg.exe...[/]"),
            console=console,
        ) as progress:
            task = progress.add_task("locate", total=None)

            for root, dirs, files in os.walk(str(project_root)):
                for fname in files:
                    if fname.lower() == "ffmpeg.exe":
                        src = os.path.join(root, fname)
                        dst = str(bin_dir / "ffmpeg.exe")
                        if os.path.normcase(src) != os.path.normcase(dst):
                            shutil.copy2(src, dst)
                            found = True
                            break
                if found:
                    break

            progress.update(task, total=1, completed=1 if found else 0)
    else:
        # On Linux, we just extracted it directly to bin/ffmpeg, now we make it executable
        os.chmod(bin_dir / "ffmpeg", 0o755)
        found = (bin_dir / "ffmpeg").exists()

    if not found:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Could not configure ffmpeg executable.[/]")
        archive_path.unlink(missing_ok=True)
        return False

    # ── Step 4: Cleanup ───────────────────────────────────────────────────
    with Progress(
        SpinnerColumn(style=BLUE_LIGHT, spinner_name="dots"),
        TextColumn(f"[bold {BLUE_ICE}]Cleaning up...[/]"),
        console=console,
    ) as progress:
        task = progress.add_task("cleanup", total=None)

        archive_path.unlink(missing_ok=True)
        # Remove extracted ffmpeg directories
        for item in project_root.iterdir():
            if item.is_dir() and item.name.startswith("ffmpeg-"):
                shutil.rmtree(item, ignore_errors=True)

        progress.update(task, total=1, completed=1)

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
        success = _install_ffmpeg(console, project_root)

        if success and _check_ffmpeg(project_root):
            steps[1] = ("FFmpeg audio engine", "ok")
        else:
            ffmpeg_exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
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
