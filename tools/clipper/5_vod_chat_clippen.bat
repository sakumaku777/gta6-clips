@echo off
cd /d "%~dp0"
where ffmpeg >nul 2>&1 || set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
if not exist TwitchDownloaderCLI.exe echo FEHLT: TwitchDownloaderCLI.exe in diesem Ordner, siehe ANLEITUNG.md & pause & exit /b
set /p VOD=Twitch-VOD-Link (https://www.twitch.tv/videos/...):
python clipper.py chatvod %VOD% --top 8
pause
