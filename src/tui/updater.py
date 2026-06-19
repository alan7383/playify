"""Playify TUI — Git-less Auto-Updater."""

import json
import os
import sys
import time
import zipfile
import urllib.request
import urllib.error
from pathlib import Path
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn

from .platform_utils import is_windows, apply_update_and_restart
from .theme import BLUE_ICE, BLUE_LIGHT, GREEN, RED, YELLOW, GRAY, WHITE, ICON_SPARK, ICON_ROCKET, BLUE_NAVY

GITHUB_API_URL = "https://api.github.com/repos/alan7383/playify/commits/main"
GITHUB_ZIP_URL = "https://github.com/alan7383/playify/archive/refs/heads/main.zip"

def get_current_version(project_root: Path) -> str | None:
    """Read the current commit SHA from data/version.json."""
    version_file = project_root / "data" / "version.json"
    if version_file.exists():
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("sha")
        except Exception:
            return None
    return None

def check_for_updates(project_root: Path) -> tuple[bool, str | None, str | None]:
    """Check GitHub API for a new commit on the main branch."""
    current_sha = get_current_version(project_root)
    
    try:
        req = urllib.request.Request(GITHUB_API_URL, headers={"User-Agent": "Playify-Updater"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            latest_sha = data.get("sha")
            commit_msg = data.get("commit", {}).get("message", "No description").split("\n")[0]
            
            if not current_sha:
                # If there's no version.json, assume it's the first run and save it, but don't force update.
                save_version(project_root, latest_sha)
                return False, latest_sha, commit_msg
                
            if latest_sha and latest_sha != current_sha:
                return True, latest_sha, commit_msg
                
            return False, latest_sha, commit_msg
    except Exception:
        return False, None, None

def save_version(project_root: Path, sha: str) -> None:
    """Save the new commit SHA."""
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    with open(data_dir / "version.json", "w", encoding="utf-8") as f:
        json.dump({"sha": sha}, f)

def run_update_wizard(console: Console, project_root: Path, latest_sha: str, commit_msg: str) -> bool:
    """Displays the update prompt and handles downloading and extracting."""
    console.clear()
    
    panel = Panel(
        Text.from_markup(
            f"[{BLUE_LIGHT}]{ICON_SPARK} A new version of Playify is available![/]\n\n"
            f"[{GRAY}]Latest Update:[/] [{WHITE}]{commit_msg}[/]\n"
            f"[{GRAY}]Version SHA:[/] [{BLUE_ICE}]{latest_sha[:7]}[/]\n\n"
            f"[{GREEN}]The updater will automatically download and install the new files without touching your settings or data.[ /]"
        ),
        title=f" {ICON_SPARK} Auto-Updater ",
        border_style=BLUE_NAVY,
        padding=(1, 2),
    )
    console.print("\n")
    console.print(panel)
    console.print("\n")
    
    choice = Prompt.ask(f"  [{YELLOW}]Do you want to update now?[/]", choices=["y", "n"], default="y", show_default=True)
    
    if choice.lower() != 'y':
        console.print(f"  [{GRAY}]Update skipped. Continuing to Playify...[/]")
        time.sleep(1)
        return False

    temp_dir = Path(os.environ.get("TEMP", "/tmp")) / "playify_update_tmp"
    zip_path = temp_dir / "playify_main.zip"
    extract_path = temp_dir / "extracted"
    
    # Clean temp dir
    import shutil
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n  [{BLUE_LIGHT}]{ICON_ROCKET} Downloading update...[/]")
    
    # Download with progress
    try:
        req = urllib.request.Request(GITHUB_ZIP_URL, headers={"User-Agent": "Playify-Updater"})
        with urllib.request.urlopen(req) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            
            with Progress(
                TextColumn("  [cyan]{task.description}"),
                BarColumn(complete_style="cyan", finished_style="green"),
                DownloadColumn(),
                TransferSpeedColumn(),
                console=console
            ) as progress:
                
                # If total_size is 0 (GitHub sometimes doesn't send Content-Length for dynamic zips), 
                # we just use a dummy large number or hide the percentage.
                task = progress.add_task("Downloading ZIP", total=total_size if total_size > 0 else None)
                
                with open(zip_path, "wb") as out_file:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        progress.update(task, advance=len(chunk))
                        
    except Exception as e:
        console.print(f"  [{RED}]Failed to download update: {e}[/]")
        time.sleep(3)
        return False

    console.print(f"  [{BLUE_ICE}]Extracting files...[/]")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
    except Exception as e:
        console.print(f"  [{RED}]Failed to extract ZIP: {e}[/]")
        time.sleep(3)
        return False

    # GitHub zips put everything inside a subfolder like "playify-main"
    subfolders = [f for f in extract_path.iterdir() if f.is_dir()]
    if not subfolders:
        console.print(f"  [{RED}]Invalid ZIP structure.[/]")
        time.sleep(3)
        return False
        
    source_folder = subfolders[0]

    # Save new version SHA
    save_version(project_root, latest_sha)

    console.print(f"  [{GREEN}]Preparing to apply update and restart...[/]")
    
    apply_update_and_restart(project_root, source_folder, temp_dir)
    
    return True # Indicates we should exit the current python process
