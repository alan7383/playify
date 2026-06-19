#!/usr/bin/env bash
# Playify V2 - Universal Linux Startup Script

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}  Preparing Playify...${NC}\n"

cd "$(dirname "$0")"
ROOT=$(pwd)

# 1. Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[!] Python 3 is not installed.${NC}"
    echo "Please install it using your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    echo "  Fedora: sudo dnf install python3"
    echo "  Arch: sudo pacman -S python"
    exit 1
fi

# 2. Check Python Version (needs 3.9+)
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
    echo -e "${RED}[!] Playify requires Python 3.9 or newer.${NC}"
    exit 1
}

# 3. Create Virtual Environment
VENV_PATH="$ROOT/.venv"
if [ ! -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    # Check if venv module is installed (Ubuntu/Debian split it)
    if ! python3 -m venv "$VENV_PATH" &> /dev/null; then
        echo -e "${RED}[!] Failed to create virtual environment.${NC}"
        echo "On Ubuntu/Debian, you may need to install the venv package:"
        echo "  sudo apt install python3-venv"
        exit 1
    fi
fi

# 4. Activate Venv and Install Dependencies
source "$VENV_PATH/bin/activate"

echo -e "${BLUE}Checking dependencies...${NC}"
pip install -r requirements.txt -q
# Ensure Playwright browsers are installed
playwright install chromium

# 5. FFmpeg will be handled by the Python TUI directly (downloaded to bin/)

# 6. Launch TUI
echo -e "${GREEN}Launching Playify TUI...${NC}\n"
python3 -m src.tui

echo -e "\n${BLUE}Playify has exited.${NC}"
