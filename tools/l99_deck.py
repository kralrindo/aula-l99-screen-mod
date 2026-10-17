"""L99 Deck (by Rindobolical) - PC side.  Windows only.

How it works
  * The keyboard can only send numpad and media keys from its screen, so the
    Deck buttons send numpad keys (7 8 9 / 4 5 6 / 1 2 3 / 0 - +).
  * When a numpad key arrives, this app asks the screen (over its USB serial
    port) which page is open. On the Deck page it runs that tile's action; on any
    other page the key is passed on unchanged, so Mini Numpad works normally.
    (Needs the Deck v5+ screen UI and AULA's driver closed.)
  * On 2.4 GHz / Bluetooth the page can't be read (the screen's USB port only
    exists on the cable), so numpad keys always run Deck tiles. deck.json
    "wireless": "numpad" turns that off (keys then pass through unchanged).

Usage
  python l99_deck.py              run (leave it running; see install-startup.bat)
  python l99_deck.py --preview    write deck_preview.png from deck.json
  python l99_deck.py --upload     draw the tiles (with each program's icon) and
                                  send the picture to the keyboard over USB
                                  (close AULA's driver first)

Edit deck.json to change tiles. Actions per tile (pick one):
  "run": "path\\to\\program.exe", "args": [...], "focus": "program.exe"
  "url": "https://..."            (opened as an app window in Chrome if found)
  "keys": "win+shift+s"           (a shortcut)
  "action": "mic_toggle" | "media_play_pause" | "media_next" | "media_prev"
Optional: "icon": "path to .png/.ico/.exe", "label": "text"
Setup: pip install pillow pyserial icoextract pycaw comtypes
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import zlib
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "deck.json"
sys.path.insert(0, str(HERE))

DECK_ADDR = 0x04120000          # Deck picture (wrapped as a normal picture) in the empty slot
BLOCK = 0x20000                 # one 128 KB storage block
W, H = 320, 480
X0, Y0, TW, TH, GX, GY, COLS = 16, 125, 90, 78, 11, 10, 3

# keypad scan codes for tiles 1..12 (7 8 9 / 4 5 6 / 1 2 3 / 0 - +)
TILE_SCANS = [0x47, 0x48, 0x49, 0x4B, 0x4C, 0x4D, 0x4F, 0x50, 0x51, 0x52, 0x4A, 0x4E]

DEFAULT_CONFIG = {
    "tiles": [
        {"label": "Chrome", "run": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "focus": "chrome.exe"},
        {"label": "Discord", "run": r"%LOCALAPPDATA%\Discord\Update.exe", "args": ["--processStart", "Discord.exe"],
         "icon": r"%LOCALAPPDATA%\Discord\app.ico", "focus": "Discord.exe"},
        {"label": "Spotify", "run": r"%APPDATA%\Spotify\Spotify.exe", "focus": "Spotify.exe"},
        {"label": "YT Music", "url": "https://music.youtube.com", "glyph": "music"},
        {"label": "Steam", "run": r"C:\Program Files (x86)\Steam\steam.exe", "focus": "steamwebhelper.exe"},
        {"label": "Epic Games", "run": r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
         "focus": "EpicGamesLauncher.exe"},
        {"label": "Mic", "action": "mic_toggle", "glyph": "mic"},
        {"label": "Play/Pause", "action": "media_play_pause", "glyph": "play"},
        {"label": "Screenshot", "keys": "win+shift+s", "glyph": "shot"},
        {"label": "Desktop", "keys": "win+d", "glyph": "desk"},
        {"label": "Task Mgr", "keys": "ctrl+shift+esc", "glyph": "task"},
        {"label": "", "glyph": "plus"},
    ]
}


def load_config() -> dict:
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"created {CONFIG} - edit it to change the tiles")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def expand(p: str) -> str:
    return os.path.expandvars(p) if p else p


# ---------------------------------------------------------------- drawing ---
def _font(size: int):
    from PIL import ImageFont
    for f in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
              "/usr/share/fonts/opentype/inter/Inter-Bold.otf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _glyph(name: str, size: int = 40):
    """Simple built-in icons for tiles without a program icon."""
    from PIL import Image, ImageDraw
    s = size * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    wcol = (255, 255, 255, 255)
    lw = s // 12
    if name == "mic":
        d.rounded_rectangle((s*0.36, s*0.08, s*0.64, s*0.6), radius=s*0.14, fill=wcol)
        d.arc((s*0.22, s*0.3, s*0.78, s*0.74), 0, 180, fill=wcol, width=lw)
        d.line((s*0.5, s*0.74, s*0.5, s*0.9), fill=wcol, width=lw)
        d.line((s*0.32, s*0.9, s*0.68, s*0.9), fill=wcol, width=lw)
    elif name == "play":
        d.polygon([(s*0.18, s*0.2), (s*0.18, s*0.8), (s*0.52, s*0.5)], fill=wcol)
        d.rectangle((s*0.6, s*0.2, s*0.68, s*0.8), fill=wcol)
        d.rectangle((s*0.76, s*0.2, s*0.84, s*0.8), fill=wcol)
    elif name == "music":
        d.ellipse((s*0.18, s*0.62, s*0.42, s*0.86), fill=wcol)
        d.ellipse((s*0.58, s*0.52, s*0.82, s*0.76), fill=wcol)
        d.line((s*0.4, s*0.74, s*0.4, s*0.16), fill=wcol, width=lw)
        d.line((s*0.8, s*0.64, s*0.8, s*0.08), fill=wcol, width=lw)
        d.polygon([(s*0.37, s*0.12), (s*0.83, s*0.02), (s*0.83, s*0.16), (s*0.37, s*0.26)], fill=wcol)
    elif name == "shot":
        d.rectangle((s*0.1, s*0.2, s*0.9, s*0.8), outline=wcol, width=lw)
        for x, y in ((0.25, 0.35), (0.75, 0.35), (0.25, 0.65), (0.75, 0.65)):
            d.ellipse((s*(x-0.05), s*(y-0.05), s*(x+0.05), s*(y+0.05)), fill=wcol)
    elif name == "desk":
        d.rectangle((s*0.08, s*0.14, s*0.92, s*0.68), outline=wcol, width=lw)
        d.line((s*0.5, s*0.68, s*0.5, s*0.84), fill=wcol, width=lw)
        d.line((s*0.3, s*0.86, s*0.7, s*0.86), fill=wcol, width=lw)
    elif name == "task":
        for k, hgt in enumerate((0.4, 0.65, 0.3, 0.8)):
            x = 0.14 + k * 0.2
            d.rectangle((s*x, s*(0.9-hgt), s*(x+0.13), s*0.9), fill=wcol)
    else:  # plus / unknown
        d.line((s*0.5, s*0.2, s*0.5, s*0.8), fill=(120, 120, 130, 255), width=lw)
        d.line((s*0.2, s*0.5, s*0.8, s*0.5), fill=(120, 120, 130, 255), width=lw)
    return im.resize((size, size), Image.LANCZOS)


def _icon_for(tile: dict, size: int = 40):
    from PIL import Image
    src = expand(tile.get("icon") or tile.get("run") or "")
    if src.lower().endswith(".url") and os.path.exists(src):
        for line in open(src, encoding="utf-8", errors="ignore"):
            if line.lower().startswith("iconfile="):
                src = expand(line.split("=", 1)[1].strip())
                break
    try:
        if src and os.path.exists(src):
            if src.lower().endswith((".exe", ".dll")):
                from icoextract import IconExtractor
                import io
                data = IconExtractor(src).get_icon().getvalue()
                img = Image.open(io.BytesIO(data))
                sizes = sorted(img.info.get("sizes", [img.size]))
                img.size = sizes[-1]
                img.load()
            else:
                img = Image.open(src)
                if img.format == "ICO":
                    img.size = sorted(img.info.get("sizes", [img.size]))[-1]
                    img.load()
            return img.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception as exc:
        print(f"  (no icon for {tile.get('label')}: {exc})")
    if "glyph" in tile or not tile.get("label"):
        return _glyph(tile.get("glyph", "plus"), size)
    return _letter(tile["label"], size)


def _letter(label: str, size: int):
    """Placeholder icon: first letter on a coloured circle."""
    from PIL import Image, ImageDraw
    s = size * 4
    hue = sum(map(ord, label)) % 6
    col = [(66, 133, 244), (88, 101, 242), (30, 185, 84), (230, 80, 60), (40, 110, 160), (120, 120, 130)][hue]
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, s - 1, s - 1), fill=col + (255,))
    d.text((s / 2, s / 2), label[0].upper(), font=_font(int(s * 0.55)), fill=(255, 255, 255, 255), anchor="mm")
    return im.resize((size, size), Image.LANCZOS)


def render(cfg: dict, out: Path | None = None):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    en = cfg.get("language", "en") != "tr"
    d.text((W // 2, 100), "Shortcuts" if en else "Kısayollar", font=_font(20), fill=(255, 255, 255), anchor="mm")
    # "Live" button (opens the Live info page, page 31) - must match build_l99.py
    d.rounded_rectangle((226, 36, 308, 70), radius=17, fill=(44, 46, 58))
    d.ellipse((238, 49, 246, 57), fill=(46, 196, 120))
    d.text((277, 53), "Live" if en else "Canlı", font=_font(14), fill=(255, 255, 255), anchor="mm")
    small = _font(12)
    tiles = (cfg["tiles"] + [{}] * 12)[:12]
    for k, tile in enumerate(tiles):
        c, r = k % COLS, k // COLS
        x, y = X0 + c * (TW + GX), Y0 + r * (TH + GY)
        empty = not tile or not any(key in tile for key in ("run", "url", "keys", "action"))
        d.rounded_rectangle((x, y, x + TW, y + TH), radius=12,
                            fill=(26, 26, 32) if empty else (44, 46, 58),
                            outline=(60, 60, 70) if empty else None)
        icon = _icon_for(tile or {}, 40)
        im.paste(icon, (x + (TW - 40) // 2, y + 9), icon)
        label = tile.get("label", "") if tile else ""
        while label and d.textlength(label, font=small) > TW - 6:
            label = label[:-1]
        d.text((x + TW / 2, y + 63), label, font=small, fill=(235, 235, 240), anchor="mm")
    if out:
        im.save(out)
    return im


# ------------------------------------------------------------ USB upload ---
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _flash_cmd(cmd: int, addr: int, payload: bytes) -> bytes:
    body = bytes([cmd]) + addr.to_bytes(4, "big") + payload
    c = _crc16(body)
    return b"\x5a\xa5\x00" + (len(body) + 2).to_bytes(2, "big") + body + bytes([c & 0xFF, c >> 8])


def _check_port_clash(port: str) -> None:
    from serial.tools import list_ports
    others = [p for p in list_ports.comports() if p.device == port and not (p.vid == 0xEEEF and p.pid == 0x268A)]
    if others:
        raise RuntimeError(f"{port} is ALSO used by: {others[0].description}.\n"
                 "Windows gave two devices the same COM number, so the screen can't be reached.\n"
                 "Unplug that device, or give the screen another number: Device Manager > Ports > "
                 "'USB Seri Cihaz' > Properties > Port Settings > Advanced > COM Port Number.")


class UploadError(RuntimeError):
    pass


def upload_picture(cfg: dict, addr: int = DECK_ADDR, progress=None) -> None:
    """Draw the tiles and write the picture into the keyboard screen's storage.
    progress(percent) is called as it goes. Raises UploadError with a readable message."""
    import serial
    from serial.tools import list_ports
    from lt_encode import deck_blob
    image = render(cfg, HERE / "deck_preview.png")
    blob = deck_blob(image)            # looks like a normal picture to the screen
    port = next((p.device for p in list_ports.comports() if p.vid == 0xEEEF and p.pid == 0x268A), None)
    if not port:
        raise UploadError("Keyboard screen not found. Connect the keyboard with the USB cable.")
    try:
        _check_port_clash(port)
    except RuntimeError as exc:
        raise UploadError(str(exc))
    ser = None
    for attempt in range(10):          # the Deck runner may hold the port for up to 3 s
        try:
            ser = serial.Serial(port, 115200, timeout=0.5)
            break
        except serial.SerialException as exc:
            last = exc
            time.sleep(0.5)
    if ser is None:
        raise UploadError(f"Cannot open {port}: {last}\nQuit AULA's driver (tray icon > Exit) and try again.")
    with ser:
        ser.reset_input_buffer()
        region, region_bytes, off = addr, 0, 0
        while off < len(blob):
            chunk = blob[off:off + 2048]
            ser.write(_flash_cmd(0x64, addr + off, chunk))
            reply, end = b"", time.time() + 3
            while b"OK" not in reply and time.time() < end:
                reply += ser.read(64)
            if b"OK" not in reply:
                hint = ("\nThe screen looks like it is in upgrade mode - unplug and replug the keyboard."
                        if reply[:1] == b"\x44" else "")
                raise UploadError(f"The screen stopped answering at {100 * off // len(blob)}%.{hint}")
            off += len(chunk)
            region_bytes += len(chunk)
            if off - (region - addr) >= BLOCK or off >= len(blob):
                ser.write(_flash_cmd(0x66, region, region_bytes.to_bytes(4, "big")))
                time.sleep(0.6)
                ser.read(64)
                region, region_bytes = addr + off, 0
            if progress:
                progress(100 * off // len(blob))


def upload(cfg: dict, addr: int = DECK_ADDR) -> None:
    print(f"sending the Deck picture (storage address {addr:#x}) ...")
    print("(the keyboard screen freezes while it writes - that's normal)")
    try:
        upload_picture(cfg, addr, lambda p: print(f"\r  {p:3d}%", end="", flush=True))
    except UploadError as exc:
        sys.exit(f"\n{exc}")
    print("\nDone. Open another app on the screen, then Deck again (replug if the old tiles stay).")


# --------------------------------------------------------------- actions ---
user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
VK_NUMLOCK, VK_RETURN = 0x90, 0x0D
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x01, 0x02
VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "esc": 0x1B, "tab": 0x09, "enter": 0x0D,
      "space": 0x20, "delete": 0x2E, "home": 0x24, "end": 0x23, "up": 0x26, "down": 0x28, "left": 0x25,
      "right": 0x27, "printscreen": 0x2C, "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1,
      "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}
VK.update({f"f{n}": 0x6F + n for n in range(1, 25)})


def _key(vk: int, up: bool) -> None:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in (0x5B, 0x2E, 0x24, 0x23, 0x25, 0x26, 0x27, 0x28, 0x90, 0xB0, 0xB1, 0xB3, 0xAD, 0xAE, 0xAF):
        flags |= KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, 0, flags, 0)


def send_keys(combo: str) -> None:
    vks = []
    for part in combo.lower().split("+"):
        part = part.strip()
        vks.append(VK.get(part) or ord(part.upper()[0]))
    for v in vks:
        _key(v, False)
    for v in reversed(vks):
        _key(v, True)


def numlock_on() -> None:
    if not user32.GetKeyState(VK_NUMLOCK) & 1:
        _key(VK_NUMLOCK, False)
        _key(VK_NUMLOCK, True)


def mic_toggle() -> None:
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL, CoInitialize
        CoInitialize()
        mic = AudioUtilities.GetMicrophone()
        vol = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = ctypes.cast(vol, ctypes.POINTER(IAudioEndpointVolume))
        muted = not vol.GetMute()
        vol.SetMute(muted, None)
        print("  microphone", "MUTED" if muted else "on")
    except Exception as exc:
        print(f"  mic toggle needs: pip install pycaw comtypes ({exc})")


def focus_window(exe_name: str) -> bool:
    """Bring a running program's main window to the front."""
    if not exe_name:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid)
        if h:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if os.path.basename(buf.value).lower() == exe_name.lower():
                    found.append(hwnd)
            kernel32.CloseHandle(h)
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    _key(0x12, False); _key(0x12, True)   # lets Windows accept the focus change
    user32.SetForegroundWindow(hwnd)
    return True


def run_tile(tile: dict) -> None:
    label = tile.get("label") or "tile"
    print(f"{time.strftime('%H:%M:%S')}  {label}")
    try:
        if "action" in tile:
            a = tile["action"]
            if a == "mic_toggle":
                mic_toggle()
            elif a in VK:
                _key(VK[a], False); _key(VK[a], True)
        elif "keys" in tile:
            send_keys(tile["keys"])
        elif "url" in tile:
            chrome = expand(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            if os.path.exists(chrome):
                subprocess.Popen([chrome, f"--app={tile['url']}"])
            else:
                os.startfile(tile["url"])
        elif "run" in tile:
            path = expand(tile["run"])
            focus = tile.get("focus") or (os.path.basename(path) if path.lower().endswith(".exe") else "")
            if not focus_window(focus):
                if path.lower().endswith(".exe"):
                    subprocess.Popen([path] + [expand(a) for a in tile.get("args", [])],
                                     cwd=os.path.dirname(path) or None)
                else:                      # .lnk / .url / steam:// etc.
                    os.startfile(path)
    except Exception as exc:
        print(f"  failed: {exc}")


# ------------------------------------------------------------ key hook ---
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


DECK_PAGE = 30
KEYPAD_SCANS = {0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x37}


class ScreenLink:
    """Asks the keyboard screen which page is open (register 0x2000) over its USB serial port.
    The port is opened only when needed and closed after a few idle seconds, so
    upload-deck-icons.bat can still use it while the Deck app is running."""

    def __init__(self):
        self.ser = None
        self.last_use = 0.0
        self.warned = False
        self.lock = threading.RLock()
        self.missing = False

    def _open(self):
        import serial
        from serial.tools import list_ports
        port = next((p.device for p in list_ports.comports() if p.vid == 0xEEEF and p.pid == 0x268A), None)
        self.missing = not port            # no screen USB port = keyboard on 2.4 GHz / Bluetooth
        if not port:
            raise OSError("screen not found (keyboard not on the cable?)")
        if any(p.device == port and not (p.vid == 0xEEEF and p.pid == 0x268A) for p in list_ports.comports()):
            raise OSError(f"{port} is shared with another device (phone?)")
        self.ser = serial.Serial(port, 115200, timeout=0.15)

    @staticmethod
    def _frame(body: bytes) -> bytes:
        c = _crc16(body)
        return b"\x5a\xa5" + bytes([len(body) + 2]) + body + bytes([c & 0xFF, c >> 8])

    def read(self, addr: int):
        """Read one 16-bit screen variable/register (0x2000 = open page, 0x203F = language)."""
        packet = self._frame(bytes([0x03, addr >> 8, addr & 0xFF, 0x00, 0x01]))
        want = b"\x5a\xa5\x09\x03" + bytes([addr >> 8, addr & 0xFF])
        with self.lock:
            for attempt in range(2):
                try:
                    if self.ser is None:
                        self._open()
                    self.ser.reset_input_buffer()
                    self.ser.write(packet)
                    reply = b""
                    end = time.time() + 0.25
                    while len(reply) < 12 and time.time() < end:
                        reply += self.ser.read(12 - len(reply))
                    self.last_use = time.time()
                    i = reply.find(want)
                    if i >= 0 and len(reply) >= i + 12:
                        return reply[i + 8] << 8 | reply[i + 9]
                except Exception as exc:
                    if not self.warned:
                        print(f"  (can't reach the screen: {exc}; is AULA's driver open?)")
                        self.warned = True
                    self.close()
        return None

    def page(self):
        return self.read(0x2000)

    def write(self, addr: int, data: bytes) -> bool:
        """Write screen variables (2 bytes each) starting at addr. The screen sends no reply."""
        if len(data) % 2:
            data += b"\0"
        with self.lock:
            try:
                if self.ser is None:
                    self._open()
                self.ser.write(self._frame(bytes([0x10, addr >> 8, addr & 0xFF]) + data))
                self.ser.flush()
                self.last_use = time.time()
                time.sleep(0.02)
                return True
            except Exception:
                self.close()
                return False

    def close_if_idle(self):
        with self.lock:
            if self.ser is not None and time.time() - self.last_use > 3:
                self.close()

    def close(self):
        with self.lock:
            try:
                if self.ser is not None:
                    self.ser.close()
            finally:
                self.ser = None


def run(cfg: dict) -> None:
    """Every numpad key is held back for a moment while we ask the screen which page
    is open. On the Deck page the tile's action runs; anywhere else the key is
    sent on to Windows unchanged (so Mini Numpad works normally)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "L99DeckRunner")
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        sys.exit("L99 Deck is already running.")
    state = {"tiles": (cfg["tiles"] + [{}] * 12)[:12],
             "mtime": CONFIG.stat().st_mtime if CONFIG.exists() else 0}

    def current_tiles():
        try:
            m = CONFIG.stat().st_mtime
            if m != state["mtime"]:
                state["cfg"] = json.loads(CONFIG.read_text(encoding="utf-8"))
                state["tiles"] = (state["cfg"]["tiles"] + [{}] * 12)[:12]
                state["mtime"] = m
                print("(deck.json reloaded)")
        except Exception as exc:
            print(f"  (deck.json not reloaded: {exc})")
        return state["tiles"]

    jobs: queue.Queue = queue.Queue()
    link = ScreenLink()
    decision = {}

    def reinject(vk, scan, flags, up):
        f = (KEYEVENTF_EXTENDEDKEY if flags & LLKHF_EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        user32.keybd_event(vk, scan, f, 0)

    def worker():
        while True:
            try:
                job = jobs.get(timeout=1)
            except queue.Empty:
                link.close_if_idle()
                continue
            kind, vk, scan, flags = job
            key = (vk, scan, flags & LLKHF_EXTENDED)
            if kind == "down":
                page = link.page()
                if page is None and link.missing and state.get("cfg", cfg).get("wireless", "deck") == "deck":
                    page = DECK_PAGE           # wireless: the page can't be read, numpad keys = Deck tiles
                if page == DECK_PAGE:
                    decision[key] = "deck"
                    if not flags & LLKHF_EXTENDED and scan in TILE_SCANS:
                        threading.Thread(target=run_tile, args=(current_tiles()[TILE_SCANS.index(scan)],), daemon=True).start()
                else:
                    decision[key] = "pass"
                    reinject(vk, scan, flags, False)
            else:
                if decision.pop(key, "pass") == "pass":
                    reinject(vk, scan, flags, True)
    threading.Thread(target=worker, daemon=True).start()
    try:
        from l99_live import LiveInfo
        LiveInfo(link, lambda: (current_tiles(), state.get("cfg", cfg))[1]).start()
    except Exception as exc:
        print(f"  (live page off: {exc})")

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t

    def is_keypad(k) -> bool:
        ext = k.flags & LLKHF_EXTENDED
        return (k.vkCode == VK_NUMLOCK or (k.vkCode == VK_RETURN and ext) or (k.scanCode == 0x35 and ext)
                or (not ext and k.scanCode in KEYPAD_SCANS))

    @HOOKPROC
    def hook(n, wparam, lparam):
        if n == 0:
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not k.flags & LLKHF_INJECTED and is_keypad(k):
                jobs.put(("down" if wparam in (0x100, 0x104) else "up", k.vkCode, k.scanCode, k.flags))
                return 1
        return user32.CallNextHookEx(None, n, wparam, lparam)

    handle = user32.SetWindowsHookExW(13, hook, None, 0)
    if not handle:
        sys.exit(f"could not install keyboard hook ({ctypes.get_last_error()})")
    print("L99 Deck (by Rindobolical) is running. Open Deck on the keyboard screen. Close this window to stop.")
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(handle)


def main() -> None:
    cfg = load_config()
    if "--preview" in sys.argv:
        render(cfg, HERE / "deck_preview.png")
        print(f"wrote {HERE / 'deck_preview.png'}")
    elif "--upload" in sys.argv:
        addr = int(sys.argv[sys.argv.index("--addr") + 1], 0) if "--addr" in sys.argv else int(str(cfg.get("deck_addr", DECK_ADDR)), 0)
        upload(cfg, addr)
    else:
        if os.name != "nt":
            sys.exit("The Deck runner works on Windows only.")
        run(cfg)


if __name__ == "__main__":
    main()
