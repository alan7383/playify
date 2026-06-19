"""Playify TUI — Visual theme, colors, and style constants."""

from rich.theme import Theme
from rich.style import Style
from rich.text import Text

# ─── Brand Colors ────────────────────────────────────────────────────────────
# Playify palette: deep blues, navy, ice accents
BLUE_DEEP = "#0A1628"
BLUE_DARK = "#0D2137"
BLUE_NAVY = "#1B3A5C"
BLUE = "#2471A3"
BLUE_MID = "#2E86C1"
BLUE_LIGHT = "#5DADE2"
BLUE_ICE = "#85C1E9"
BLUE_PALE = "#AED6F1"
BLUE_ELECTRIC = "#3498DB"
STEEL = "#ABB2B9"
WHITE = "#ECF0F1"
GRAY = "#7F8C8D"
GRAY_DARK = "#566573"
RED = "#E74C3C"
GREEN = "#27AE60"
YELLOW = "#F1C40F"

# ─── Rich Theme ──────────────────────────────────────────────────────────────
PLAYIFY_THEME = Theme(
    {
        "title": Style(color=BLUE_LIGHT, bold=True),
        "subtitle": Style(color=BLUE_ICE),
        "success": Style(color=GREEN, bold=True),
        "error": Style(color=RED, bold=True),
        "warning": Style(color=YELLOW, bold=True),
        "info": Style(color=BLUE_LIGHT),
        "muted": Style(color=GRAY),
        "accent": Style(color=BLUE_ELECTRIC, bold=True),
        "key": Style(color=BLUE_ICE, bold=True),
        "value": Style(color=WHITE),
        "header": Style(color=BLUE_LIGHT, bold=True),
        "border": Style(color=BLUE_NAVY),
        "log.info": Style(color=BLUE_LIGHT),
        "log.warning": Style(color=YELLOW),
        "log.error": Style(color=RED, bold=True),
        "log.debug": Style(color=GRAY),
        "prompt": Style(color=BLUE_ICE, bold=True),
        "input": Style(color=WHITE, bold=True),
        "status.online": Style(color=GREEN, bold=True),
        "status.offline": Style(color=RED),
        "music.title": Style(color=WHITE, bold=True),
        "music.artist": Style(color=BLUE_ICE),
        "music.time": Style(color=BLUE_LIGHT),
        "hotkey": Style(color=BLUE_ICE, bold=True),
        "hotkey.desc": Style(color=GRAY),
    }
)

# ─── Gradient Colors for ASCII Art ───────────────────────────────────────────
GRADIENT_COLORS = [
    "#0D2137",  # Deep navy
    "#14344E",
    "#1B4769",
    "#1F5F8B",
    "#2471A3",
    "#2E86C1",
    "#3498DB",
    "#5DADE2",
    "#85C1E9",
    "#AED6F1",  # Pale ice
]

# ─── Box Characters ──────────────────────────────────────────────────────────
BOX_H = "━"
BOX_V = "┃"
BOX_TL = "┏"
BOX_TR = "┓"
BOX_BL = "┗"
BOX_BR = "┛"
BOX_T = "┳"
BOX_B = "┻"
BOX_L = "┣"
BOX_R = "┫"
BOX_X = "╋"

# ─── Status Icons (ASCII/Unicode only, no emojis) ────────────────────────────
ICON_CHECK = "+"
ICON_CROSS = "x"
ICON_MUSIC = "♪"
ICON_PLAY = ">"
ICON_PAUSE = "||"
ICON_STOP = "[]"
ICON_LOOP = "~"
ICON_VOLUME = "))"
ICON_ONLINE = "*"
ICON_OFFLINE = "o"
ICON_ARROW = "->"
ICON_SPARK = "::"
ICON_WARN = "/!\\"
ICON_GEAR = "#"
ICON_ROCKET = ">>"

VERSION = "2.0.0"
