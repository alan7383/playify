"""Platform-specific utilities for Playify."""

import os
import sys
import time
import shutil
import subprocess
import signal
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn

from .theme import BLUE_ICE, BLUE_LIGHT, BLUE_NAVY, GRAY, RED, GREEN, ICON_SPARK, ICON_ARROW, ICON_CROSS, ICON_CHECK


def is_windows() -> bool:
    """Check if the current OS is Windows."""
    return os.name == "nt"


def get_ffmpeg_executable_name() -> str:
    """Return the FFmpeg executable name based on the OS."""
    return "ffmpeg.exe" if is_windows() else "ffmpeg"


import contextlib

def get_keypress() -> str | None:
    """Read a single keypress without echoing (cross-platform), handling arrow keys."""
    if is_windows():
        import msvcrt
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key in (b"\x00", b"\xe0"):
                special = msvcrt.getch()
                if special == b"H": return "UP"
                elif special == b"P": return "DOWN"
                return None
            try:
                return key.decode("utf-8").lower()
            except UnicodeDecodeError:
                return None
        return None
    else:
        import select
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

@contextlib.contextmanager
def terminal_context():
    """Context manager to setup and restore terminal settings (for Linux tty)."""
    if is_windows():
        yield
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                yield
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        else:
            yield


def get_bot_creationflags() -> int:
    """Return the correct creation flags for a subprocess (Windows only)."""
    if is_windows():
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def kill_bot_process(process: subprocess.Popen) -> None:
    """Gracefully kill a subprocess."""
    if is_windows():
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)


def apply_update_and_restart(project_root: Path, source_folder: Path, temp_dir: Path) -> None:
    """Write the update script and execute it to restart Playify."""
    if is_windows():
        updater_script = project_root / "apply_update.bat"
        script_content = f"""@echo off
title Playify Updater
echo Playify is updating... Please wait.
:: Wait 3 seconds to let Python exit completely
ping 127.0.0.1 -n 4 > nul

:: Copy all files, overwriting existing ones. 
xcopy /s /e /y "{source_folder}\\*" "{project_root}\\" > nul

:: Clean up temp files
rmdir /s /q "{temp_dir}" > nul
del "%~f0" > nul

:: Restart Playify
cd /d "{project_root}"
start cmd /c start.bat
exit
"""
        with open(updater_script, "w", encoding="utf-8") as f:
            f.write(script_content)

        subprocess.Popen(
            ["cmd.exe", "/c", str(updater_script)],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    else:
        updater_script = project_root / "apply_update.sh"
        script_content = f"""#!/bin/bash
echo "Playify is updating... Please wait."
cp -r "{source_folder}"/* "{project_root}"/
rm -rf "{temp_dir}"
rm -f "$0"
cd "{project_root}"
exec bash start.sh
"""
        with open(updater_script, "w", encoding="utf-8") as f:
            f.write(script_content)
        os.chmod(updater_script, 0o755)

        os.execv("/bin/bash", ["bash", str(updater_script)])


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


def install_ffmpeg(console: Console, project_root: Path) -> bool:
    """
    Download and install FFmpeg with animated progress bars.
    Returns True on success, False on failure.
    """
    ffmpeg_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" if is_windows() else "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    ffmpeg_archive = "ffmpeg.zip" if is_windows() else "ffmpeg.tar.xz"
    archive_path = project_root / ffmpeg_archive

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

            if total_size == 0:
                progress.update(task, total=downloaded, completed=downloaded)

    except Exception as e:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Download failed: {e}[/]")
        if archive_path.exists():
            archive_path.unlink()
        return False

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

        if is_windows():
            seven_zip = _find_7zip()
            if seven_zip:
                result = subprocess.run(
                    [seven_zip, "x", str(archive_path), f"-o{project_root}", "-y"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    extracted = True

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

            if not extracted:
                progress.update(task, description=f"[bold {BLUE_ICE}]Trying tar extraction...[/]")
                result = subprocess.run(
                    ["tar", "-xf", str(archive_path), "-C", str(project_root)],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    extracted = True
        else:
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
        if is_windows():
            console.print(f"  [{GRAY}]Please install 7-Zip from https://www.7-zip.org/ and try again.[/]")
        archive_path.unlink(missing_ok=True)
        return False

    console.print(f"  [bold {GREEN}][{ICON_CHECK}] Extraction complete![/]\n")
    time.sleep(0.3)

    # ── Step 3: Move ffmpeg to bin/ ───────────────────────────────────────
    found = False
    if is_windows():
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

            progress.update(task, total=1, completed=1)

        # Cleanup extracted folders
        for item in project_root.iterdir():
            if item.is_dir() and item.name.startswith("ffmpeg-master-latest-win64"):
                shutil.rmtree(item, ignore_errors=True)
    else:
        # For Linux, we already extracted directly to bin/ffmpeg
        if (bin_dir / "ffmpeg").exists():
            os.chmod(bin_dir / "ffmpeg", 0o755)
            found = True

    archive_path.unlink(missing_ok=True)

    if not found:
        console.print(f"\n  [bold {RED}][{ICON_CROSS}] Could not find FFmpeg executable after extraction.[/]")
        return False

    console.print(f"  [bold {GREEN}][{ICON_CHECK}] Installation successful! Playify is ready.[/]\n")
    time.sleep(1)
    return True
