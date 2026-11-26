#!/bin/bash
# Installs Navi on a Mac without the Gatekeeper quarantine flag, then asks for your keys.
# Usage:     curl -fsSL https://raw.githubusercontent.com/Tboy662/Navi-Ai/main/install-mac.sh | bash
# Uninstall: bash "$HOME/Library/Application Support/Navi/app/install-mac.sh" --uninstall   (add --keep-keys to keep .env)
# A different branch: NAVI_REF=my-branch, piped the same way.
#
# Navi runs from source on a Mac (a Mac app can't be built on Windows): this puts the code and its own Python
# packages in ~/Library/Application Support/Navi and makes ~/Applications/Navi.app to open it.
set -euo pipefail

REPO="Tboy662/Navi-Ai"
NAVI_REF="${NAVI_REF:-main}"
HOME_DIR="$HOME/Library/Application Support/Navi"
APP_DIR="$HOME_DIR/app"      # code, .env and Navi's own data (settings, notes, voice recordings)
VENV="$HOME_DIR/venv"         # Navi's Python packages, apart from everything else on the Mac
APP="$HOME/Applications/Navi.app"

quit_navi() {
    osascript -e 'quit app "Navi"' 2>/dev/null || true
    pkill -f "$APP_DIR/voice.py" 2>/dev/null || true
}

ask() {  # works when piped from curl too: the answer comes from the keyboard, not the pipe
    local reply=""
    read -r -p "$1 [y/N] " reply </dev/tty || true
    [[ "$reply" =~ ^[Yy] ]]
}

if [[ "${1:-}" == "--uninstall" ]]; then
    ask "Remove Navi, its app and its packages?" || { echo "Nothing removed."; exit 0; }
    quit_navi
    rm -rf "$APP" "$VENV"
    if [[ "${2:-}" == "--keep-keys" ]]; then
        find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name ".env" ! -name "navi_*.json" ! -name "voice_profile" \
             ! -name "google_*.json" -exec rm -rf {} +
        echo "Navi was removed. Your keys and settings were kept in $APP_DIR"
    else
        rm -rf "$HOME_DIR"
        echo "Navi was removed."
    fi
    exit 0
fi

# 1. what Navi needs: Python 3.10+ with tkinter (python.org's installer has it), and PortAudio for the microphone
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$(command -v "$candidate")"; break
    fi
done
if [[ -z "$PY" ]]; then
    echo "Navi needs Python 3.10 or newer with tkinter. Install it from https://www.python.org/downloads/macos/"
    echo "then run this installer again."
    open "https://www.python.org/downloads/macos/" 2>/dev/null || true
    exit 1
fi
if command -v brew >/dev/null 2>&1; then
    brew list portaudio >/dev/null 2>&1 || { echo "Installing PortAudio (for the microphone)..."; brew install portaudio; }
elif [[ ! -e /opt/homebrew/lib/libportaudio.dylib && ! -e /usr/local/lib/libportaudio.dylib ]]; then
    echo "Navi needs PortAudio for the microphone. Install Homebrew from https://brew.sh, then run this again."
    exit 1
fi

# 2. the code: this folder if the script sits next to voice.py, otherwise the GitHub download
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$HERE" && -f "$HERE/voice.py" && "$HERE" != "$APP_DIR" ]]; then
    SRC="$HERE"
else
    echo "Downloading Navi ($NAVI_REF)..."
    curl -fL --progress-bar -o "$TMP/navi.tar.gz" "https://github.com/$REPO/archive/$NAVI_REF.tar.gz"
    mkdir "$TMP/src"
    tar -xzf "$TMP/navi.tar.gz" -C "$TMP/src" --strip-components 1
    SRC="$TMP/src"
fi

quit_navi
echo "Installing to $APP_DIR..."
mkdir -p "$APP_DIR"
# copies the code over the old version. Your .env and Navi's data files aren't in the download, so they're kept
(cd "$SRC" && tar -cf - --exclude .git --exclude .env --exclude 'navi_*.json' --exclude dist --exclude build .) | \
    (cd "$APP_DIR" && tar -xf -)
xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

# 3. Navi's packages, in their own virtual environment
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
echo "Installing Navi's Python packages (the first time takes a minute)..."
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"
xattr -dr com.apple.quarantine "$VENV" 2>/dev/null || true

# 4. Navi.app: a small launcher, so Navi opens from Launchpad and macOS asks for the microphone in its name
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Navi</string>
  <key>CFBundleDisplayName</key><string>Navi</string>
  <key>CFBundleIdentifier</key><string>io.github.tboy662.navi</string>
  <key>CFBundleExecutable</key><string>Navi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>NSMicrophoneUsageDescription</key><string>Navi listens for its name and your commands.</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/Navi" <<LAUNCH
#!/bin/bash
cd "$APP_DIR"
exec "$VENV/bin/python" "$APP_DIR/voice.py" "\$@"
LAUNCH
chmod +x "$APP/Contents/MacOS/Navi"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
# ad-hoc signature, so macOS doesn't reject a missing one
codesign --force --deep --sign - "$APP" 2>/dev/null || true

# 5. the keys: a window walks you through each one and writes $APP_DIR/.env (only you can read it)
echo "Opening key setup..."
"$VENV/bin/python" "$APP_DIR/navi_setup.py" --keys --dir "$APP_DIR" || echo "Key setup closed. Run it later with: open -a Navi --args --keys"

echo "Installed: $APP"
echo "Change keys later: open -a Navi --args --keys"
echo "Uninstall: bash \"$APP_DIR/install-mac.sh\" --uninstall"
open "$APP"
