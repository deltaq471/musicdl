@echo off
REM Standalone build: produces a single-file MusicDL.exe for Windows.
cd /d "%~dp0"

where python >nul 2>&1 || (echo python3 is required to build. & pause & exit /b 1)
python -m pip install --quiet pyinstaller yt-dlp
python -m PyInstaller --noconfirm --clean --onefile --name MusicDL downloader_gui.py

echo.
echo Done. The standalone app is: dist\MusicDL.exe
pause
