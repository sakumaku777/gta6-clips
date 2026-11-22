#!/usr/bin/env python3
"""GTA-Highlight-Clipper: Highlights ohne KI-Tokens -> 9:16-Shorts."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Twitch-Titel mit Emojis sollen die Ausgabe nicht abstuerzen lassen (Windows, umgeleitete Ausgabe)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RAW = Path("raw")
OUT = Path("out")


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd)[:200])
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"Fehlt: {tool}. Siehe ANLEITUNG.md")


def safe(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "clip"


GQL_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # oeffentliche Client-ID der Twitch-Webseite


def gql_clips(slugs):
    """Spiel, VOD-ID und VOD-Offset je Clip von gql.twitch.tv (leer bei Fehler)."""
    import urllib.request
    out = {}
    q = "query{clip(slug:%s){game{name} videoOffsetSeconds video{id}}}"
    for i in range(0, len(slugs), 30):
        chunk = slugs[i:i + 30]
        body = json.dumps([{"query": q % json.dumps(s)} for s in chunk]).encode()
        req = urllib.request.Request("https://gql.twitch.tv/gql", data=body,
                                     headers={"Client-ID": GQL_ID,
                                              "Content-Type": "application/json"})
        try:
            res = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception as ex:
            print(f"  Twitch-API nicht erreichbar ({ex}), nutze Fallback")
            return out
        for s, r in zip(chunk, res):
            c = (r.get("data") or {}).get("clip")
            if c:
                out[s] = {"game": (c.get("game") or {}).get("name") or "",
                          "offset": c.get("videoOffsetSeconds"),
                          "vod": (c.get("video") or {}).get("id")}
    return out


def cmd_clips(a):
    import yt_dlp
    from yt_dlp.utils import download_range_func
    RAW.mkdir(exist_ok=True)
    url = f"https://www.twitch.tv/{a.channel}/clips?filter=clips&range={a.range}"
    with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True,
                           "playlistend": a.scan}) as y:
        info = y.extract_info(url, download=False)
    cands = [e for e in info.get("entries") or []
             if a.min_len <= (e.get("duration") or 0) <= a.max_len]
    # Reihenfolge = Twitch-Reihenfolge (meiste Views zuerst)
    gql = gql_clips([e["url"].rsplit("/", 1)[-1] for e in cands])
    for e in cands:
        e.update(gql.get(e["url"].rsplit("/", 1)[-1], {}))
    game = (a.game or "").lower()
    entries = []
    if game:
        print(f"Pruefe bis zu {len(cands)} Clips auf Spiel '{a.game}' ...")
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            for e in cands:
                if "game" in e:  # schnell per Twitch-API
                    cats = [e["game"]] if e["game"] else []
                else:            # Fallback: langsam per yt-dlp
                    try:
                        cats = y.extract_info(e["url"], download=False, process=False).get("categories") or []
                    except Exception as ex:
                        print(f"  ueberspringe {e.get('title')!r}: {ex}")
                        continue
                if any(game in c.lower() for c in cats):
                    e["game"] = cats[0]
                    entries.append(e)
                    if len(entries) >= a.top:
                        break
    else:
        entries = cands[: a.top]
    print(f"{len(entries)} Top-Clips von {a.channel} ({a.range}) gefunden")
    base = {"format": "best[ext=mp4]/best", "quiet": True, "no_warnings": True}
    meta = []
    for i, e in enumerate(entries, 1):
        dur = e.get("duration") or 0
        print(f"[{i}/{len(entries)}] {e.get('title')!r} ({dur}s, {e.get('view_count')} Views)")
        name = str(RAW / f"{safe(a.channel)}_{e.get('id')}.%(ext)s")
        pad = 0
        # kurze Highlights bekommen Vorlauf aus dem VOD, damit man den Kontext sieht
        if a.pad and e.get("vod") and e.get("offset") is not None:
            # Achtung: videoOffsetSeconds markiert das ENDE des Clips (gemessen per Audio-Abgleich)
            clip_start = max(0, e["offset"] - dur)
            s = max(0, clip_start - a.pad)
            try:
                opts = dict(base, outtmpl=name,
                            format="best[height<=1080]/best",
                            download_ranges=download_range_func(None, [(s, e["offset"] + 1)]),
                            force_keyframes_at_cuts=True)
                with yt_dlp.YoutubeDL(opts) as y:
                    y.download([f"https://www.twitch.tv/videos/{e['vod']}"])
                pad = clip_start - s
                print(f"    mit {pad}s Vorlauf aus VOD {e['vod']}")
            except Exception as ex:
                print(f"    VOD nicht verfuegbar ({str(ex)[:80]}), nehme den normalen Clip")
                for p in RAW.glob(f"{safe(a.channel)}_{e.get('id')}.*"):
                    p.unlink()
        if not pad:
            with yt_dlp.YoutubeDL(dict(base, outtmpl=name)) as y:
                y.download([e["url"]])
        meta.append({"id": e.get("id"), "title": e.get("title"), "url": e["url"],
                     "duration": dur, "views": e.get("view_count"), "channel": a.channel,
                     "game": e.get("game"), "pad": pad})
    (RAW / f"{safe(a.channel)}_clips.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def chat_counts(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    comments = data.get("comments", data) if isinstance(data, dict) else data
    counts = {}
    hype = re.compile(r"KEKW|LUL|OMEGALUL|LMAO|POG|W+\b|HOLY|CLIP|\?\?\?", re.I)
    for c in comments:
        t = c.get("content_offset_seconds")
        if t is None:
            continue
        body = ((c.get("message") or {}).get("body") or "")
        w = 2.0 if hype.search(body) else 1.0
        counts[int(t)] = counts.get(int(t), 0) + w
    n = max(counts) + 1 if counts else 0
    return [counts.get(i, 0) for i in range(n)]


def audio_levels(url):
    import numpy as np
    import yt_dlp
    RAW.mkdir(exist_ok=True)
    tmpl = str(RAW / "_vod_audio.%(ext)s")
    with yt_dlp.YoutubeDL({"format": "bestaudio/worst", "outtmpl": tmpl, "quiet": True}) as y:
        info = y.extract_info(url, download=True)
        f = y.prepare_filename(info)
    sr = 8000
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True, check=True)
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
    n = len(x) // sr
    rms = np.sqrt((x[: n * sr].reshape(n, sr) ** 2).mean(axis=1))
    os.remove(f)
    return list(rms)


def pick_peaks(series, top, length, window=20, lead=15):
    n = len(series)
    if n == 0:
        return []
    win = [sum(series[i:i + window]) for i in range(max(1, n - window))]
    order = sorted(range(len(win)), key=lambda i: win[i], reverse=True)
    picks = []
    for i in order:
        start = max(0, i - lead)
        if all(abs(start - s) >= length for s, _ in picks):
            picks.append((start, win[i]))
        if len(picks) >= top:
            break
    return sorted(picks)


def cmd_vod(a):
    import yt_dlp
    from yt_dlp.utils import download_range_func
    RAW.mkdir(exist_ok=True)
    if a.chat:
        series, src = chat_counts(a.chat), "Chat"
    else:
        series, src = audio_levels(a.url), "Lautstaerke"
    peaks = pick_peaks(series, a.top, a.length)
    print(f"{len(peaks)} Highlights per {src} gefunden:")
    for s, score in peaks:
        print(f"  {s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}  score={score:.0f}")
    for s, _ in peaks:
        opts = {"outtmpl": str(RAW / f"vod_%(id)s_{s:06d}.%(ext)s"),
                "format": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
                "download_ranges": download_range_func(None, [(s, s + a.length)]),
                "force_keyframes_at_cuts": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([a.url])


def ass_time(t):
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def clip_meta():
    meta = {}
    for j in RAW.glob("*_clips.json"):
        try:
            for m in json.loads(j.read_text(encoding="utf-8")):
                meta[str(m.get("id"))] = m
        except Exception:
            pass
    return meta


def hook_text(hook):
    """Max. 2 Zeilen, Schrift wird bei langen Hooks kleiner."""
    t = re.sub(r"[{}\\\n\r]", "", hook or "").strip().upper()
    if not t:
        return ""
    words, best = t.split(), None
    for i in range(1, len(words)):  # Zeilenumbruch moeglichst mittig
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if best is None or max(len(a), len(b)) < max(len(best[0]), len(best[1])):
            best = (a, b)
    longest = max(len(x) for x in best) if best else len(t)
    size = 84 if longest <= 18 else 72 if longest <= 22 else 60 if longest <= 27 else 50
    body = "\\N".join(best) if best and len(t) > 16 else t
    return f"{{\\fs{size}}}{body}"


def build_ass(words, path, max_words=3, title=""):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,92,&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1
Style: Hook,Arial Black,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,8,3,2,50,50,1484,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines, grp = [], []
    title = hook_text(title)
    if title:
        # steht im unscharfen Bereich UEBER dem Video (Video beginnt bei y=476), ganze Laenge
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(36000)},Hook,,0,0,0,,{title}")

    def flush():
        if grp:
            text = " ".join(w["w"] for w in grp).upper().replace("{", "").replace("}", "")
            start, end = grp[0]["s"], grp[-1]["e"]
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,"
                         f"{{\\fscx80\\fscy80\\t(0,80,\\fscx100\\fscy100)}}{text}")
            grp.clear()

    for w in words:
        if grp and (len(grp) >= max_words or w["s"] - grp[-1]["e"] > 0.6):
            flush()
        grp.append(w)
        if re.search(r"[.!?,]$", w["w"]):
            flush()
    flush()
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def transcribe(model, video):
    segs, info = model.transcribe(str(video), word_timestamps=True, vad_filter=True)
    words = [{"w": w.word.strip(), "s": w.start, "e": w.end}
             for s in segs for w in (s.words or []) if w.word.strip()]
    return words, info.language


def render(video, words, out, title="", gross=True):
    """gross=True: Gameplay gross (1080x1240, Mitte des Bildes, leicht gezoomt), damit man sieht,
    wie gut GTA 6 aussieht. gross=False: altes Layout mit ganzem 16:9-Bild klein in der Mitte."""
    out.parent.mkdir(exist_ok=True)
    ass = out.with_suffix(".ass")
    n = build_ass(words, ass, title=title) if (words or title) else 0
    if n and gross:
        t = ass.read_text(encoding="utf-8")
        # Hook ueber dem Gameplay (endet bei y=320), Untertitel unten im Gameplay
        t = re.sub(r"^(Style: Hook,(?:[^,]*,){20})\d+", r"\g<1>1600", t, flags=re.M)
        t = re.sub(r"^(Style: Cap,(?:[^,]*,){20})\d+", r"\g<1>430", t, flags=re.M)
        ass.write_text(t, encoding="utf-8")
    if gross:
        fg = ("[b]scale=-2:1240:flags=lanczos,crop=1080:1240,unsharp=3:3:0.4[fg];"
              "[bg][fg]overlay=0:340")
    else:
        fg = "[b]scale=1080:-2:flags=lanczos[fg];[bg][fg]overlay=0:(H-h)/2-180"
    vf = ("[0:v]split[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "boxblur=30:3,eq=brightness=-0.15[bg];" + fg)
    if n:
        vf += f",ass={ass.name}"
    vf += "[v]"
    # immer ab t=0 komplett neu kodieren: kein -ss, keine Keyframe-Schnitte
    run(["ffmpeg", "-v", "error", "-y", "-i", video.resolve(), "-filter_complex", vf,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast",
         "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart",
         out.name], cwd=out.parent)
    return n


FACECAM = Path("facecam.json")


def facecam_box(video):
    """Facecam-Ausschnitt (x, y, w, h als Anteil 0-1) fuer den Streamer aus facecam.json."""
    if not FACECAM.exists():
        return None
    boxes = json.loads(FACECAM.read_text(encoding="utf-8"))
    return boxes.get(video.stem.rsplit("_", 1)[0].lower())


def render_facecam(video, words, out, box, title=""):
    """Hochkant-Layout wie im Beispiel: Facecam oben, Hook-Box auf der Naht, Gameplay unten."""
    out.parent.mkdir(exist_ok=True)
    ass = out.with_suffix(".ass")
    n = build_ass(words, ass, title=title) if (words or title) else 0
    if n:
        t = ass.read_text(encoding="utf-8")
        # Untertitel im Gameplay-Teil, Hook als weisse Box mit schwarzer Schrift auf der Naht (y=640)
        t = re.sub(r"^Style: Cap,.*$", "Style: Cap,Arial Black,84,&H0000E5FF,&H00FFFFFF,&H00000000,"
                   "&H64000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,420,1", t, flags=re.M)
        t = re.sub(r"^Style: Hook,.*$", "Style: Hook,Arial,62,&H00111111,&H00111111,&H00FFFFFF,"
                   "&H00FFFFFF,1,0,0,0,100,100,0,0,3,14,0,8,70,70,430,1", t, flags=re.M)
        ass.write_text(t, encoding="utf-8")
    x, y, w, h = box
    # Facecam hoechstens ca. 1,7-fach vergroessern (sonst matschig), Rand mit unscharfer Kopie fuellen
    vf = ("[0:v]split[a][b];"
          f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},split[ca][cb];"
          "[ca]scale=1080:480:force_original_aspect_ratio=increase,crop=1080:480,"
          "boxblur=20:2,eq=brightness=-0.2[cambg];"
          "[cb]scale=-2:420:flags=lanczos,unsharp=5:5:0.6[camfg];"
          "[cambg][camfg]overlay=(W-w)/2:(H-h)/2,crop=1080:480[cam];"
          "[b]scale=-2:1440:flags=lanczos,crop=1080:1440,unsharp=3:3:0.4[game];"
          "[cam][game]vstack")
    if n:
        vf += f",ass={ass.name}"
    vf += "[v]"
    run(["ffmpeg", "-v", "error", "-y", "-i", video.resolve(), "-filter_complex", vf,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast",
         "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart",
         out.name], cwd=out.parent)
    return n


REVIEW = Path("review.csv")
COLS = ["datei", "behalten", "hook", "twitch_titel", "views", "transkript_kurz",
        "qc", "qc_notiz", "redaktion"]


def raw_files():
    return sorted(p for p in RAW.glob("*.mp4") if not p.name.startswith("_"))


def words_path(video):
    return video.with_suffix(".words.json")


def get_words(video, model_name, cache_only=False, _m={}):
    """Transkript aus dem Cache (raw/*.words.json), sonst Whisper laufen lassen."""
    wp = words_path(video)
    if wp.exists():
        d = json.loads(wp.read_text(encoding="utf-8"))
        return d["words"], d["lang"]
    if cache_only:
        return [], "-"
    if "model" not in _m:
        from faster_whisper import WhisperModel
        print(f"Lade Whisper-Modell '{model_name}' ...")
        _m["model"] = WhisperModel(model_name, device="cpu", compute_type="int8")
    words, lang = transcribe(_m["model"], video)
    wp.write_text(json.dumps({"lang": lang, "words": words}, ensure_ascii=False), encoding="utf-8")
    return words, lang


def read_review():
    import csv
    if not REVIEW.exists():
        return {}
    with REVIEW.open(encoding="utf-8-sig", newline="") as fh:
        return {r["datei"]: r for r in csv.DictReader(fh, delimiter=";")}


def write_review(rows):
    import csv
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def gemini_key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if not k and Path("gemini_key.txt").exists():
        k = Path("gemini_key.txt").read_text(encoding="utf-8").strip()
    return k


def gemini_hook(key, video, transcript, title, lang):
    """Hook-Vorschlag von Gemini (Transkript + 3 kleine Standbilder). None bei Fehler."""
    import base64
    import urllib.request
    d = duration(video)
    parts = [{"text": (
        "You write the on-screen hook for a vertical short made from a GTA V roleplay "
        "Twitch clip. Look at the frames and the transcript and describe in ONE line what "
        "actually happens, so a scrolling viewer instantly gets it and wants to watch. "
        f"Rules: max 8 words, write it in this language: {lang}, punchy, no emojis, "
        "no hashtags, no quotes, no clickbait lies - only what is really in the clip. "
        "Reply with the hook only.\n"
        f"Twitch clip title: {title}\nTranscript: {transcript or '(no speech)'}")}]
    for frac in (0.25, 0.5, 0.75):
        img = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{d * frac:.2f}", "-i", str(video),
                              "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5",
                              "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                             capture_output=True).stdout
        if img:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(img).decode()}})
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0.7,
                                            "maxOutputTokens": 60}}).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{os.environ.get('GEMINI_MODEL', 'gemini-flash-latest')}:generateContent")
    req = urllib.request.Request(url, data=body, headers={"x-goog-api-key": key,
                                                          "Content-Type": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=60).read())
        text = res["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as ex:
        print(f"    Gemini-Fehler: {str(ex)[:120]}")
        return None
    u = res.get("usageMetadata", {})
    print(f"    Gemini: {u.get('promptTokenCount')} rein / "
          f"{u.get('candidatesTokenCount')} raus = {u.get('totalTokenCount')} Tokens")
    return re.sub(r'["“”#*]', "", text).strip().splitlines()[0][:80]


def cmd_prepare(a):
    files = raw_files()
    if not files:
        print("raw/ ist leer. Erst 1_clips_holen.bat ausfuehren.")
        return
    meta, old, key = clip_meta(), read_review(), gemini_key()
    print("Gemini-Hooks: " + ("AN" if key else "aus (kein GEMINI_API_KEY / gemini_key.txt)"))
    rows = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        words, lang = get_words(f, a.model)
        text = " ".join(w["w"] for w in words)
        m = meta.get(f.stem.rsplit("_", 1)[-1], {})
        r = old.get(f.name) or {"datei": f.name, "behalten": "ja", "hook": ""}
        r.update({"twitch_titel": m.get("title", ""), "views": m.get("views", ""),
                  "transkript_kurz": text[:200]})
        if key and not r.get("hook"):
            r["hook"] = gemini_hook(key, f, text, m.get("title", ""), lang) or ""
            if r["hook"]:
                print(f"    Hook: {r['hook']}")
        rows.append(r)
    try:
        write_review(rows)
    except PermissionError:
        sys.exit("review.csv ist noch in Excel offen. Bitte Excel schliessen und nochmal starten.")
    print(f"\n{REVIEW} geschrieben ({len(rows)} Clips). Jetzt in Excel: Spalte 'hook' ausfuellen, "
          "schlechte Clips bei 'behalten' auf 'nein' setzen, speichern, dann rendern.")


def cmd_render(a):
    need("ffmpeg")
    review, meta = read_review(), clip_meta()
    files = []
    for f in raw_files():
        r = review.get(f.name, {})
        if (r.get("behalten") or "ja").strip().lower() in ("nein", "n", "no", "0"):
            print(f"uebersprungen (behalten=nein): {f.name}")
            continue
        if a.force or not (OUT / f.name).exists():
            files.append(f)
    if not files:
        print("Nichts zu tun (raw/ leer oder schon gerendert, sonst --force).")
        return
    for i, f in enumerate(files, 1):
        t0 = time.time()
        print(f"[{i}/{len(files)}] {f.name}")
        words, lang = ([], "-") if a.no_subs else get_words(f, a.model)
        r = review.get(f.name, {})
        title = "" if a.no_title else (r.get("hook") or "").strip() or \
            meta.get(f.stem.rsplit("_", 1)[-1], {}).get("title", "")
        print(f"    Hook: {title or '(keiner)'}")
        box = facecam_box(f) if a.layout == "facecam" else None
        if box:
            print("    Layout: Facecam oben, Gameplay unten")
            n = render_facecam(f, words, OUT / f.name, box, title=title)
        else:
            if a.layout == "facecam":
                print("    keine Facecam-Position in facecam.json, nehme normales Layout")
            n = render(f, words, OUT / f.name, title=title, gross=a.layout != "klein")
        d_in, d_out = duration(f), duration(OUT / f.name)
        print(f"    -> out/{f.name}  ({d_out:.1f}s, {n} Text-Zeilen, Sprache {lang}, "
              f"{time.time() - t0:.0f}s Rechenzeit)")
        if abs(d_in - d_out) > 0.3:
            print(f"    WARNUNG: Laenge passt nicht! raw {d_in:.2f}s vs out {d_out:.2f}s")


# ----------------------------------------------------------------- Chat-Spike-Clipping

def gql(query):
    import urllib.request
    req = urllib.request.Request("https://gql.twitch.tv/gql", data=json.dumps({"query": query}).encode(),
                                 headers={"Client-ID": GQL_ID, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data") or {}


def stream_game(channel):
    """Aktuelles Spiel des Streams ('' = offline, None = Twitch nicht erreichbar)."""
    try:
        u = gql("query{user(login:%s){stream{game{name}}}}" % json.dumps(channel)).get("user") or {}
        return ((u.get("stream") or {}).get("game") or {}).get("name") or ""
    except Exception:
        return None


def vod_info(vid):
    """Kanal, Spiel und Kapitel (Spielwechsel) eines VODs."""
    q = ("query{video(id:%s){owner{login} game{name} lengthSeconds "
         "moments(momentRequestType:VIDEO_CHAPTER_MARKERS){edges{node{positionMilliseconds "
         "durationMilliseconds details{...on GameChangeMomentDetails{game{displayName}}}}}}}}") % json.dumps(vid)
    try:
        v = gql(q).get("video") or {}
    except Exception:
        return {}
    chapters = [{"startMilliseconds": e["node"].get("positionMilliseconds") or 0,
                 "lengthMilliseconds": e["node"].get("durationMilliseconds") or 0,
                 "gameDisplayName": (((e["node"].get("details") or {}).get("game")) or {}).get("displayName", "")}
                for e in ((v.get("moments") or {}).get("edges") or [])]
    return {"channel": (v.get("owner") or {}).get("login"), "game": (v.get("game") or {}).get("name"),
            "length": v.get("lengthSeconds"), "chapters": chapters}


def cmd_chatvod(a):
    """VOD -> Chat-Replay -> Spikes -> nur diese Abschnitte laden (kein ganzes VOD, keine Tokens)."""
    import chatspike as cs
    m = re.search(r"(?:videos/|^v?)(\d{6,})", a.url)
    if not m:
        sys.exit("Bitte eine VOD-URL wie https://www.twitch.tv/videos/123456789 angeben")
    vid = m.group(1)
    RAW.mkdir(exist_ok=True)
    chat = Path(a.chat) if a.chat else RAW / f"_chat_{vid}.json"
    if not chat.exists():
        exe = next((p for p in ("TwitchDownloaderCLI.exe", "TwitchDownloaderCLI") if Path(p).exists()), None)
        if not exe:
            sys.exit("TwitchDownloaderCLI.exe fehlt im Ordner (siehe ANLEITUNG.md) oder --chat angeben.")
        print(f"Lade Chat-Replay von VOD {vid} (kann bei langen Streams ein paar Minuten dauern) ...")
        run([Path(exe).resolve(), "chatdownload", "--id", vid, "-o", chat, "--collision", "Overwrite"])
    msgs, info = cs.load_chat(chat)
    print(f"{len(msgs)} Chat-Nachrichten gelesen")
    meta = vod_info(vid)
    channel = a.kanal or meta.get("channel") or ((info.get("user_name") or info.get("channel") or "vod")
                                                if isinstance(info, dict) else "vod")
    channel = safe(channel.lower())
    ranges = None
    if not a.alle_spiele:
        ranges = cs.gta6_ranges(info, a.game)
        if ranges is None:
            ranges = cs.gta6_ranges(meta, a.game)
        if ranges is None:   # keine Kapitel: dann zaehlt das Spiel des VODs
            g = meta.get("game") or ((info.get("game") or {}).get("name") if isinstance(info.get("game"), dict) else info.get("game")) or ""
            if g and a.game.lower() not in g.lower():
                sys.exit(f"VOD-Spiel ist '{g}', kein GTA 6. Abbruch (sonst --alle-spiele).")
            if not g:
                print("  Spiel des VODs unbekannt, clippe alles (bitte pruefen, dass es GTA 6 ist)")
        elif not ranges:
            sys.exit("In diesem VOD gibt es kein GTA-6-Kapitel. Abbruch (sonst --alle-spiele).")
        else:
            print("GTA-6-Abschnitte: " + ", ".join(f"{cs.fmt(x)}-{cs.fmt(y)}" for x, y in ranges))
    picks = cs.find_spikes(msgs, top=a.top, threshold=a.schwelle, min_rate=a.min_rate, delay=a.delay,
                           vor=a.vor, nach=a.nach, laenge_max=a.max_len, ranges=ranges)
    print(f"{len(picks)} Chat-Spikes gefunden:")
    for p in picks:
        print(f"  {cs.fmt(p['moment'])}  Wert {p['score']:.1f}  ({p['chat_rate']} Nachr./s)  "
              f"Chat: {' | '.join(p['chat'][:4])[:90]}")
    (RAW / f"_spikes_{vid}.json").write_text(json.dumps(picks, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.nur_liste:
        return
    for i, p in enumerate(picks, 1):
        cid = f"v{vid}t{int(p['start']):06d}"
        name = RAW / f"{channel}_{cid}.mp4"
        if name.exists():
            print(f"[{i}/{len(picks)}] schon da: {name.name}")
            continue
        print(f"[{i}/{len(picks)}] lade {cs.fmt(p['start'])}-{cs.fmt(p['end'])}")
        if a.datei:   # lokale Datei statt Download (Test / eigene Aufnahme)
            run(["ffmpeg", "-v", "error", "-y", "-ss", p["start"], "-i", a.datei, "-t", p["end"] - p["start"],
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", name])
        else:
            import yt_dlp
            from yt_dlp.utils import download_range_func
            opts = {"outtmpl": str(name.with_suffix(".%(ext)s")),
                    "format": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
                    "download_ranges": download_range_func(None, [(p["start"], p["end"])]),
                    "force_keyframes_at_cuts": True, "quiet": True, "no_warnings": True}
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([f"https://www.twitch.tv/videos/{vid}"])
        cs.save_meta(channel, {"id": cid, "title": "", "info": f"Chat-Spike {cs.fmt(p['moment'])}",
                               "url": f"https://www.twitch.tv/videos/{vid}?t={int(p['start'])}s",
                               "duration": round(p["end"] - p["start"], 1), "views": "", "channel": channel,
                               "game": a.game, "pad": 0, "quelle": "chat-spike-vod", "spike": p["score"],
                               "chat_rate": p["chat_rate"], "chat": p["chat"]})
    print("Fertig. Weiter wie immer: vorbereiten (Hooks) und rendern.")


def cmd_live(a):
    import chatspike as cs
    channel = a.channel.lower().strip().rsplit("/", 1)[-1]
    cs.watch_live(channel, hours=a.stunden, threshold=a.schwelle, min_rate=a.min_rate, delay=a.delay,
                  vor=a.vor, nach=a.nach, laenge_max=a.max_len, max_clips=a.max_clips, game=a.game,
                  game_check=None if a.alle_spiele else stream_game)


def add_spike_args(x):
    x.add_argument("--schwelle", type=float, default=4.0, help="wie stark der Chat ausrasten muss (kleiner = mehr Clips)")
    x.add_argument("--min-rate", type=float, default=1.0, help="mindestens so viele Nachrichten pro Sekunde")
    x.add_argument("--delay", type=float, default=4, help="Sekunden, die der Chat nach dem Moment reagiert")
    x.add_argument("--vor", type=float, default=6, help="Sekunden vor dem Moment (Pointe frueh im Clip)")
    x.add_argument("--nach", type=float, default=12, help="Sekunden nach dem Moment (Reaktion)")
    x.add_argument("--max-len", type=float, default=20)
    x.add_argument("--game", default="Grand Theft Auto VI")
    x.add_argument("--alle-spiele", action="store_true", help="nicht auf GTA 6 pruefen")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clips")
    c.add_argument("channel")
    c.add_argument("--range", default="7d", choices=["24hr", "7d", "30d", "all"])
    c.add_argument("--top", type=int, default=10)
    c.add_argument("--max-len", type=float, default=35)
    c.add_argument("--min-len", type=float, default=10)
    c.add_argument("--game", default="Grand Theft Auto")
    c.add_argument("--scan", type=int, default=100)
    c.add_argument("--pad", type=int, default=6)
    v = sub.add_parser("vod")
    v.add_argument("url")
    v.add_argument("--chat")
    v.add_argument("--top", type=int, default=8)
    v.add_argument("--length", type=int, default=45)
    cv = sub.add_parser("chatvod", help="VOD ueber Chat-Spikes clippen")
    cv.add_argument("url")
    cv.add_argument("--chat", help="Chat-JSON (sonst automatisch per TwitchDownloaderCLI)")
    cv.add_argument("--kanal")
    cv.add_argument("--top", type=int, default=8)
    cv.add_argument("--datei", help="lokale Videodatei statt Download")
    cv.add_argument("--nur-liste", action="store_true", help="nur Spikes anzeigen, nichts laden")
    add_spike_args(cv)
    lv = sub.add_parser("live", help="laufenden Stream live clippen")
    lv.add_argument("channel")
    lv.add_argument("--stunden", type=float, default=0, help="0 = bis Strg+C / Streamende")
    lv.add_argument("--max-clips", type=int, default=0)
    add_spike_args(lv)
    pr = sub.add_parser("prepare")
    pr.add_argument("--model", default="small")
    r = sub.add_parser("render")
    r.add_argument("--model", default="small")
    r.add_argument("--no-subs", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("--no-title", action="store_true")
    r.add_argument("--layout", default="gross", choices=["gross", "klein", "blur", "facecam"],
                   help="facecam = Facecam oben, Gameplay unten (Position aus facecam.json)")
    a = p.parse_args()
    {"clips": cmd_clips, "vod": cmd_vod, "chatvod": cmd_chatvod, "live": cmd_live, "prepare": cmd_prepare,
     "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
