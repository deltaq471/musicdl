#!/usr/bin/env sh
# Standalone build: produces a single-file MusicDL executable.
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "python3 is required to build."; exit 1; }
python3 -m pip install --quiet pyinstaller yt-dlp
python3 -m PyInstaller --noconfirm --clean --onefile --name MusicDL downloader_gui.py

echo ""
echo "Done. The standalone app is: dist/MusicDL"