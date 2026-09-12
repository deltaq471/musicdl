@echo off
REM Music DL launcher for Windows
REM First run sets up a private Python environment inside this folder.
cd /d "%~dp0"

where python >nul 2>&1 || (echo Python 3 is required. Install it and rerun. & pause & exit /b 1)
python -c "import tkinter" >nul 2>&1 || (echo Tk is missing. Reinstall Python with the "tcl/tk" option checked, then rerun. & pause & exit /b 1)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating private environment... (one time)
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  .venv\Scripts\python.exe -m pip install --quiet yt-dlp
)

.venv\Scripts\python.exe downloader_gui.py
