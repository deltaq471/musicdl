#!/usr/bin/env sh
# Music DL launcher for Linux / BSD / macOS
# First run sets up a private Python environment inside this folder.
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || { echo "Python 3 is required. Install it (on Debian/Ubuntu also: sudo apt install python3-venv python3-tk) then rerun."; exit 1; }

"$PY" -c "import tkinter" 2>/dev/null || { echo "Tk missing. Install python3-tk (Debian/Ubuntu) or X11/Tk for Python, then rerun."; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "First run: creating private environment... (one time)"
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  ./.venv/bin/python -m pip install --quiet yt-dlp
fi

exec ./.venv/bin/python downloader_gui.py