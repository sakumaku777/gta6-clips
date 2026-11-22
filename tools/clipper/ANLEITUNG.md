# GTA-Highlight-Clipper: Anleitung (Windows)

Der Clipper findet Highlights ohne KI-Tokens und baut daraus 9:16-Shorts mit Wort-für-Wort-Untertiteln.

## 1. Einmalig installieren (PowerShell)

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
# PowerShell danach neu öffnen
pip install yt-dlp faster-whisper numpy
```

Optional für Chat-Peaks: Lade dir [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader/releases) herunter (die Datei "CLI Windows x64") und entpacke sie in denselben Ordner wie `clipper.py`.

## 2. Highlights holen

**A) Top-Twitch-Clips eines Streamers** (am schnellsten):
```powershell
python clipper.py clips KANALNAME --range 7d --top 10
```
`--range` kann `24hr`, `7d`, `30d` oder `all` sein. Es werden nur Clips bis 60 Sekunden geladen.

**B) Aus einem ganzen VOD, über Chat-Peaks:**
```powershell
.\TwitchDownloaderCLI.exe chatdownload --id 123456789 -o chat.json
python clipper.py vod https://www.twitch.tv/videos/123456789 --chat chat.json --top 8 --length 45
```

**C) Aus einem VOD über Lautstärke-Peaks** (ohne Chat, geht auch mit YouTube):
```powershell
python clipper.py vod https://www.youtube.com/watch?v=XXXX --top 8
```

Alle Rohclips landen im Ordner `raw\`.

## 3. Shorts rendern

```powershell
python clipper.py render
```

Dabei wird jeder Clip transkribiert (Whisper läuft lokal auf der CPU), auf 9:16 gebracht (das Video mittig, dahinter ein unscharfer Hintergrund) und mit gelben Untertiteln versehen, 1 bis 3 Wörter auf einmal. Die fertigen Shorts landen in `out\`.

- Beim ersten Start lädt Whisper sein Modell herunter, etwa 500 MB.
- Wenn es zu langsam ist, nimm `--model base` oder `--model tiny`. Das ist schneller, aber ungenauer.
- `--no-subs` rendert ohne Untertitel.

## Wichtig

Poste nur Content, der zu einer Kampagne passt (Whop/Content Rewards, Vyro usw.), und halte dich an deren Regeln: Watermarks, Credits, erlaubte Streamer.

## 4. Chat-Spike-Clipping (neu, 21.11.2026)

Der Clipper findet die besten Momente selbst: dort, wo der Twitch-Chat explodiert (viele Nachrichten, KEKW, LMAO, CLIP, 💀 ...). Kostet keine KI-Tokens. Es wird immer nur GTA 6 geclippt (Spiel-Check über Twitch). Clips: 15-20 s, der Moment liegt ca. 6 s im Clip, danach die Reaktion. Hält der Chat-Ausbruch länger an, wird das Ende länger, nie abgeschnitten.

**Live** (Streamer ist gerade live): `4_live_clippen.bat` starten, Kanal eingeben. Läuft bis Streamende oder Strg+C. Nimmt den Stream als Ringpuffer auf (nur die letzten 15 min, wenig Speicher) und schneidet jeden Spike sofort nach `raw\`. Braucht `streamlink` (installiert die Bat automatisch). Laptop muss wach bleiben.

**VOD** (Stream ist vorbei): `5_vod_chat_clippen.bat` starten, VOD-Link eingeben. Lädt das Chat-Replay per TwitchDownloaderCLI und dann nur die Spike-Abschnitte, nicht das ganze VOD. Nur die GTA-6-Kapitel zählen.

Danach wie immer: `2_vorbereiten.bat` (Hooks per Gemini) und `3b_shorts_rendern_facecam.bat`.

Feintuning: `--schwelle 3` = mehr (schwächere) Clips, `--schwelle 5` = nur die krassesten. `--nur-liste` zeigt beim VOD nur die gefundenen Zeiten.
