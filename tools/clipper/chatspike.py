#!/usr/bin/env python3
"""Chat-Spike-Clipping: findet Momente, in denen der Twitch-Chat explodiert, und schneidet sie.

Idee aus dem Skill "clip-stream" (alisadiq-ai/favstash-ai-video-editing-skills), nach Python
portiert und an unsere Regeln angepasst: nur GTA 6, 15-20 s, Pointe in den ersten Sekunden,
keine KI-Tokens. Der Chat wird anonym gelesen, Nutzernamen werden nie gespeichert.

Zwei Wege:
  vod   Chat-Replay eines VODs (TwitchDownloaderCLI-JSON) -> Spikes -> Abschnitte per yt-dlp laden
  live  Stream aufnehmen (Ringpuffer aus kurzen Stuecken) + Live-Chat lesen -> bei Spike sofort schneiden
Die Clips landen in raw/ wie die normalen Twitch-Clips, danach laeuft prepare/render wie immer.
"""
import json
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

RAW = Path("raw")

# Chat-Woerter, die "das war ein Moment" bedeuten (Twitch, auch DE-Chats)
CHAT_HYPE = {
    "clip", "clipit", "kekw", "lul", "lol", "lmao", "lmfao", "pog", "pogchamp", "poggers", "pogu",
    "omegalul", "w", "ww", "www", "l", "monkas", "pepelaugh", "icant", "omg", "wtf", "xd", "xdd",
    "kek", "aware", "sadge", "holy", "bruh", "nein", "digga", "alter", "hahaha", "haha",
}
HYPE_RE = re.compile(r"no way|clip|😂|🤣|💀|🔥|\?\?\?|!!!|^w+$|^ha(ha)+$", re.I | re.U)


# ----------------------------------------------------------------- Chat einlesen

def parse_time(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if 2 <= len(parts) <= 3 and all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        t = 0.0
        for p in parts:
            t = t * 60 + float(p)
        return t
    return None


def load_chat(path):
    """Chat-Log -> ([{t, text}], info). Versteht TwitchDownloaderCLI-JSON (comments + video),
    eine JSON-Liste [{t|time|content_offset_seconds, text|message}] und Textzeilen "[1:02:03] name: text"."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    info = {}
    msgs = []
    if raw[:1] in "[{":
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                info = data.get("video") or {}
                lst = data.get("comments") or data.get("messages") or []
            else:
                lst = data
            for m in lst:
                t = parse_time(m.get("t", m.get("time", m.get("timestamp", m.get("content_offset_seconds")))))
                body = m.get("message")
                body = body.get("body", "") if isinstance(body, dict) else (body or m.get("text") or "")
                if t is not None:
                    msgs.append({"t": t, "text": str(body)})
            return sorted(msgs, key=lambda m: m["t"]), info
        except json.JSONDecodeError:
            pass
    for line in raw.splitlines():
        m = re.match(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\]?\s*(.*)$", line)
        if m and parse_time(m.group(1)) is not None:
            msgs.append({"t": parse_time(m.group(1)), "text": m.group(2)})
    return sorted(msgs, key=lambda m: m["t"]), info


def is_hype(text):
    t = text.lower()
    words = [w for w in re.split(r"[\s,.!?]+", t) if w]
    return any(w in CHAT_HYPE for w in words) or bool(HYPE_RE.search(t.strip()))


def per_second(msgs, length=None):
    """Nachrichten und Hype-Nachrichten pro Sekunde (Index = Sekunde im Video)."""
    n = int(length if length is not None else (max((m["t"] for m in msgs), default=0) + 1))
    count = [0.0] * max(1, n)
    hype = [0.0] * max(1, n)
    for m in msgs:
        s = int(m["t"])
        if 0 <= s < n:
            count[s] += 1
            if is_hype(m["text"]):
                hype[s] += 1
    return count, hype


# ----------------------------------------------------------------- Bewertung

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def score_at(count, hype, i, smooth=5, base=180, skip=10, floor=0.5):
    """Spike-Wert fuer Sekunde i, nur aus der VERGANGENHEIT (damit live und VOD gleich rechnen):
    Chat-Tempo der letzten `smooth` Sekunden gegen Median/MAD der `base` Sekunden davor
    (die letzten `skip` Sekunden ausgenommen), plus Bonus fuer Clip-/Lach-Emotes."""
    lo = max(0, i - smooth + 1)
    rate = _mean(count[lo:i + 1])
    hyp = _mean(hype[lo:i + 1])
    b_hi = max(0, i - skip)
    b_lo = max(0, b_hi - base)
    if b_hi - b_lo < 60:          # zu wenig Vorlauf fuer eine Grundlinie (erste ~70 s)
        return 0.0, rate
    hist = [_mean(count[max(b_lo, j - smooth + 1):j + 1]) for j in range(b_lo, b_hi)]
    med = _median(hist)
    mad = _median([abs(h - med) for h in hist])
    z = (rate - med) / max(floor, 1.4826 * mad)
    share = hyp / rate if rate else 0.0      # Anteil der Hype-Nachrichten
    s = min(6.0, max(0.0, z)) + min(3.0, hyp * 0.5) + 2.0 * share
    return s, rate


def all_scores(count, hype, **kw):
    out, rates = [], []
    for i in range(len(count)):
        s, r = score_at(count, hype, i, **kw)
        out.append(s)
        rates.append(r)
    return out, rates


class SpikeFinder:
    """Fasst Sekunden ueber der Schwelle zu einem Spike zusammen (Start = Beginn des Ausbruchs)."""

    def __init__(self, threshold=4.0, min_rate=1.0, hold=4):
        self.threshold, self.min_rate, self.hold = threshold, min_rate, hold
        self.run = None

    def feed(self, i, s, rate):
        """Gibt einen fertigen Spike zurueck {onset, peak, score, rate} oder None."""
        hot = s >= self.threshold and rate >= self.min_rate
        if hot:
            if self.run is None:
                self.run = {"onset": i, "peak": i, "score": s, "rate": rate, "last": i}
            else:
                if s > self.run["score"]:
                    self.run.update(peak=i, score=s)
                self.run["rate"] = max(self.run["rate"], rate)
                self.run["last"] = i
            return None
        if self.run is not None and i - self.run["last"] > self.hold:
            done, self.run = self.run, None
            return done
        return None


def window_for(spike, delay=4, vor=6, nach=12, laenge_max=20, laenge_min=15):
    """Clip-Fenster zu einem Spike. Der Chat reagiert ~`delay` s nach dem Moment.
    Der Moment liegt `vor` s im Clip (Pointe in den ersten Sekunden), danach `nach` s Reaktion.
    Haelt der Chat-Ausbruch laenger an, waechst das Ende mit (nie Reaktion/Ende abschneiden),
    aber hoechstens bis `laenge_max`."""
    moment = spike["onset"] - delay
    start = max(0.0, moment - vor)
    end = moment + nach
    end = max(end, spike["last"] - delay + 3)          # solange der Chat noch tobt
    end = min(end, start + laenge_max)
    if end - start < laenge_min:
        end = start + laenge_min
    return round(start, 2), round(end, 2), round(moment, 2)


def find_spikes(msgs, length=None, top=8, threshold=4.0, min_rate=1.0, delay=4,
                vor=6, nach=12, laenge_max=20, laenge_min=15, ranges=None):
    """Offline (VOD): alle Spikes finden, nach Staerke sortieren, ohne Ueberlappung."""
    count, hype = per_second(msgs, length)
    scores, rates = all_scores(count, hype)
    finder = SpikeFinder(threshold, min_rate)
    spikes = []
    for i, (s, r) in enumerate(zip(scores, rates)):
        sp = finder.feed(i, s, r)
        if sp:
            spikes.append(sp)
    sp = finder.feed(len(scores) + 99, 0, 0)
    if sp:
        spikes.append(sp)
    picks = []
    for sp in sorted(spikes, key=lambda x: -x["score"]):
        st, en, mo = window_for(sp, delay, vor, nach, laenge_max, laenge_min)
        if ranges is not None and not any(a <= mo <= b for a, b in ranges):
            continue
        if any(st < p["end"] + 3 and en > p["start"] - 3 for p in picks):
            continue
        sample = [m["text"] for m in msgs if sp["onset"] <= m["t"] <= sp["onset"] + 6][:8]
        picks.append({"start": st, "end": en, "moment": mo, "score": round(sp["score"], 2),
                      "chat_rate": round(sp["rate"], 1), "chat": sample})
        if len(picks) >= top:
            break
    return sorted(picks, key=lambda p: -p["score"])


def fmt(t):
    t = int(max(0, t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def gta6_ranges(info, game="Grand Theft Auto VI"):
    """Zeitbereiche (s) der VOD-Kapitel mit GTA 6. None = keine Kapitel-Infos vorhanden."""
    chapters = (info or {}).get("chapters") or []
    if not chapters:
        return None
    out = []
    for c in chapters:
        name = c.get("gameDisplayName") or c.get("description") or ""
        if game.lower() in name.lower():
            s = (c.get("startMilliseconds") or 0) / 1000
            out.append((s, s + (c.get("lengthMilliseconds") or 0) / 1000))
    return out


# ----------------------------------------------------------------- Live: Twitch-Chat (anonym)

class TwitchChat(threading.Thread):
    """Liest den oeffentlichen Chat anonym (justinfan). Speichert nur (Zeit, Text), keine Namen."""

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.channel = channel.lower().lstrip("#")
        self.lock = threading.Lock()
        self.msgs = []          # (wall, text)
        self.stop = False
        self.connected = False

    def run(self):
        while not self.stop:
            try:
                self._loop()
            except Exception as ex:
                self.connected = False
                print(f"  Chat-Verbindung weg ({ex}), neuer Versuch in 5 s")
                time.sleep(5)

    def _loop(self):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
        nick = f"justinfan{random.randint(10000, 99999)}"
        s.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        buf = b""
        self.connected = True
        while not self.stop:
            try:
                data = s.recv(65536)
            except socket.timeout:
                s.sendall(b"PING :tmi.twitch.tv\r\n")
                continue
            if not data:
                raise ConnectionError("Server hat getrennt")
            buf += data
            *lines, buf = buf.split(b"\r\n")
            now = time.time()
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                if line.startswith("PING"):
                    s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                at = line.find(" PRIVMSG #")
                if at < 0:
                    continue
                rest = line[at + 10:]
                c = rest.find(" :")
                if c >= 0:
                    with self.lock:
                        self.msgs.append((now, rest[c + 2:]))

    def take_since(self, t0):
        with self.lock:
            if self.msgs and self.msgs[0][0] < t0 - 120:
                self.msgs = [m for m in self.msgs if m[0] >= t0 - 60]
            return [m for m in self.msgs if m[0] >= t0]


# ----------------------------------------------------------------- Live: Aufnahme als Ringpuffer

class Recorder:
    """Nimmt den Stream in 6-s-Stuecken auf (Dateiname = Uhrzeit), alte Stuecke werden geloescht.
    So braucht ein langer Stream nur ein paar hundert MB statt 4 GB pro Stunde."""

    def __init__(self, channel, folder, quality="1080p60,1080p,best", keep_min=15):
        self.channel, self.folder, self.quality, self.keep = channel, Path(folder), quality, keep_min * 60
        self.folder.mkdir(parents=True, exist_ok=True)
        self.procs = []
        self._durs = {}

    def source_cmd(self):
        url = f"https://www.twitch.tv/{self.channel}"
        try:
            import streamlink  # noqa: F401
            return [sys.executable, "-m", "streamlink", "--twitch-disable-ads", "--stdout",
                    "--retry-streams", "10", "--retry-open", "10", url, self.quality]
        except ImportError:
            print("  Hinweis: streamlink fehlt (pip install streamlink), nehme yt-dlp. Werbung wird dann mit aufgenommen.")
            return [sys.executable, "-m", "yt_dlp", "-q", "-f", "best[height<=1080]/best", "-o", "-", url]

    def start(self, source=None):
        src = subprocess.Popen(source or self.source_cmd(), stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
        seg = subprocess.Popen(["ffmpeg", "-v", "error", "-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?",
                                "-c", "copy", "-f", "segment", "-segment_time", "6",
                                "-reset_timestamps", "1", "-strftime", "1",
                                str(self.folder / "seg_%Y%m%d-%H%M%S.ts")],
                               stdin=src.stdout, stderr=subprocess.DEVNULL)
        src.stdout.close()
        self.procs = [src, seg]

    def alive(self):
        return all(p.poll() is None for p in self.procs)

    def stop(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def _dur(self, p):
        if p not in self._durs:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(p)], capture_output=True, text=True)
            try:
                self._durs[p] = float(r.stdout.strip())
            except ValueError:
                self._durs[p] = 6.0
        return self._durs[p]

    def segments(self):
        """[(wall_start, path)] der fertigen Stuecke. Die Zeit wird vom gerade offenen Stueck aus
        rueckwaerts ueber die echten Laengen gerechnet (der Dateiname allein ist am Anfang ungenau)."""
        files = sorted(self.folder.glob("seg_*.ts"))
        if len(files) < 2:
            return []
        try:
            anchor = datetime.strptime(files[-1].stem[4:], "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            return []
        out, t = [], anchor
        for p in reversed(files[:-1]):
            t -= self._dur(p)
            out.append((t, p))
        for p in list(self._durs):
            if not p.exists():
                del self._durs[p]
        return out[::-1]

    def cleanup(self, protect_from):
        limit = min(time.time() - self.keep, protect_from)
        for wall, p in self.segments():
            if wall < limit - 10:
                try:
                    p.unlink()
                except OSError:
                    pass

    def cut(self, ws, we, out):
        """Wanduhr-Bereich [ws, we] als MP4 schneiden. True wenn geklappt."""
        segs = self.segments()
        use = []
        for k, (wall, p) in enumerate(segs):
            nxt = wall + self._dur(p)
            if nxt > ws and wall < we:
                use.append((wall, p))
        if not use or use[-1][0] + self._dur(use[-1][1]) < we - 0.3:
            return None          # Ende noch nicht aufgenommen (oder Luecke): spaeter nochmal
        ws = max(ws, use[0][0])  # Anfang fehlt (Aufnahme startete spaeter): Clip beginnt etwas spaeter
        tmp = Path(out).with_suffix(".tmp.ts")
        with open(tmp, "wb") as fh:
            for _, p in use:
                fh.write(p.read_bytes())
        off = max(0.0, ws - use[0][0])
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{off:.2f}", "-i", str(tmp),
                            "-t", f"{we - ws:.2f}", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
                           check=True)
        finally:
            tmp.unlink(missing_ok=True)
        return Path(out).exists()


def save_meta(channel, entry):
    """Wie bei normalen Clips: raw/<kanal>_clips.json, damit prepare/render Titel & Infos finden."""
    RAW.mkdir(exist_ok=True)
    j = RAW / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_clips.json"
    try:
        meta = json.loads(j.read_text(encoding="utf-8")) if j.exists() else []
    except Exception:
        meta = []
    meta = [m for m in meta if str(m.get("id")) != str(entry["id"])] + [entry]
    j.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def watch_live(channel, hours=0, threshold=4.0, min_rate=1.0, delay=4, vor=6, nach=12,
               laenge_max=20, max_clips=0, game="Grand Theft Auto VI", game_check=None,
               source=None, on_clip=None):
    """Live-Schleife. game_check(channel) -> Spielname oder None (None = Pruefung nicht moeglich)."""
    if not shutil.which("ffmpeg"):
        sys.exit("Fehlt: ffmpeg")
    RAW.mkdir(exist_ok=True)
    buf = RAW / f"_live_{channel}"
    rec = Recorder(channel, buf)
    chat = TwitchChat(channel)
    chat.start()
    t_begin = time.time()
    t_end = t_begin + hours * 3600 if hours else None
    counts, hypes = {}, {}       # Wanduhr-Sekunde -> Anzahl
    finder = SpikeFinder(threshold, min_rate)
    pending, made, last_end = [], 0, 0.0
    last_sec = int(time.time()) - 1
    game_ok, next_game = None, 0.0
    log = open(RAW / f"_live_{channel}.log", "a", encoding="utf-8")
    print(f"Live-Clipper fuer twitch.tv/{channel} laeuft. Strg+C beendet.")
    try:
        while True:
            now = time.time()
            if t_end and now > t_end:
                break
            # --- nur GTA 6 clippen
            if now >= next_game:
                next_game = now + 300
                g = game_check(channel) if game_check else None
                if g is None and game_check:
                    g_ok = game_ok if game_ok is not None else True
                else:
                    g_ok = (g is None) or (game.lower() in (g or "").lower())
                if g_ok != game_ok:
                    print(f"  Spiel: {g or 'unbekannt'} -> {'clippe' if g_ok else 'pausiert (kein GTA 6)'}")
                game_ok = g_ok
                if game_ok and not rec.procs:
                    rec.start(source)
                    print(f"  {datetime.now():%H:%M:%S}  Aufnahme gestartet")
            if rec.procs and not rec.alive():
                print("  Aufnahme beendet (Stream offline?). Neuer Versuch in 60 s.")
                rec.stop()
                rec.procs = []
                time.sleep(60)
                if game_ok:
                    rec.start(source)
                continue
            # --- Chat einsortieren
            msgs = chat.take_since(now - 420)
            counts.clear()
            hypes.clear()
            for wall, text in msgs:
                s = int(wall)
                counts[s] = counts.get(s, 0) + 1
                if is_hype(text):
                    hypes[s] = hypes.get(s, 0) + 1
            cur = int(now) - 1
            if cur - int(t_begin) > 2:
                lo = max(cur - 400, int(t_begin))   # nur Chat seit Start zaehlt (sonst Fehlalarm am Anfang)
                c = [counts.get(s, 0) for s in range(lo, cur + 1)]
                h = [hypes.get(s, 0) for s in range(lo, cur + 1)]
                for s in range(last_sec + 1, cur + 1):
                    i = s - lo
                    sc, rate = score_at(c, h, i)
                    sp = finder.feed(s, sc, rate)
                    if sp and game_ok and rec.procs:
                        ws, we, mo = window_for(sp, delay, vor, nach, laenge_max)
                        if ws >= last_end:
                            sample = [t for w, t in msgs if sp["onset"] <= w <= sp["onset"] + 6][:8]
                            pending.append({"ws": ws, "we": we, "mo": mo, "sp": sp, "chat": sample})
                            last_end = we
                            print(f"  SPIKE {datetime.fromtimestamp(mo):%H:%M:%S}  Wert {sp['score']:.1f}, "
                                  f"{sp['rate']:.1f} Nachr./s  -> Clip wird geschnitten, sobald aufgenommen")
                last_sec = cur
                if now - int(now) < 0.6 and cur % 30 == 0:
                    sc, rate = score_at(c, h, len(c) - 1)
                    print(f"  {datetime.now():%H:%M:%S}  Chat {rate:.1f} Nachr./s, Spike-Wert {sc:.1f} "
                          f"(Schwelle {threshold}), Clips {made}")
            # --- fertige Fenster schneiden (wenn die Aufnahme ueber das Ende hinaus ist)
            for p in list(pending):
                if now < p["we"] + 8:
                    continue
                cid = f"live{datetime.fromtimestamp(p['ws']):%Y%m%d%H%M%S}"
                out = RAW / f"{channel}_{cid}.mp4"
                ok = rec.cut(p["ws"], p["we"], out)
                if ok is None and now < p["we"] + 60:
                    continue
                pending.remove(p)
                if not ok:
                    print(f"  Clip {cid}: Aufnahme deckt den Moment nicht ab, uebersprungen")
                    continue
                made += 1
                entry = {"id": cid, "title": "", "info": f"Chat-Spike {datetime.fromtimestamp(p['mo']):%d.%m. %H:%M}",
                         "url": f"https://www.twitch.tv/{channel}", "duration": round(p["we"] - p["ws"], 1),
                         "views": "", "channel": channel, "game": game, "pad": 0,
                         "quelle": "chat-spike-live", "spike": round(p["sp"]["score"], 2),
                         "chat_rate": round(p["sp"]["rate"], 1), "chat": p["chat"]}
                save_meta(channel, entry)
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                print(f"  -> {out}  ({entry['duration']} s)")
                if on_clip:
                    on_clip(out, entry)
                if max_clips and made >= max_clips:
                    raise KeyboardInterrupt
            rec.cleanup(min([p["ws"] for p in pending], default=time.time()))
            time.sleep(1)
    except KeyboardInterrupt:
        print("Beende ...")
    finally:
        chat.stop = True
        rec.stop()
        log.close()
    print(f"Fertig: {made} Live-Clips in {RAW}/")
    return made
