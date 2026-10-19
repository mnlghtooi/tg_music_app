@echo off
cd /d "%~dp0"
title Music Lab - SoundCloud Search
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -c "import yt_dlp, httpx, h2, imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe launch.py --restart
if errorlevel 1 goto fail
exit /b
:fail
echo Launch failed. Copy the error text above.
pause
