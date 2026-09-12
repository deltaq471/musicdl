#!/usr/bin/env python3
"""Music Downloader - YouTube + SoundCloud + Spotify - fast, zero deps."""

import os, re, json, sys, time, threading, subprocess, queue
import ssl, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
import yt_dlp

BG      = "#14161a"
PANEL   = "#1c1f24"
FIELD   = "#262a31"
BORDER  = "#33383f"
FG      = "#e7eaef"
MUTED   = "#9aa3b0"
ACCENT  = "#38b7a0"
DANGER  = "#e0524f"
GOOD    = "#56c271"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CLIENT_ID = None
CID_CACHE = Path.home() / ".sc_cid"
FFMPEG_PATH = None

FORMATS = {
    "mp3":  ("mp3",    "0",     ".mp3"),
    "flac": ("flac",   "0",     ".flac"),
    "m4a":  ("m4a",    "320k",  ".m4a"),
    "opus": ("opus",   "256k",  ".opus"),
    "ogg":  ("vorbis", "10",    ".ogg"),
    "wav":  ("wav",    "0",     ".wav"),
}


def _urlopen(req, timeout=20):
    """Open a URL, retrying once with certificate verification off if needed."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if getattr(e, "reason", None) and isinstance(e.reason, ssl.SSLCertVerificationError):
            return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        raise


def _bundled_bin():
    cands = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "downloader_gui_bin")
            cands.append(Path(meipass))
        cands.append(Path(sys.executable).parent / "downloader_gui_bin")
        cands.append(Path(sys.executable).parent)
    cands.append(Path(__file__).parent / "downloader_gui_bin")
    for d in cands:
        if (d / "ffmpeg.exe").exists() and (d / "ffprobe.exe").exists():
            return str(d)
    return None


def get_ffmpeg():
    """Return a usable ffmpeg exe path, or None. Only a system install counts."""
    global FFMPEG_PATH
    if FFMPEG_PATH: return FFMPEG_PATH
    d = _bundled_bin()
    if d:
        FFMPEG_PATH = str(Path(d) / "ffmpeg.exe")
        return FFMPEG_PATH
    import shutil
    FFMPEG_PATH = shutil.which("ffmpeg")
    return FFMPEG_PATH


def get_ffprobe_dir():
    """Directory containing ffprobe for yt-dlp (needs both exes)."""
    d = _bundled_bin()
    if d: return d
    import shutil
    if shutil.which("ffprobe"):
        return str(Path(shutil.which("ffprobe")).parent)
    return None


def is_youtube(url):
    return bool(re.match(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be|m\.youtube\.com)', url))


def is_youtube_playlist(url):
    if "youtube.com" in url and ("/playlist?" in url or re.search(r'[\?&]list=', url)):
        return True
    return False


def get_youtube_entries(url):
    """Return a list of (entry_url, title, uploader) for a YouTube playlist."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = []
        for e in info.get('entries') or []:
            if not e: continue
            vid_url = e.get('url') or ('https://www.youtube.com/watch?v=' + e.get('id', ''))
            if not e.get('id') and not e.get('url'): continue
            entries.append((vid_url, e.get('title', '?'), e.get('uploader', e.get('channel', '?'))))
        return entries


def is_soundcloud(url):
    return bool(re.match(r'https?://(?:www\.)?soundcloud\.com', url))


def is_spotify(url):
    return bool(re.match(r'https?://(?:open|play)\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)', url))


def _spotify_target(url):
    m = re.search(r'spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)/([A-Za-z0-9]+)', url)
    if not m: return None
    return m.group(1), m.group(2)


def _spotify_art(ent):
    """Best artwork URL from a Spotify entity, or empty string."""
    ca = ent.get("coverArt") or {}
    if isinstance(ca, dict):
        for s in sorted(ca.get("sources") or [], key=lambda s: s.get("width") or 0, reverse=True):
            u = (s or {}).get("url")
            if u: return u
    vi = ent.get("visualIdentity") or {}
    if isinstance(vi, dict):
        for img in vi.get("image") or []:
            u = (img or {}).get("url")
            if u: return u
    return ""


def spotify_embed(kind, sid):
    h = fetch_text(f"https://open.spotify.com/embed/{kind}/{sid}", "https://open.spotify.com/")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', h, re.DOTALL)
    if not m: return None
    d = json.loads(m.group(1))
    return (d.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})) or None


def spotify_search_items(url):
    """Resolve a Spotify track/album/playlist URL into a list of item dicts.

    Metadata comes from Spotify's public embed page; the audio itself is
    fetched from YouTube (the best public source available for Spotify tracks).
    """
    target = _spotify_target(url)
    if not target: return None
    kind, sid = target
    ent = spotify_embed(kind, sid)
    if not ent: return None
    art = _spotify_art(ent)

    if kind == "track":
        title = ent.get("title", "") or ""
        artists = ", ".join(a.get("name", "") for a in (ent.get("artists") or []) if a.get("name"))
        release = ent.get("releaseDate") or {}
        released = release.get("isoString", "") if isinstance(release, dict) else str(release)
        artist = artists or ((ent.get("subtitle") or "").split(" • ")[0].strip())
        return [{"title": title, "artist": artist, "album": "",
                 "released": released, "art": art}] if title else []

    ent_album = ent.get("album") or {}
    ent_album = ent_album if isinstance(ent_album, dict) else {}
    album_title = (ent_album.get("title") or "") if kind == "album" else ""
    release = ent.get("releaseDate") or {}
    released = release.get("isoString", "") if isinstance(release, dict) else str(release)

    tl = ent.get("trackList") or ent_album.get("trackList") or []
    items = []
    for t in tl:
        if not t: continue
        title = t.get("title", "") or ""
        subtitle = (t.get("subtitle") or "").replace("\xa0", " ").strip()
        parts = [p.strip() for p in re.split(r" • | — |,", subtitle) if p.strip()]
        artist = parts[0] if parts else ""
        if kind == "album":
            alb = album_title or ent.get("title", "")
        elif " • " in subtitle:
            bits = [p.strip() for p in subtitle.split(" • ")]
            alb = bits[1] if len(bits) > 1 else ""
        else:
            alb = ""
        if not title: continue
        items.append({"title": title, "artist": artist, "album": alb,
                      "released": released, "art": art})
    return items


def dl_youtube(url, path, fmt="flac", prog=None, cancel=None):
    """Download audio from a YouTube URL (or ytsearchN: query) via yt-dlp.

    Downloads the native best-audio stream and returns its real file. If the
    downloaded container already matches the requested format it is kept
    untouched; otherwise it is converted only when a system ffmpeg is found,
    else the native file is returned as-is (callers name files by the real
    extension). No ffmpeg is ever required.
    """
    path = Path(path)
    _codec, _quality, suffix = FORMATS.get(fmt, FORMATS["flac"])
    stem = path.with_suffix('')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(stem) + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }
    if cancel:
        ydl_opts['progress_hooks'] = [lambda d: cancel.is_set() and (_ for _ in ()).throw(RuntimeError("Cancelled"))]
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'unknown')
        artist = info.get('artist', info.get('uploader', 'unknown'))
        ts = info.get('timestamp')
        native = None
        try:
            native = Path(info['requested_downloads'][0]['filepath'])
        except Exception:
            pass
        if not native:
            exts = sorted(stem.parent.glob(stem.name + ".*"))
            native = exts[0] if exts else None
        if not native or not native.exists():
            raise RuntimeError("No audio file produced")

        if native.suffix.lower() == suffix:
            return native, title, artist, ts

        ffmpeg = get_ffmpeg()
        if not ffmpeg:
            return native, title, artist, ts

        target = path.with_suffix(suffix)
        try:
            args = ['-i', str(native)] + _conv_args(fmt) + ['-y', str(target)]
            subprocess.run([ffmpeg] + args, check=True, capture_output=True, timeout=600)
        except Exception:
            pass
        if target.exists() and target.stat().st_size > 0:
            native.unlink(missing_ok=True)
            return target, title, artist, ts
        return native, title, artist, ts


def _valid_cid(cid):
    """True if the client_id is accepted by the API (probe /resolve; 401 = rejected)."""
    if not cid: return False
    try:
        url = ("https://api-v2.soundcloud.com/resolve?url="
               + urllib.parse.quote("https://soundcloud.com/__probe_invalid_xyz")
               + f"&client_id={cid}")
        r = _urlopen(urllib.request.Request(url, headers=HEADERS), 10)
        return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 401
    except: return False


def _scrape_cid():
    for page in ("https://soundcloud.com", "https://soundcloud.com/discover"):
        try:
            req = urllib.request.Request(page, headers=HEADERS)
            h = _urlopen(req, 15).read().decode(errors="replace")
        except: continue
        try:
            m = re.search(r'__sc_hydration\s*=\s*(\[.*?\]);', h, re.DOTALL)
            if m:
                for item in json.loads(m.group(1)):
                    if item.get("hydratable") == "apiClient":
                        cid = item["data"]["id"]
                        if _valid_cid(cid): return cid
        except: pass
        for pat in (r'"client_id":"([a-zA-Z0-9_\-]+)"', r'"client_id"\s*:\s*"([a-zA-Z0-9_\-]+)"'):
            for cid in re.findall(pat, h):
                if _valid_cid(cid): return cid
    return None


def get_cid():
    global CLIENT_ID
    if CLIENT_ID:
        if _valid_cid(CLIENT_ID): return CLIENT_ID
    fresh = _scrape_cid()
    if fresh:
        CLIENT_ID = fresh
        try: CID_CACHE.write_text(fresh)
        except: pass
        return CLIENT_ID
    if CID_CACHE.exists():
        CLIENT_ID = CID_CACHE.read_text().strip()
        if _valid_cid(CLIENT_ID): return CLIENT_ID
    CLIENT_ID = "Pb72ranhoyt6gw7hM7TkzUItXlMWSNSo"
    return CLIENT_ID


def api(path, params=None):
    p = {"client_id": get_cid()}
    if params: p.update(params)
    url = f"https://api-v2.soundcloud.com/{path.lstrip('/')}?{urllib.parse.urlencode(p)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with _urlopen(req, 15) as r:
        return json.loads(r.read().decode())


def resolve(url):
    return api("/resolve", {"url": url})


def _pick_stream(trans):
    """Pick the best quality transcoding (highest bitrate MP3 suggested by presets)."""
    chosen = None
    for t in trans:
        preset = t.get("preset", "")
        proto = t.get("format", {}).get("protocol", "")
        if proto == "progressive" and preset in ("mp3_320", "mp3_0_1"):
            return t
        if proto == "progressive" and preset.startswith("mp3") and not chosen:
            chosen = t
    if not chosen:
        for t in trans:
            if t.get("format", {}).get("protocol", "") == "progressive":
                chosen = t; break
    if not chosen and trans:
        chosen = trans[0]
    return chosen


def stream_url(tid):
    """Get (download_url, ext, track_data) for a SoundCloud track. 2 API calls."""
    try:
        td = api(f"/tracks/{tid}")
        time.sleep(0.1)
        trans = td.get("media", {}).get("transcodings", [])
        if not trans: return None, None, None
        chosen = _pick_stream(trans)
        if not chosen: return None, None, None
        rel = chosen["url"].replace("https://api-v2.soundcloud.com/", "")
        sd = api(rel)
        u = sd.get("url")
        if not u: return None, None, td
        m = chosen.get("format", {}).get("mime_type", "")
        e = ("m4a" if ("mp4" in m or "aac" in m)
             else "opus" if "opus" in m
             else "flac" if "flac" in m
             else "mp3")
        return u, e, td
    except: return None, None, None


def fetch(url, ref=None):
    h = HEADERS.copy()
    if ref: h["Referer"] = ref
    req = urllib.request.Request(url, headers=h)
    return _urlopen(req, 30)


def dl_stream(url, path, prog=None, cancel=None):
    """Download from URL, return path or raise."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_hls = False
    try:
        r = fetch(url, "https://soundcloud.com/")
        first = r.read(4096)
        ct = r.headers.get("Content-Type", "")
        is_hls = ("m3u8" in ct or first.startswith(b"#EXTM3U"))
        r.close()
    except: pass

    if is_hls:
        return _dl_hls(first if first.startswith(b"#EXTM3U") else fetch_text(url), url, path, prog, cancel)
    return _dl_direct(url, path, prog, cancel)


def _dl_direct(url, path, prog=None, cancel=None):
    r = fetch(url, "https://soundcloud.com/")
    total = int(r.headers.get("content-length", 0))
    down = 0; start = time.time()
    with open(path, "wb") as f:
        while True:
            if cancel and cancel.is_set():
                f.close(); path.unlink(missing_ok=True); raise RuntimeError("Cancelled")
            d = r.read(256*1024)
            if not d: break
            f.write(d); down += len(d)
            if total and prog:
                p = min(100, down/total*100)
                prog(p, format_bytes(down), format_bytes(down/(time.time()-start+0.01))+"/s")
    r.close()
    return path


def _dl_hls(playlist, base_url, path, prog=None, cancel=None):
    if not playlist.startswith("#EXTM3U"): playlist = fetch_text(base_url)
    segs = []
    b = base_url.rsplit("/", 1)[0]
    for l in playlist.splitlines():
        l = l.strip()
        if l and not l.startswith("#"):
            segs.append(l if l.startswith("http") else f"{b}/{l}")
    if not segs: raise RuntimeError("No HLS segments")
    with open(path, "wb") as f:
        for i, su in enumerate(segs):
            if cancel and cancel.is_set():
                f.close(); path.unlink(missing_ok=True); raise RuntimeError("Cancelled")
            for _ in range(3):
                try:
                    r = fetch(su, "https://soundcloud.com/")
                    while True:
                        d = r.read(256*1024)
                        if not d: break
                        f.write(d)
                    r.close(); break
                except: time.sleep(0.5)
            if prog: prog((i+1)/len(segs)*100, f"seg {i+1}/{len(segs)}", "")
    return path


def fetch_text(url, ref=None):
    with fetch(url, ref) as r:
        return r.read().decode(errors="replace")


def format_bytes(b):
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.2f} GB"

fmt_bytes = format_bytes  # kept name used by the Files tab


def extract_urls(t):
    return list(set(re.findall(r'https?://[^\s"\'<>]+', t)))


def clean_name(s):
    return re.sub(r'[<>:"/\\|?*]', "_", str(s)).strip()[:100] or "untitled"


def _sc_name(td, opts):
    """Build display/filename respecting the author (artist account) option."""
    username = td.get('user', {}).get('username', '')
    title = td.get('title', '?')
    if (opts.get("author") or opts.get("artist")) and username:
        return f"{username} - {title}"
    return title


def _conv_args(fmt):
    """ffmpeg codec args for converting SoundCloud streams to the target format."""
    return {
        "mp3":  ['-c:a', 'libmp3lame', '-q:a', '0'],
        "flac": ['-c:a', 'flac'],
        "m4a":  ['-c:a', 'aac', '-b:a', '320k'],
        "opus": ['-c:a', 'libopus', '-b:a', '256k'],
        "ogg":  ['-c:a', 'libvorbis', '-q:a', '10'],
        "wav":  ['-c:a', 'pcm_s16le'],
    }.get(fmt, ['-c:a', 'flac'])


import struct as _struct
class ID3:
    """Minimal ID3v2.3 writer with album art, genre and year."""
    @staticmethod
    def write(path, title="", artist="", album="", art=None, genre="", year=""):
        path = Path(path)
        if not path.exists(): return
        old = path.read_bytes()
        if old[:3] == b"ID3":
            s = 0
            for i in range(6, 10): s = (s << 7) | (old[i] & 0x7F)
            old = old[10+s:]
        frames = b""
        def tf(i, t):
            nonlocal frames
            if not t: return
            d = bytes([0x03]) + t.encode("utf-8") + b"\x00"
            frames += i.encode() + _struct.pack(">I", len(d)) + b"\x00\x00" + d
        tf("TIT2", title); tf("TPE1", artist); tf("TALB", album); tf("TCON", genre); tf("TYER", year)
        if art:
            enc = bytes([0x00])
            mime = b"image/jpeg\x00"
            frames += b"APIC" + _struct.pack(">I", 5+len(art)) + b"\x00\x00" + enc + mime + bytes([0x03]) + b"\x00" + art
        sz = len(frames)
        h = b"ID3\x03\x00\x00"
        for i in range(3, -1, -1): h += bytes([(sz >> (i*7)) & 0x7F])
        path.write_bytes(h + frames + old)


def strip_vorbis(path):
    """Remove Vorbis-comment (and padding) metadata blocks from a FLAC file."""
    try:
        path = Path(path)
        data = path.read_bytes()
        if data[:4] != b"fLaC": return
        i = 4
        blocks = data[:4]
        while i < len(data):
            header = data[i]
            last = header & 0x80
            btype = header & 0x7F
            size = int.from_bytes(data[i+1:i+4], "big")
            if btype == 4 or btype == 6:
                i += 4 + size
                if last: break
                continue
            blocks += data[i:i+4+size]
            i += 4 + size
            if last: break
        path.write_bytes(blocks + data[i:])
    except: pass


class Downloader:
    def __init__(self, out_dir, max_conc=3, opts=None, opts_get=None, on_prog=None, on_done=None, on_err=None, on_q=None):
        self.out = Path(out_dir)
        self.max_conc = max_conc
        self.opts = opts or {"artwork": True, "author": True, "album": True, "title": True}
        self.opts_get = opts_get
        self.on_prog = on_prog
        self.on_done = on_done
        self.on_err = on_err
        self.on_q = on_q
        self._tasks = {}
        self._queue = []
        self._active = 0
        self._cancel = {}
        self._lock = threading.Lock()
        self._count = 0

    def add(self, url):
        opts = self.opts_get() if self.opts_get else self.opts
        t = {"id": self._count, "url": url, "state": "queued", "prog": 0, "speed": "", "name": url[:60], "err": "", "data": None, "opts": opts}
        with self._lock:
            self._count += 1
            self._tasks[t["id"]] = t
            self._queue.append(t["id"])
            self._cancel[t["id"]] = threading.Event()
        if self.on_q: self.on_q()
        self._proc()

    def add_tracks(self, tracks):
        opts = self.opts_get() if self.opts_get else self.opts
        with self._lock:
            for tr in tracks:
                t = {"id": self._count, "url": tr.get("permalink_url", ""), "state": "queued", "prog": 0,
                     "speed": "", "name": f"{tr.get('user', {}).get('username', '?')} - {tr.get('title', '?')}",
                     "err": "", "data": tr, "opts": opts}
                self._count += 1
                self._tasks[t["id"]] = t
                self._queue.append(t["id"])
                self._cancel[t["id"]] = threading.Event()
        if self.on_q: self.on_q()
        self._proc()

    def add_spotify(self, item):
        opts = self.opts_get() if self.opts_get else self.opts
        with self._lock:
            name = f"{item.get('artist', '?')} - {item.get('title', '?')}"
            t = {"id": self._count, "url": "spotify:" + item.get("title", ""), "state": "queued", "prog": 0,
                 "speed": "", "name": name, "err": "", "data": {"src": "spotify", **item}, "opts": opts}
            self._count += 1
            self._tasks[t["id"]] = t
            self._queue.append(t["id"])
            self._cancel[t["id"]] = threading.Event()
        if self.on_q: self.on_q()
        self._proc()

    def cancel(self, tid):
        e = self._cancel.get(tid)
        if e: e.set()
        with self._lock:
            t = self._tasks.get(tid)
            if t: t["state"] = "cancelled"
            if tid in self._queue: self._queue.remove(tid)
        if self.on_q: self.on_q()

    def cancel_all(self):
        for e in self._cancel.values(): e.set()
        with self._lock:
            for t in self._tasks.values():
                if t["state"] in ("queued", "downloading"): t["state"] = "cancelled"
            self._queue.clear()
        if self.on_q: self.on_q()

    def retry(self, tid):
        with self._lock:
            t = self._tasks.get(tid)
            if not t: return
            t["state"] = "queued"; t["prog"] = 0; t["speed"] = ""; t["err"] = ""
            self._queue.append(tid)
            e = self._cancel.get(tid)
            if e: e.clear()
        if self.on_q: self.on_q()
        self._proc()

    def _proc(self):
        while self._active < self.max_conc and self._queue:
            with self._lock:
                if not self._queue: break
                tid = self._queue.pop(0)
                t = self._tasks.get(tid)
                if not t or t["state"] == "cancelled": continue
                self._active += 1
            t["state"] = "downloading"
            threading.Thread(target=self._work, args=(tid,), daemon=True).start()

    def _work(self, tid):
        t = self._tasks.get(tid)
        if not t: return
        cancel = self._cancel.get(tid)
        try:
            time.sleep(0.15)  # rate limit safety
            url = t["url"]
            data = t.get("data") or {}
            opts = t.get("opts") or (self.opts_get() if self.opts_get else self.opts)
            fmt = opts.get("fmt", "flac") or "flac"
            if fmt not in FORMATS: fmt = "flac"

            if is_youtube(url):
                fp = self.out / "temp_download"
                final_path, title, artist, ts = dl_youtube(url, fp, fmt=fmt, cancel=cancel)
                if (opts.get("author") or opts.get("artist")) and artist:
                    t["name"] = f"{artist} - {title}"
                else:
                    t["name"] = title
                fname = f"{clean_name(t['name'])}{final_path.suffix.lower() or '.' + fmt}"
                self.out.mkdir(parents=True, exist_ok=True)
                if final_path != self.out / fname:
                    final_path.rename(self.out / fname)
                fp = self.out / fname

                if (opts.get("author") or opts.get("artist")) and fp.suffix.lower() in (".mp3", ".flac"):
                    try:
                        yr = str(datetime.fromtimestamp(ts).year) if ts else ""
                        ID3.write(fp, title=title, artist=artist, album="", year=yr)
                    except: pass
                t["state"] = "completed"; t["prog"] = 100; t["output"] = fp
                if self.on_done: self.on_done(tid, fp)

            elif data.get("src") == "spotify":
                fp = self.out / "temp_download"
                q = f"{data.get('artist', '')} {data.get('title', '')} audio".strip()
                try:
                    final_path, _, _, ts = dl_youtube("ytsearch1:" + q, fp, fmt=fmt, cancel=cancel)
                except Exception:
                    raise RuntimeError("No YouTube match found for this track")
                title = data.get("title", "?")
                artist = data.get("artist", "?")
                album = data.get("album", "") or ""
                genre = data.get("genre", "") or ""
                released = data.get("released", "") or ""
                art = data.get("art", "") or ""
                t["name"] = f"{artist} - {title}" if (opts.get("author") or opts.get("artist")) else title
                self.out.mkdir(parents=True, exist_ok=True)
                fname = f"{clean_name(t['name'])}{final_path.suffix.lower() or '.' + fmt}"
                if final_path != self.out / fname:
                    final_path.rename(self.out / fname)
                fp = self.out / fname

                if fp.suffix.lower() in (".mp3", ".flac") and (opts.get("author") or opts.get("artist")):
                    try:
                        ab = None
                        if opts.get("artwork") and art:
                            ab = _fetch_art(art)
                        yr = str(released)[:4] if released else (str(datetime.fromtimestamp(ts).year) if ts else "")
                        ID3.write(fp, title=title, artist=artist, album=album,
                                  art=ab, genre=genre, year=yr)
                    except: pass
                t["state"] = "completed"; t["prog"] = 100; t["output"] = fp
                if self.on_done: self.on_done(tid, fp)

            else:
                if data:
                    tid2 = data.get("id")
                    dlurl, ext, td = stream_url(tid2)
                    if not dlurl: raise RuntimeError("No stream URL")
                    t["name"] = _sc_name(td, opts)
                else:
                    r = resolve(t["url"])
                    rid = r.get("id")
                    if not rid: raise RuntimeError("Could not resolve")
                    time.sleep(0.15)
                    dlurl, ext, td = stream_url(rid)
                    if not dlurl: raise RuntimeError("No stream URL")
                    t["name"] = _sc_name(td, opts)

                self.out.mkdir(parents=True, exist_ok=True)
                fname = f"{clean_name(t['name'])}.{fmt}"
                fp = self.out / fname
                if fp.exists():
                    t["state"] = "completed"; t["prog"] = 100; t["output"] = fp
                    if self.on_done: self.on_done(tid, fp)
                    return

                def cb(p, b, s):
                    t["prog"] = p; t["speed"] = s
                    if self.on_prog: self.on_prog(tid)

                # Stream to a temp file using its real container/extension.
                tmp = self.out / f"temp_{tid}.{ext}"
                dl_stream(dlurl, tmp, cb, cancel)

                want_ext = f".{fmt}"
                if tmp.suffix.lower() == want_ext:
                    # Source codec already matches the target: move untouched.
                    if tmp != fp: tmp.rename(fp)
                else:
                    # Re-encode only when needed. Lossless targets (flac/wav)
                    # are encoded from the best source without further loss.
                    ffmpeg = get_ffmpeg()
                    if ffmpeg:
                        try:
                            args = ['-i', str(tmp)] + _conv_args(fmt) + ['-y', str(fp)]
                            subprocess.run([ffmpeg] + args, check=True, capture_output=True, timeout=600)
                            if fp.exists() and fp.stat().st_size > 0:
                                tmp.unlink(missing_ok=True)
                        except: pass
                    if tmp.exists():
                        alt = fp.with_suffix(tmp.suffix.lower())
                        if tmp != alt: tmp.rename(alt)
                        fp = alt

                # Tags/art are appended only for real mp3 & flac files (safe formats);
                # other containers are left untouched to stay compatible.
                # When tagging is off ("No metadata"), any tags are stripped.
                if fp.suffix.lower() in (".mp3", ".flac"):
                    try:
                        if opts.get("author") or opts.get("artist"):
                            art_url = (td or {}).get("artwork_url", "")
                            art = None
                            if opts.get("artwork") and art_url:
                                art = _fetch_art(art_url)
                            ttl = td.get("title", "") if opts.get("title", True) else ""
                            cat = td.get("user", {}).get("username", "")
                            cal = (td.get("genre", "") or "SoundCloud") if opts.get("album") else ""
                            g = td.get("genre", "") or ""
                            yr = (td.get("created_at", "") or "")[:4]
                            if art or ttl or cat or cal or g or yr:
                                ID3.write(fp, title=ttl, artist=cat, album=cal, art=(art or None), genre=g, year=yr)
                        else:
                            strip_vorbis(fp)
                    except: pass

                t["state"] = "completed"; t["prog"] = 100; t["output"] = fp
                if self.on_done: self.on_done(tid, fp)
        except Exception as e:
            t["state"] = "error"; t["err"] = str(e)
            if self.on_err: self.on_err(tid, str(e))
        finally:
            with self._lock: self._active -= 1
            self._proc()
            if self.on_q: self.on_q()

    def stats(self):
        with self._lock:
            return (len(self._tasks), len(self._queue), self._active,
                    sum(1 for t in self._tasks.values() if t["state"] == "completed"),
                    sum(1 for t in self._tasks.values() if t["state"] == "error"))

    def list(self):
        return list(self._tasks.values())


def _fetch_art(url):
    """Download album art, return bytes or None."""
    if not url: return None
    u = url.replace("-large.", "-t500x500.").replace("large", "t500x500")
    try:
        req = urllib.request.Request(u, headers=HEADERS)
        return _urlopen(req, 10).read()
    except: return None


APP_NAME = "Music DL"; VERSION = "5.0"
SETTINGS = Path.home() / ".sc_dl.json"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("1020x680")
        self.minsize(840, 560)
        self.configure(bg=BG)
        self._no_meta = tk.BooleanVar(value=False)
        self._setup()
        self._load()

        self.dl = Downloader(self._out.get(), int(self._maxv.get()),
                             opts_get=self._get_opts,
                             on_prog=lambda t: self._ev.put(("prog", t)),
                             on_done=lambda t, p: self._ev.put(("done", t, p)),
                             on_err=lambda t, e: self._ev.put(("err", t, e)),
                             on_q=lambda: self._ev.put(("q",)))
        self._widgets = {}
        self._ev = queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(500, self._poll)

    # ---------- layout ----------
    def _setup(self):
        st = self._style = ttk.Style()
        for t in ("clam", "alt", "default"):
            if t in st.theme_names(): st.theme_use(t); break
        st.configure(".", background=PANEL, foreground=FG, fieldbackground=FIELD,
                     selectbackground=ACCENT, selectforeground="#0b0d0f",
                     font=("Segoe UI", 10))
        st.configure("TFrame", background=PANEL)
        st.configure("TLabel", background=PANEL, foreground=FG)
        st.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), background=BG, foreground=FG)
        st.configure("Sub.TLabel", font=("Segoe UI", 9), background=BG, foreground=MUTED)
        st.configure("Header.TLabel", font=("Segoe UI", 9, "bold"), foreground=MUTED)
        st.configure("Group.TLabel", font=("Segoe UI", 9, "bold"),
                     background=PANEL, foreground=ACCENT, padding=(0, 0, 6, 0))
        st.configure("Hint.TLabel", font=("Segoe UI", 9), background=BG, foreground=MUTED)
        st.configure("TEntry", padding=5)
        st.configure("URL.TEntry", fieldbackground=FIELD, foreground=FG,
                     insertcolor=FG, padding=(8, 6))
        st.configure("TCombobox", fieldbackground=FIELD, foreground=FG, padding=4,
                     selectbackground=FIELD, selectforeground=FG)
        st.configure("TButton", padding=(10, 5), background="#2a2e36")
        st.map("TButton", background=[("active", "#353a44"), ("pressed", "#23262d")],
               foreground=[("disabled", "#5a6068")])
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)],
               selectbackground=[("readonly", FIELD)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#09201a", font=("Segoe UI", 10, "bold"))
        st.map("Accent.TButton", background=[("active", "#43c9b0"), ("pressed", "#2fa089")],
               foreground=[("disabled", "#0d3a31")])
        st.configure("Danger.TButton", background=DANGER, foreground="white")
        st.map("Danger.TButton", background=[("active", "#ee6a66"), ("pressed", "#c7423e")])
        st.configure("Flat.TButton", background="#242830", foreground=FG)
        st.map("Flat.TButton", background=[("active", "#30353f"), ("pressed", "#1d2026")])
        st.configure("TCheckbutton", background=PANEL, foreground=FG, padding=(2, 2))
        st.map("TCheckbutton", background=[("active", PANEL)], foreground=[("selected", FG)])
        st.configure("Accent.Horizontal.TProgressbar", background=ACCENT, troughcolor="#2c313a", thickness=9)
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(4, 6, 4, 0))
        st.configure("TNotebook.Tab", background="#262a31", foreground=MUTED, padding=(14, 6))
        st.map("TNotebook.Tab", background=[("selected", PANEL)],
               foreground=[("selected", FG)])
        st.configure("Status.TLabel", background=BG, foreground=MUTED, padding=(10, 6),
                     font=("Segoe UI", 9))
        st.configure("NoMeta.Off.TButton", background=DANGER, foreground="white")
        st.map("NoMeta.Off.TButton", background=[("active", "#ee6a66"), ("pressed", "#c7423e")],
               foreground=[("disabled", "#f0b6b4")])
        st.configure("NoMeta.On.TButton", background=GOOD, foreground="#08120a")
        st.map("NoMeta.On.TButton", background=[("active", "#6fd088"), ("pressed", "#45b05c")],
               foreground=[("disabled", "#8fd4a0")])

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Header
        hf = tk.Frame(self, bg=BG)
        hf.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        hf.grid_columnconfigure(1, weight=1)
        ttk.Label(hf, text="Music DL", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(hf, text="YouTube  ·  SoundCloud  ·  Spotify", style="Sub.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(hf, text=f"v{VERSION}", style="Sub.TLabel").grid(row=0, column=2, sticky="e", pady=(4, 0))

        # URL card
        uf = tk.Frame(self, bg=BG)
        uf.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 2))
        uf.grid_columnconfigure(0, weight=1)
        self._ph = "Paste a YouTube, SoundCloud or Spotify link…"
        self._url = ttk.Entry(uf, style="URL.TEntry")
        self._url.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=2)
        self._url.insert(0, self._ph)
        self._url.configure(foreground=MUTED)
        self._url.bind("<Return>", lambda e: self._add())
        self._url.bind("<KeyRelease>", self._srcupd)
        self._url.bind("<FocusIn>", self._focusin)
        self._url.bind("<FocusOut>", self._focusout)
        ttk.Button(uf, text="  +  Add  ", style="Accent.TButton", command=self._add).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(uf, text="Paste", style="Flat.TButton", command=self._paste).grid(row=0, column=2)
        self._src = ttk.Label(uf, text="Supports YouTube, SoundCloud and Spotify links",
                              style="Hint.TLabel")
        self._src.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0), padx=(2, 0))

        # Output card
        sf = tk.Frame(self, bg=BG)
        sf.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 2))
        sf.grid_columnconfigure(1, weight=1)
        ttk.Label(sf, text="Save to", style="Group.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._out = ttk.Entry(sf, style="URL.TEntry")
        self._out.grid(row=0, column=1, sticky="ew", padx=(0, 8), ipady=2)
        self._out.insert(0, str(Path.home() / "Downloads" / "Music"))
        ttk.Button(sf, text="Browse", style="Flat.TButton", command=self._br).grid(row=0, column=2, padx=(0, 18))
        ttk.Label(sf, text="Parallel", style="Group.TLabel").grid(row=0, column=3, sticky="e", padx=(0, 8))
        self._maxv = tk.StringVar(value="3")
        ttk.Combobox(sf, textvariable=self._maxv, values=["1", "2", "3", "4", "5", "6", "8", "10"],
                     width=3, state="readonly").grid(row=0, column=4)
        ttk.Label(sf, text="Format", style="Group.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self._fmt = tk.StringVar(value="flac")
        ttk.Combobox(sf, textvariable=self._fmt, values=list(FORMATS),
                     width=6, state="readonly").grid(row=1, column=1, sticky="w", pady=(8, 0))
        self._nmeta = ttk.Button(sf, style="NoMeta.Off.TButton", command=self._togglenm)
        self._nmeta.grid(row=1, column=4, padx=(0, 6), pady=(8, 0))
        ttk.Button(sf, text="Cancel All", style="Danger.TButton", command=self._ca).grid(row=1, column=5, pady=(8, 0))
        self._paint_nm()

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=3, column=0, sticky="nsew", padx=14, pady=(6, 0))
        self.tq = ttk.Frame(self.nb); self.tf = ttk.Frame(self.nb)
        self.nb.add(self.tq, text="  Downloads  ")
        self.nb.add(self.tf, text="  Files  ")
        self._build_q()
        self._build_f()

        self._sb = ttk.Label(self, style="Status.TLabel", anchor="w")
        self._sb.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 8))

    def _build_q(self):
        self.tq.grid_columnconfigure(0, weight=1); self.tq.grid_rowconfigure(1, weight=1)
        h = ttk.Frame(self.tq); h.grid(row=0, column=0, sticky="ew", pady=(10, 2), padx=10)
        h.grid_columnconfigure((0, 1, 2, 3), weight=1)
        for c, t in [(0, "FILE"), (1, "PROGRESS"), (2, "SPEED"), (3, "STATUS")]:
            ttk.Label(h, text=t, style="Header.TLabel").grid(row=0, column=c, padx=4, sticky="w")
        cv = tk.Canvas(self.tq, bg=PANEL, highlightthickness=0)
        cv.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.tq, orient="v", command=cv.yview)
        sb.grid(row=1, column=1, sticky="ns")
        cv.configure(yscrollcommand=sb.set)
        self._qi = ttk.Frame(cv)
        cv.create_window((0, 0), window=self._qi, anchor="nw")
        self._qi.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfig(1, width=e.width))
        self._qempty = ttk.Label(self._qi, text="Nothing here yet — paste a link above and hit + Add.",
                                 style="Header.TLabel", background=PANEL)
        self._qempty.grid(row=0, column=0, pady=90)

    def _build_f(self):
        self.tf.grid_columnconfigure(0, weight=1); self.tf.grid_rowconfigure(1, weight=1)
        tb = ttk.Frame(self.tf); tb.grid(row=0, column=0, sticky="ew", pady=(10, 4), padx=10)
        tb.grid_columnconfigure(2, weight=1)
        ttk.Button(tb, text="Refresh", style="Flat.TButton", command=self._rf).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(tb, text="Select All", style="Flat.TButton", command=self._selall).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(tb, text="Delete Sel", style="Danger.TButton", command=self._del).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(tb, text="Open Folder", style="Flat.TButton", command=self._openf).grid(row=0, column=3, padx=(0, 10))
        self._ff = ttk.Entry(tb, style="URL.TEntry"); self._ff.grid(row=0, column=4, sticky="ew", padx=(10, 0))
        self._ff.bind("<KeyRelease>", lambda e: self._rf(self._ff.get()))
        cv = tk.Canvas(self.tf, bg=PANEL, highlightthickness=0)
        cv.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.tf, orient="v", command=cv.yview)
        sb.grid(row=1, column=1, sticky="ns")
        cv.configure(yscrollcommand=sb.set)
        self._fi = ttk.Frame(cv)
        cv.create_window((0, 0), window=self._fi, anchor="nw")
        self._fi.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfig(1, width=e.width))
        self._fempty = ttk.Label(self._fi, text="No music files in the output folder yet.",
                                 style="Header.TLabel", background=PANEL)
        self._fempty.grid(row=0, column=0, pady=70)
        self._fdata = []; self._fw = []

    # ---------- actions ----------
    def _focusin(self, e=None):
        if self._url.get() == self._ph:
            self._url.delete(0, "end")
            self._url.configure(foreground=FG)
        self._srcupd()

    def _focusout(self, e=None):
        if not self._url.get().strip():
            self._url.insert(0, self._ph)
            self._url.configure(foreground=MUTED)
        self._srcupd()

    def _srcupd(self, e=None):
        u = self._url.get().strip()
        if not u or u == self._ph:
            self._src.configure(text="Supports YouTube, SoundCloud and Spotify links")
        elif is_youtube(u):
            self._src.configure(text="Source: YouTube  ·  playlists are auto-expanded")
        elif is_soundcloud(u):
            self._src.configure(text="Source: SoundCloud  ·  sets & albums are auto-expanded")
        elif is_spotify(u):
            self._src.configure(text="Source: Spotify  ·  audio is matched from YouTube")
        else:
            self._src.configure(text="This doesn't look like a supported link")

    def _add(self):
        raw = self._url.get().strip()
        if self._ph in raw:
            raw = raw.replace(self._ph, "").strip()
        if not raw: return
        urls = extract_urls(raw) or [raw]
        urls = [u.strip().rstrip('.,;:()[]"\'') for u in urls]
        self._url.delete(0, "end")
        self._url.configure(foreground=MUTED)
        self._url.insert(0, self._ph)
        self._srcupd()
        added = 0
        for u in urls:
            u = u.strip()
            if not u: continue
            if is_youtube(u):
                if is_youtube_playlist(u):
                    try:
                        entries = get_youtube_entries(u)
                        if entries:
                            for eurl, etitle, eup in entries:
                                self.dl.add(eurl)
                                added += 1
                            continue
                    except: pass
                self.dl.add(u)
                added += 1
            elif is_soundcloud(u):
                if any(k in urllib.parse.urlparse(u).path for k in ("/sets/", "/albums/", "/playlists/")):
                    try:
                        r = resolve(u)
                        tracks = r.get("tracks", [])
                        if tracks:
                            self.dl.add_tracks(tracks)
                            added += len(tracks)
                            continue
                    except: pass
                self.dl.add(u)
                added += 1
            elif is_spotify(u):
                try:
                    items = spotify_search_items(u)
                except Exception:
                    items = None
                if items:
                    for it in items:
                        self.dl.add_spotify(it)
                    added += len(items)
                    continue
                self.dl.add(u)
                added += 1
        if added:
            self.nb.select(self.tq)

    def _paste(self):
        try:
            t = self.clipboard_get()
            u = extract_urls(t) or [t.strip()]
            if not u or not u[0]: return
            self._url.configure(foreground=FG)
            self._url.delete(0, "end")
            self._url.insert(0, u[0])
            self._srcupd()
            self._add()
        except: pass

    def _br(self):
        p = filedialog.askdirectory()
        if p:
            self._out.delete(0, "end"); self._out.insert(0, p)

    def _ca(self):
        self.dl.cancel_all()

    def _open_path(self, p):
        p = str(Path(p))
        try:
            if os.name == "nt":
                os.startfile(p)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except: pass

    def _openf(self):
        out = self._out.get().strip()
        if out:
            self._open_path(out)

    def _selall(self):
        if not self._fw: return
        all_on = all(w["var"].get() for w in self._fw)
        for w in self._fw:
            w["var"].set(not all_on)

    # ---------- status ----------
    def _upd(self, tid):
        w = self._widgets.get(tid)
        if not w: return
        t = self.dl._tasks.get(tid)
        if not t: return
        w["bar"]["value"] = t["prog"]
        w["speed"].configure(text=t["speed"])
        if t["state"] == "downloading":
            w["status"].configure(text=f"  {t['prog']:.0f}%", foreground=ACCENT)

    def _done(self, tid, path):
        w = self._widgets.get(tid)
        if w:
            w["status"].configure(text="  Done", foreground=GOOD)
            w["bar"]["value"] = 100
            w["speed"].configure(text="")
        self._rf()
        self._stat()

    def _err(self, tid, msg):
        w = self._widgets.get(tid)
        if w:
            w["status"].configure(text="  Error", foreground=DANGER)
            w["speed"].configure(text=msg[:34])
        self._stat()

    def _qchg(self):
        self._stat()
        tasks = self.dl.list()
        existing = set(self._widgets.keys())
        current = set()
        row = 0
        for t in tasks:
            if t["state"] == "completed": continue
            current.add(t["id"])
            if t["id"] not in self._widgets:
                self._qempty.grid_forget()
                f = ttk.Frame(self._qi)
                f.grid(row=row, column=0, sticky="ew", pady=3, padx=10)
                f.grid_columnconfigure(0, weight=1)
                nl = ttk.Label(f, text=t["name"][:70], anchor="w")
                nl.grid(row=0, column=0, sticky="ew", padx=(2, 8), pady=6)
                pr = ttk.Progressbar(f, style="Accent.Horizontal.TProgressbar", length=180, mode="determinate")
                pr.grid(row=0, column=1, padx=4, pady=6)
                sl = ttk.Label(f, text="", width=18, anchor="e", foreground=MUTED)
                sl.grid(row=0, column=2, padx=4, pady=6)
                st = ttk.Label(f, text="  Queued", width=9, anchor="center", foreground=MUTED)
                st.grid(row=0, column=3, padx=4, pady=6)
                cb = ttk.Button(f, text="X", width=3, style="Flat.TButton",
                                command=lambda tid=t["id"]: self.dl.cancel(tid))
                cb.grid(row=0, column=4, padx=(8, 2), pady=6)
                self._widgets[t["id"]] = {"frame": f, "bar": pr, "speed": sl, "status": st, "cancel": cb}
            else:
                self._widgets[t["id"]]["frame"].grid(row=row, column=0, sticky="ew", pady=3, padx=10)
            row += 1
        for tid in existing - current:
            w = self._widgets.pop(tid, None)
            if w: w["frame"].destroy()
        if not self._widgets and all(t["state"] == "completed" for t in tasks):
            self._qempty.grid(row=0, column=0, pady=90)

    def _drain(self):
        while True:
            try:
                ev = self._ev.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "prog":
                self._upd(ev[1])
            elif kind == "done":
                self._done(ev[1], ev[2])
            elif kind == "err":
                self._err(ev[1], ev[2])
            elif kind == "q":
                self._qchg()

    def _poll(self):
        self._drain()
        tasks = self.dl.list()
        for t in tasks:
            if t["state"] == "downloading":
                w = self._widgets.get(t["id"])
                if w:
                    w["bar"]["value"] = t["prog"]
                    w["speed"].configure(text=t["speed"])
        self.after(500, self._poll)

    # ---------- files tab ----------
    def _rf(self, ft=None):
        for w in self._fw:
            try: w["f"].destroy()
            except: pass
        self._fw.clear()
        if ft is None: ft = self._ff.get()
        out = self._out.get()
        try:
            files = sorted(Path(out).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        except: files = []
        self._fdata = []
        for f in files:
            if f.suffix.lower() not in (".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac"): continue
            if f.name.startswith("."): continue
            self._fdata.append((f.name, f))
        self._fempty.grid_forget()
        shown = 0
        for name, fp in self._fdata:
            if ft and ft.lower() not in name.lower(): continue
            var = tk.BooleanVar()
            f = ttk.Frame(self._fi); f.grid(row=shown, column=0, sticky="ew", pady=1, padx=10)
            f.grid_columnconfigure(2, weight=1)
            ttk.Checkbutton(f, variable=var).grid(row=0, column=0, padx=2, pady=3)
            ttk.Label(f, text=name, anchor="w").grid(row=0, column=2, sticky="ew", padx=4, pady=3)
            s = fp.stat().st_size
            ttk.Label(f, text=format_bytes(s), anchor="e", width=11, foreground=MUTED).grid(row=0, column=3, padx=4, pady=3)
            ttk.Button(f, text="Open", style="Flat.TButton",
                       command=lambda p=fp: self._open_path(p)).grid(row=0, column=4, padx=2, pady=3)
            self._fw.append({"f": f, "var": var, "path": fp})
            shown += 1
        if not shown:
            self._fempty.grid(row=0, column=0, pady=70)

    def _del(self):
        sel = [w for w in self._fw if w["var"].get()]
        if not sel: return
        for w in sel:
            try: w["path"].unlink()
            except: pass
        self._rf()

    # ---------- helpers ----------
    def _stat(self):
        _, q, a, d, e = self.dl.stats()
        parts = []
        if a: parts.append(f"Active: {a}")
        if q: parts.append(f"Queued: {q}")
        parts.append(f"Done: {d}")
        if e: parts.append(f"Errors: {e}")
        self._sb.configure(text="   " + "   |   ".join(parts))

    def _togglenm(self):
        self._no_meta.set(not self._no_meta.get())
        self._paint_nm()

    def _paint_nm(self):
        on = self._no_meta.get()
        self._nmeta.configure(style="NoMeta.On.TButton" if on else "NoMeta.Off.TButton",
                              text="No metadata: ON" if on else "No metadata: OFF")

    def _get_opts(self):
        nm = self._no_meta.get()
        return {"artwork": not nm,
                "author": not nm,
                "album": not nm,
                "title": not nm,
                "fmt": self._fmt.get()}

    def _close(self):
        self.dl.cancel_all()
        self._save()
        self.destroy()

    def _load(self):
        try:
            if SETTINGS.exists():
                d = json.loads(SETTINGS.read_text())
                self._out.delete(0, "end"); self._out.insert(0, d.get("out", str(Path.home() / "Downloads" / "Music")))
                self._maxv.set(str(d.get("max", 3)))
                if "geo" in d: self.geometry(d["geo"])
                if "opts" in d:
                    o = d["opts"]
                    self._no_meta.set(o.get("nometa", False))
                    self._fmt.set(o.get("fmt", "flac"))
                    self._paint_nm()
        except: pass

    def _save(self):
        try:
            SETTINGS.write_text(json.dumps({
                "out": self._out.get(), "max": int(self._maxv.get()),
                "geo": self.geometry(),
                "opts": self._get_opts()}))
        except: pass


if __name__ == "__main__":
    App().mainloop()