"""Build an L99 UI image with a new Deck app on page 30.

- copies the page table and page-background table to free space and adds page 30
- page 30 = full-screen picture (the deck tiles) + back button + N report buttons
- home menu: new icon in the free slot (row 3, column 3) that opens page 30
Input: original verified image; output: new image + a compact patch file.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from l99_assets import encode  # noqa: E402
from lt_encode import encode_lt, DECK_LT_SIZE, deck_blob, DECK_LT_OFFSET  # noqa: E402

PAGE_TABLE, BG_TABLE = 0x21DD0, 0x5C008E
FREE = 0x00B00000            # inside the zero-filled gap (0xA355EC..0x4000000), below 16 MB
NEW_PT = FREE                # page table   (62 x 6 bytes)
NEW_HOME_TOUCH = FREE + 0x2000
NEW_P30_DISP = FREE + 0x3000
NEW_P30_TOUCH = FREE + 0x4000
MOVED_LT0 = FREE + 0x10000   # page 5's background, moved so the bg table can grow in place
DECK_IMAGE = int(__import__("os").environ.get("DECK_IMAGE", hex(FREE + 0x20000)), 0)  # 128 KB aligned
DECK_MAX = 0x60000
DECK_PAGE = 30
HOME_PAGE, NUMPAD_PAGE = 1, 21
SPRITE_OFF = 0x625FDC        # home-menu sprite (asset 000)

MARKERS = __import__("os").environ.get("MARKERS", "0") == "1"   # v4 used signal keys; v5 reads the page instead
MARK_NUMPAD = 0x30   # keypad NumLock: "Mini Numpad is open / Deck closed"
MARK_DECK = 0x3E     # keypad Enter:   "Deck is open"
# Deck tiles, row by row: keypad 7 8 9 / 4 5 6 / 1 2 3 / 0 - +
DECK_CODES = [0x34, 0x35, 0x36, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3F, 0x33, 0x37]
NUMPAD_ICONS = {319: (67, 67), 320: (67, 67), 351: (67, 138), 352: (67, 138)}  # Num, Enter (normal/pressed)

# deck grid (must match make_deck_image.py)
X0, Y0, TW, TH, GX, GY, COLS, ROWS = 16, 125, 90, 78, 11, 10, 3, 4


def button(x1, y1, x2, y2, ret=0x20, goto=0xFFFF, report=False, icons=(0xFFFF, 0xFFFF)) -> bytes:
    return bytes([0x01, 0xC0 if report else 0x80, 0x10]) + struct.pack(
        "<8H", x1, y1, x2, y2, ret, icons[0], icons[1], goto)


def entries(d: bytes):
    return [list(struct.unpack_from("<IH", d, PAGE_TABLE + 6 * i)) for i in range(60)]


def display_widgets(d: bytes, addr: int, length: int):
    out, i = [], addr
    while i < addr + length:
        n = d[i + 4]
        out.append(d[i:i + 5 + n])
        i += 5 + n
    return out


def icon_offset(d: bytes, index: int) -> int:
    table = 0x622A84
    start = struct.unpack_from("<I", d, table)[0]
    for i in range(index + 1):
        w, h, packed, end = struct.unpack_from("<HHII", d, table + 4 + i * 12)
        if i == index:
            assert (packed & 0xFFFFFF) == w * h * 2 and end - start == w * h * 2
            return start
        start = end


def blank_key(d: bytes, off: int, w: int, h: int) -> bytearray:
    """Copy of a numpad key picture with its label removed and the face dimmed."""
    px = list(struct.unpack_from(f"<{w*h}H", d, off))
    inner = [px[y * w + x] for y in range(10, h - 10) for x in range(10, w - 10)]
    face = max(set(inner), key=inner.count)
    out = bytearray()
    for y in range(h):
        for x in range(w):
            v = px[y * w + x]
            if 10 <= x < w - 10 and 10 <= y < h - 10:
                v = face
            if v == 0x0FFF:
                out += bytes((0, 0, 0, 0)); continue
            r, g, b = ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31
            out += bytes((r * 2 // 5, g * 2 // 5, b * 2 // 5, 255))
    return out


def build(src: Path, sprite_png: Path, deck_png: Path, codes: list[int], dst: Path) -> None:
    d = bytearray(src.read_bytes())
    assert struct.unpack_from("<II", d, 8) == (PAGE_TABLE, 30), "unexpected header (page table)"
    assert struct.unpack_from("<II", d, 32) == (BG_TABLE, 30), "unexpected header (backgrounds)"
    assert not any(d[FREE:FREE + 0x20000]), "free area is not empty"
    assert not any(d[DECK_IMAGE:DECK_IMAGE + DECK_MAX]), "deck picture area is not empty"

    ents = entries(d)
    # home touch block + one new button over the free slot
    ha, hl = ents[2 * HOME_PAGE + 1]
    home_touch = bytearray(d[ha:ha + hl])
    if MARKERS:
        for i in range(0, len(home_touch), 19):        # Mini Numpad button: also send the numpad signal
            if struct.unpack_from("<H", home_touch, i + 3 + 14)[0] == NUMPAD_PAGE:
                home_touch[i + 1] = 0xC0
                struct.pack_into("<H", home_touch, i + 3 + 8, MARK_NUMPAD)
    home_touch = bytes(home_touch) + button(163, 270, 223, 339, ret=MARK_DECK if MARKERS else 0x20,
                                            report=MARKERS, goto=DECK_PAGE)
    d[NEW_HOME_TOUCH:NEW_HOME_TOUCH + len(home_touch)] = home_touch
    ents[2 * HOME_PAGE + 1] = [NEW_HOME_TOUCH, len(home_touch)]

    # page 30 display: the numpad page's status bar + back arrow, without its title
    na, nl = ents[2 * NUMPAD_PAGE]
    widgets = [w for w in display_widgets(d, na, nl) if 0x013E not in struct.unpack_from(f"<{(len(w) - 5) // 2}H", w, 5)]
    p30_disp = b"".join(widgets)
    d[NEW_P30_DISP:NEW_P30_DISP + len(p30_disp)] = p30_disp

    # page 30 touch: deck buttons + back button (same rectangle as other pages)
    touch = b""
    for k, code in enumerate(codes):
        c, r = k % COLS, k // COLS
        x, y = X0 + c * (TW + GX), Y0 + r * (TH + GY)
        for one in (code if isinstance(code, list) else [code]):   # list = overlapping buttons
            touch += button(x, y, x + TW, y + TH, ret=one, report=True)
    touch += button(10, 28, 89, 77, ret=MARK_NUMPAD if MARKERS else 0x20, report=MARKERS, goto=HOME_PAGE)

    # Mini Numpad page: Num and Enter become blank, inactive keys (their codes are the signals)
    ta, tl = ents[2 * NUMPAD_PAGE + 1] if MARKERS else (0, 0)
    for i in range(ta, ta + tl, 19):
        if d[i] == 1 and struct.unpack_from("<H", d, i + 3 + 8)[0] in (MARK_NUMPAD, MARK_DECK):
            d[i + 1] = 0x80
            struct.pack_into("<H", d, i + 3 + 8, 0x0020)
    for index, (w, h) in (NUMPAD_ICONS.items() if MARKERS else []):
        off = icon_offset(d, index)
        img = Image.frombytes("RGBA", (w, h), bytes(blank_key(d, off, w, h)))
        d[off:off + w * h * 2] = encode(img, 2, (0, 0, 0))
    d[NEW_P30_TOUCH:NEW_P30_TOUCH + len(touch)] = touch

    # page table with 31 pages
    ents += [[NEW_P30_DISP, len(p30_disp)], [NEW_P30_TOUCH, len(touch)]]
    for i, (a, l) in enumerate(ents):
        struct.pack_into("<IH", d, NEW_PT + 6 * i, a, l)

    # backgrounds: the table stays where it is and grows to 31 records. The 12 bytes
    # after it belong to page 5's background (an LT resource), so move that first.
    lt0 = BG_TABLE + 360
    assert d[lt0:lt0 + 2] == b"LT"
    lt0_size = struct.unpack_from("<I", d, lt0 + 8)[0]
    d[MOVED_LT0:MOVED_LT0 + lt0_size] = d[lt0:lt0 + lt0_size]
    for rec in range(30):
        if struct.unpack_from("<I", d, BG_TABLE + 12 * rec)[0] == lt0:
            struct.pack_into("<I", d, BG_TABLE + 12 * rec, MOVED_LT0)
    deck = Image.open(deck_png).convert("RGB")
    assert deck.size == (320, 480)
    blob = deck_blob(deck)
    assert len(blob) <= DECK_MAX
    d[DECK_IMAGE:DECK_IMAGE + len(blob)] = blob
    struct.pack_into("<IHHI", d, lt0, DECK_IMAGE + DECK_LT_OFFSET, 320, 480, (3 << 24) | DECK_LT_SIZE)

    # home sprite with the new icon
    sprite = Image.open(sprite_png).convert("RGBA")
    assert sprite.size == (287, 272)
    d[SPRITE_OFF:SPRITE_OFF + 287 * 272 * 2] = encode(sprite, 2, (0, 0, 0))

    # header: point at the new tables
    struct.pack_into("<II", d, 8, NEW_PT, 31)
    struct.pack_into("<II", d, 32, BG_TABLE, 31)

    dst.write_bytes(d)
    make_patch(src.read_bytes(), bytes(d), dst.with_suffix(".patch"))
    print(f"wrote {dst}  sha1 {hashlib.sha1(d).hexdigest()}")


def make_patch(old: bytes, new: bytes, path: Path) -> None:
    """Patch = list of changed ranges, zlib-compressed, plus both sha1s."""
    ranges, i, n = [], 0, len(old)
    step = 4096
    while i < n:
        if old[i:i + step] != new[i:i + step]:
            j = i
            while j < n and old[j:j + step] != new[j:j + step]:
                j += step
            ranges.append((i, min(j, n)))
            i = j
        else:
            i += step
    blob = bytearray()
    index = []
    for a, b in ranges:
        index.append([a, b - a])
        blob += new[a:b]
    meta = {"from_sha1": hashlib.sha1(old).hexdigest(), "to_sha1": hashlib.sha1(new).hexdigest(),
            "size": len(new), "ranges": index}
    header = json.dumps(meta).encode()
    path.write_bytes(struct.pack("<I", len(header)) + header + zlib.compress(bytes(blob), 9))
    print(f"patch {path} ({path.stat().st_size // 1024} KB, {len(ranges)} ranges)")


if __name__ == "__main__":
    src, sprite, deck, dst = map(Path, sys.argv[1:5])
    codes = ([[int(p, 16) for p in c.split("+")] if "+" in c else int(c, 16) for c in sys.argv[5].split(",")]
             if len(sys.argv) > 5 else DECK_CODES)
    build(src, sprite, deck, codes, dst)
