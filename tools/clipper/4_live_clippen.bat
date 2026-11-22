@echo off
cd /d "%~dp0"
where ffmpeg >nul 2>&1 || set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
python -c "import streamlink" 2>nul || python -m pip install -q streamlink
set /p KANAL=Twitch-Kanal (laeuft gerade live mit GTA 6):
echo Laeuft bis Streamende oder Strg+C. Clips landen in raw\, danach 2_vorbereiten und 3b_rendern.
python clipper.py live %KANAL%
pause
