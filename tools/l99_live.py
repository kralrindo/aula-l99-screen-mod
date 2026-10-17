"""Live info page (page 31) + language keeper for the L99 screen. Windows.

Runs inside the Deck runner (l99_deck.py), sharing its USB serial link:
  * While the "Canlı Bilgi / Live Info" page is open it fills 8 text lines
    (screen variables 0x1000-0x1070) every 2 seconds: weather, now playing,
    system load, date/time.
  * Every 30 s it re-applies the language chosen in Settings > Language. The
    screen remembers the choice but only switches when it is written again,
    so after a power cycle it would otherwise start in the first language.

The screen fonts only have plain ASCII letters, so Turkish letters are written
without accents (ş -> s, ı -> i, ...).

deck.json, optional:
  "live": {"city": "Istanbul",
           "info": ["CPU %{cpu}  RAM %{ram}", "{date}", "{time}"]}   (system card lines)
Test without a keyboard:  python l99_live.py --print
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LIVE_PAGE = 31
LANG_REG = 0x203F
FIRST_LANGUAGE = "en"          # language 0 in the built UI image (build_l99.py --first)
VARS = [0x1000, 0x1010, 0x1020, 0x1030, 0x1040, 0x1050, 0x1060, 0x1070]
MAXLEN = [18, 28, 28, 28, 28, 28, 28, 28]
VAR_BYTES = 32

TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")
DAYS = {"tr": ["Pazartesi", "Sali", "Carsamba", "Persembe", "Cuma", "Cumartesi", "Pazar"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
MONTHS = {"tr": ["Ocak", "Subat", "Mart", "Nisan", "Mayis", "Haziran", "Temmuz", "Agustos", "Eylul",
                 "Ekim", "Kasim", "Aralik"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                 "October", "November", "December"]}
WMO = {  # Open-Meteo weather codes
    "tr": {0: "Acik", 1: "Az bulutlu", 2: "Parcali bulutlu", 3: "Kapali", 45: "Sisli", 48: "Sisli",
           51: "Ciseleme", 53: "Ciseleme", 55: "Ciseleme", 61: "Hafif yagmur", 63: "Yagmurlu", 65: "Saganak",
           71: "Hafif kar", 73: "Karli", 75: "Yogun kar", 80: "Sagnak", 81: "Saganak", 82: "Kuvvetli saganak",
           95: "Firtina", 96: "Dolulu firtina", 99: "Dolulu firtina"},
    "en": {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
           51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
           71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Heavy showers",
           95: "Thunderstorm", 96: "Hail storm", 99: "Hail storm"},
}
WORDS = {"tr": {"humidity": "nem", "paused": "Duraklatildi", "nothing": "Calan bir sey yok", "noweather": "Hava alinamadi"},
         "en": {"humidity": "hum.", "paused": "Paused", "nothing": "Nothing playing", "noweather": "No weather data"}}


def ascii_text(s: str, n: int) -> bytes:
    s = (s or "").translate(TR_ASCII)
    s = s.replace("°", "").replace("’", "'").replace("–", "-").replace("—", "-")
    raw = s.encode("ascii", "replace")[:n]
    return raw + bytes(VAR_BYTES - len(raw))


# ------------------------------------------------------------------ sources ---
class Weather:
    def __init__(self):
        self.city = None
        self.where = None
        self.data = None
        self.at = 0.0

    def get(self, city: str):
        if city != self.city:
            self.city, self.where, self.data, self.at = city, None, None, 0.0
        if time.time() - self.at > 900:
            self.at = time.time()
            try:
                from l99_weather import find_city, fetch_weather
                if self.where is None:
                    self.where = find_city(city)
                self.data = fetch_weather(self.where[0], self.where[1])
            except BaseException as exc:          # find_city calls sys.exit on unknown cities
                self.at = time.time() - 840       # retry in a minute
                print(f"  (weather: {exc})")
        return self.data


def _fmt_time(sec) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


def now_playing():
    """(title, artist, app, playing, position, duration) from Windows' media controls,
    or Spotify's window title as a fallback."""
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M

        async def get():
            mgr = await M.request_async()
            s = mgr.get_current_session()
            if s is None:
                return None
            props = await s.try_get_media_properties_async()
            info = s.get_playback_info()
            tl = s.get_timeline_properties()
            app = s.source_app_user_model_id or ""
            pos = tl.position.total_seconds() if hasattr(tl.position, "total_seconds") else tl.position.duration / 1e7
            dur = tl.end_time.total_seconds() if hasattr(tl.end_time, "total_seconds") else tl.end_time.duration / 1e7
            if info.playback_status == 4 and tl.last_updated_time:         # playing: add time since update
                try:
                    pos += (datetime.now(tl.last_updated_time.tzinfo) - tl.last_updated_time).total_seconds()
                except Exception:
                    pass
            return props.title, props.artist, app, info.playback_status == 4, pos, dur
        return asyncio.run(get())
    except ImportError:
        pass
    except Exception as exc:
        print(f"  (media: {exc})")
    title = spotify_title()
    if title and " - " in title:
        artist, song = title.split(" - ", 1)
        return song, artist, "Spotify", True, 0, 0
    return None


def spotify_title() -> str | None:
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    from ctypes import wintypes
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(0x1000, False, pid)
            if h:
                buf = ctypes.create_unicode_buffer(512); size = wintypes.DWORD(512)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) and buf.value.lower().endswith("spotify.exe"):
                    t = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, t, n + 1)
                    found.append(t.value)
                kernel32.CloseHandle(h)
        return True
    user32.EnumWindows(each, 0)
    return next((t for t in found if " - " in t), None)


class _Cpu:
    def __init__(self):
        self.last = None

    def load(self) -> int:
        try:
            import psutil
            return round(psutil.cpu_percent(interval=None))
        except ImportError:
            pass
        if os.name != "nt":
            return 0
        from ctypes import wintypes
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        v = [x.dwHighDateTime << 32 | x.dwLowDateTime for x in (idle, kern, user)]
        if self.last is None:
            self.last = v
            return 0
        di, dk, du = (a - b for a, b in zip(v, self.last))
        self.last = v
        total = dk + du
        return round(100 * (total - di) / total) if total else 0


def ram_load() -> int:
    try:
        import psutil
        return round(psutil.virtual_memory().percent)
    except ImportError:
        pass
    if os.name != "nt":
        return 0

    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(f"x{i}", ctypes.c_ulonglong) for i in range(7)]
    m = MS(); m.len = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.load


# --------------------------------------------------------------- the lines ---
class LiveInfo:
    def __init__(self, link, get_cfg):
        self.link, self.get_cfg = link, get_cfg
        self.weather = Weather()
        self.cpu = _Cpu()
        self.sent = {}
        self.lang = FIRST_LANGUAGE
        self.last_lang_check = 0.0

    def lines(self) -> list[str]:
        cfg = self.get_cfg().get("live", {})
        lang = self.lang
        W = WORDS[lang]
        out = []
        w = self.weather.get(cfg.get("city", "Istanbul"))
        city = cfg.get("city", "Istanbul").split(",")[0]
        if w:
            out += [f"{city}  {w['temp']} C",
                    f"{WMO[lang].get(w.get('wmo', 3), '')}  {w['low']}/{w['high']} C  {W['humidity']} %{w['humidity']}"]
        else:
            out += [city, W["noweather"]]
        np = now_playing()
        if np:
            title, artist, app, playing, pos, dur = np
            app = app.split("!")[-1].split(".")[0] if app else ""
            app = {"Spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge"}.get(app, app)
            state = (f"{_fmt_time(pos)} / {_fmt_time(dur)}" if dur else "") if playing else W["paused"]
            out += [title or "-", artist or "", f"{app}  {state}".strip()]
        else:
            out += [W["nothing"], "", ""]
        now = datetime.now()
        fields = {"cpu": self.cpu.load(), "ram": ram_load(), "time": now.strftime("%H:%M"),
                  "date": f"{now.day} {MONTHS[lang][now.month - 1]} {DAYS[lang][now.weekday()]}"}
        info = cfg.get("info") or ["CPU %{cpu}   RAM %{ram}", "{date}", "{time}"]
        for k in range(3):
            try:
                out.append(info[k].format(**fields) if k < len(info) else "")
            except Exception:
                out.append(info[k])
        return out

    def tick(self):
        now = time.time()
        if self.get_cfg().get("keep_language") and now - self.last_lang_check > 30:   # bilingual builds only
            self.last_lang_check = now
            v = self.link.read(LANG_REG)
            if v is not None:
                self.link.write(LANG_REG, bytes([0, v & 1]))           # make the screen switch to it
                langs = [FIRST_LANGUAGE, "en" if FIRST_LANGUAGE == "tr" else "tr"]
                self.lang = langs[v & 1]
        if self.link.page() != LIVE_PAGE:
            self.sent.clear()                     # repaint everything next time the page opens
            return
        for var, n, text in zip(VARS, MAXLEN, self.lines()):
            raw = ascii_text(text, n)
            if self.sent.get(var) != raw:
                self.link.write(var, raw)
                self.sent[var] = raw

    def run_forever(self, period: float = 2.0):
        while True:
            try:
                self.tick()
            except Exception as exc:
                print(f"  (live page: {exc})")
            self.link.close()        # free the port between ticks (the Deck editor uploads through it)
            time.sleep(period)

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True).start()


if __name__ == "__main__" and "--print" in sys.argv:
    class NoLink:
        def read(self, a): return None
        def write(self, a, b): pass
        def page(self): return LIVE_PAGE
    import json
    cfg = json.loads((HERE / "deck.json").read_text(encoding="utf-8")) if (HERE / "deck.json").exists() else {}
    li = LiveInfo(NoLink(), lambda: cfg)
    li.cpu.load(); time.sleep(0.5)
    for var, n, t in zip(VARS, MAXLEN, li.lines()):
        print(f"{var:#06x}  {ascii_text(t, n).rstrip(bytes(1)).decode()}")
