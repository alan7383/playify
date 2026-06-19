"""Settings Menu Wizard for Playify."""

import os
import json
from pathlib import Path
import traceback
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.text import Text
from rich.table import Table

# Try to use theme from tui if available
try:
    from .theme import (
        PLAYIFY_THEME, BLUE_ICE, BLUE_LIGHT, BLUE_NAVY, BLUE_DEEP, 
        STEEL, WHITE, GRAY_DARK, ICON_GEAR, ICON_PLAY, BOX_TL, BOX_TR, BOX_BL, BOX_BR, BOX_H, BOX_V
    )
except ImportError:
    from rich.theme import Theme
    PLAYIFY_THEME = Theme({})
    BLUE_ICE, BLUE_LIGHT, BLUE_NAVY, BLUE_DEEP, STEEL, WHITE, GRAY_DARK = "cyan", "blue", "blue", "blue", "grey70", "white", "grey30"
    ICON_GEAR, ICON_PLAY, BOX_TL, BOX_TR, BOX_BL, BOX_BR, BOX_H, BOX_V = "#", ">", "+", "+", "+", "+", "-", "|"

SETTINGS_PATH = Path("data/settings.json")
DEFAULT_SETTINGS = {
    "bot_status_text": "",
    "bot_status_type": 0,  # 0: Playing, 2: Listening, 3: Watching
    "controller_idle_image": "https://i.imgur.com/vDusBWD.png",
}

STATUS_TYPES = {
    0: "Playing",
    2: "Listening",
    3: "Watching",
    5: "Competing"
}

def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            settings = DEFAULT_SETTINGS.copy()
            settings.update(data)
            return settings
    except Exception:
        return DEFAULT_SETTINGS.copy()

def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        print(f"Failed to save settings: {e}")

def clear_screen(console: Console):
    console.clear()
    
from rich.align import Align

def print_banner(console: Console):
    header_text = Text()
    header_text.append(f"  {ICON_GEAR} ", style=f"bold {BLUE_ICE}")
    header_text.append("PLAYIFY", style=f"bold {BLUE_LIGHT}")
    header_text.append(" SETTINGS", style=f"bold {BLUE_ICE}")
    header_text.append(f"  {ICON_GEAR}", style=f"bold {BLUE_ICE}")

    subtitle = Text(" Advanced Configuration ", style=f"bold {GRAY_DARK}")

    panel = Panel(
        Align.center(header_text),
        border_style=BLUE_NAVY,
        padding=(0, 0),
        subtitle=subtitle,
    )
    console.print(panel)

def draw_menu(console: Console, settings: dict):
    clear_screen(console)
    print_banner(console)
    
    from rich import box
    table = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 3))
    table.add_column("Key", style=f"bold {BLUE_LIGHT}", justify="right")
    table.add_column("Description", style=STEEL)
    table.add_column("Current Value", style=f"bold {WHITE}")
    
    st_type = STATUS_TYPES.get(settings["bot_status_type"], "Unknown")
    st_text = settings["bot_status_text"] if settings["bot_status_text"] else f"[{GRAY_DARK}]<None>[/]"
    
    table.add_row(f"[{BLUE_ICE}][ 1 ][/]", "Bot Status Text", st_text)
    table.add_row(f"[{BLUE_ICE}][ 2 ][/]", "Bot Status Type", f"{st_type} (Code {settings['bot_status_type']})")
    table.add_row(f"[{BLUE_ICE}][ 3 ][/]", "Controller Idle Image URL", settings["controller_idle_image"])
    table.add_row("", "", "")
    table.add_row(f"[{BLUE_ICE}][ 0 ][/]", "Return to Dashboard", "")
    
    panel = Panel(
        table,
        title=f"[bold {WHITE}] {ICON_GEAR} Configuration Menu [/]",
        border_style=BLUE_NAVY,
        padding=(1, 4)
    )
    console.print(panel, justify="center")

def run_settings_menu():
    console = Console(theme=PLAYIFY_THEME)
    settings = load_settings()
    
    while True:
        draw_menu(console, settings)
        
        console.print()
        choice = Prompt.ask(f"  [bold {BLUE_ICE}]{ICON_PLAY} Select an option[/]", choices=["1", "2", "3", "0"], default="0")
        
        if choice == "0":
            break
            
        elif choice == "1":
            console.print(f"\n  [{STEEL}]Current text:[/] {settings['bot_status_text'] or '<None>'}")
            console.print(f"  [{GRAY_DARK}](To remove the status completely, just type the word 'none' or leave empty)[/]")
            new_val = Prompt.ask(f"  [bold {BLUE_LIGHT}]Enter new status text[/]")
            if new_val.strip().lower() == "none" or new_val == "":
                settings["bot_status_text"] = ""
            else:
                settings["bot_status_text"] = new_val.strip()
            save_settings(settings)
                
        elif choice == "2":
            console.print(f"\n  [{STEEL}]Available types:[/] 0=Playing, 2=Listening, 3=Watching, 5=Competing")
            new_val = IntPrompt.ask(f"  [bold {BLUE_LIGHT}]Enter new status type[/]", choices=["0", "2", "3", "5"], default=settings['bot_status_type'])
            settings["bot_status_type"] = new_val
            save_settings(settings)
            
        elif choice == "3":
            console.print(f"\n  [{STEEL}]Current URL:[/] {settings['controller_idle_image']}")
            new_val = Prompt.ask(f"  [bold {BLUE_LIGHT}]Enter new image URL (must be a direct link, e.g., .png/.jpg)[/]")
            if new_val.strip() and new_val.startswith("http"):
                settings["controller_idle_image"] = new_val.strip()
                save_settings(settings)
            elif new_val.strip():
                console.print(f"  [bold red]Invalid URL![/] Must start with http/https.", style="error")
                import time
                time.sleep(2)

if __name__ == "__main__":
    run_settings_menu()
