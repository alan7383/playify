#!/usr/bin/env bash
# Playify v3 launcher (full Rust) — Linux/macOS.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  > Playify v3 launcher (full Rust)"
echo

# ─── 1. Discord token ─────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    echo "  No .env found. Let's create one."
    read -rp "  Paste your Discord bot token: " TOKEN
    printf 'DISCORD_TOKEN=%s\nPLAYIFY_RUST_NODE=0\n' "$TOKEN" > .env
    echo "  .env created."
    echo
fi

# ─── 2. yt-dlp (standalone binary, no Python needed) ──────────────────────
YTDLP=""
for candidate in .venv/bin/yt-dlp bin/yt-dlp; do
    [[ -x $candidate ]] && YTDLP="$candidate" && break
done
if [[ -z $YTDLP ]] && command -v yt-dlp >/dev/null; then
    YTDLP="yt-dlp"
fi
if [[ -z $YTDLP ]]; then
    echo "  yt-dlp not found: downloading the standalone build to bin/ ..."
    mkdir -p bin
    curl -fL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" -o bin/yt-dlp
    chmod +x bin/yt-dlp
    YTDLP="bin/yt-dlp"
    echo "  yt-dlp installed in bin/."
fi
export PLAYIFY_YTDLP="$(pwd)/$YTDLP"

# ─── 3. Get the bot binary ─────────────────────────────────────────────────
# Priority: local build > bin/ copy > prebuilt download > build from source.
BOT=playify-rs/target/release/playify-v3
[[ ! -x $BOT && -x bin/playify-v3 ]] && BOT=bin/playify-v3
if [[ ! -x $BOT ]]; then
    echo "  Downloading the prebuilt Playify v3 binary..."
    mkdir -p bin
    if curl -fL "https://github.com/alan7383/playify/releases/latest/download/playify-v3-linux-x86_64" -o bin/playify-v3 2>/dev/null; then
        chmod +x bin/playify-v3
        BOT=bin/playify-v3
        echo "  Prebuilt binary installed in bin/."
    else
        rm -f bin/playify-v3
        echo "  No prebuilt binary available: falling back to building from source."
    fi
fi
if [[ ! -x $BOT ]]; then
    echo "  Building playify-v3 (first build takes a few minutes)..."
    if ! command -v cargo >/dev/null; then
        echo "  [!] Rust is not installed. Install it with:"
        echo "      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        echo "  then run this script again."
        exit 1
    fi
    if ! command -v cmake >/dev/null; then
        echo "  [!] CMake is required (Opus build). Install it, e.g.:"
        echo "      sudo apt install cmake   # Debian/Ubuntu"
        echo "      brew install cmake       # macOS"
        exit 1
    fi
    export CMAKE_POLICY_VERSION_MINIMUM=3.5
    (cd playify-rs && cargo build --release -p playify-v3)
fi

# ─── 4. Launch with the TUI dashboard ─────────────────────────────────────
echo "  Starting Playify v3 dashboard... (Q quits)"
exec "$BOT" --tui
