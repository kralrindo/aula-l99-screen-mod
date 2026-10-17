"""Build the modded L99 UI image (normally run through install.py).

On top of the original verified UartTFT-II_Flash.bin it adds:
  * Two languages that switch at runtime (Settings > Language): Turkish and English.
    The screen firmware looks every picture up as  language * 569 + number, but
    AULA's file only had language 0. The picture table is moved and doubled:
    entries 0-568 = first language, 569-1137 = second language. Pictures with no
    translation simply point at the same pixels.
  * Deck (page 30) exactly as in v5, plus a "Live" button.
  * Tetris (page 24): unlocked by making the UI version odd (V2.20 -> V2.21,
    config checksum re-signed), a home icon, touch buttons that feed the
    firmware's game keys, score / level / game-over widgets.
  * Live info (page 31): 8 text lines the PC fills over USB (variables 0x1000-0x1070).
Writes NEW.bin and NEW.patch (apply with apply_patch.py).

Usage: python build_l99.py ORIGINAL.bin NEW.bin   (English UI; --bilingual --first tr needs Turkish
pictures in tools/tr_assets, which are not part of this repo)
"""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from l99_assets import encode                       # noqa: E402
from lt_encode import encode_lt, deck_blob, DECK_LT_OFFSET, DECK_LT_SIZE   # noqa: E402
from build_deck import button, display_widgets, make_patch   # noqa: E402
import make_pages as mp                              # noqa: E402

PAGE_TABLE, BG_TABLE, IMG_TABLE, N_IMG = 0x21DD0, 0x5C008E, 0x622A84, 569
VAR_INIT = 0x11DD0
CONFIG = 0x80
FREE = 0x00B00000
NEW_PT = FREE
BLOCKS = FREE + 0x2000          # touch / display blocks, 0x1000 apart
MOVED_LT0 = FREE + 0x10000
NEW_IT = FREE + 0x20000         # 2 x 569 x 12 bytes
GAME_BG = FREE + 0x30000
LIVE_BG = FREE + 0x60000
HEAP = FREE + 0x100000          # pixel data of new / translated pictures
HEAP_END = 0x01000000
DECK_IMAGE = 0x04120000
HOME, NUMPAD, GAME, DECK, LIVE = 1, 21, 24, 30, 31

# new pictures live in empty slots of the table (436-499 are all empty)
IC_SCORE, IC_LEVEL, IC_LIVE_TITLE, IC_W, IC_M, IC_I = 440, 441, 442, 443, 444, 445
IC_BTN = 446                    # 7 buttons x (normal, pressed) = 446..459
DIGITS = (50, 59)               # existing 16x24 digit pictures
TITLE_W = 160


def u16s(*v):
    return struct.pack(f"<{len(v)}H", *v)


def w_icon(x, y, icon, var=0x000F):          # display widget 03: static picture
    return bytes([0x03, 0, 0xFF, 0xFF, 14]) + u16s(var, x, y, icon, 0xFFFF, 0, 0)


def w_number(var, mode, x, y, digits, icons):  # display widget 04: number from digit pictures
    return bytes([0x04, 0, 0xFF, 0xFF, 14]) + struct.pack("<HBHH", var, mode, x, y) + bytes(
        [digits, 0, 2]) + u16s(*icons)


def w_text(var, length, rect, size, colour, bg, align):   # display widget 01: text variable
    lib = 2 if size >= 32 else 3
    px = 0x20 if size >= 32 else 0x14
    c, b = mp.to565(colour), mp.to565(bg)
    return bytes([0x01, 0, 0xFF, 0xFF, 24]) + u16s(var, length, *rect) + bytes(
        [px, px, lib, 0, align]) + struct.pack("<I", c)[:3] + struct.pack("<I", b)[:3] + b"\0"


def touch_button(x1, y1, x2, y2, ret=0x20, goto=0xFFFF, report=False, icons=(0xFFFF, 0xFFFF)):
    return button(x1, y1, x2, y2, ret=ret, goto=goto, report=report, icons=icons)


def config_crc(table, data):
    c = 0
    for x in data:
        c = table[(c ^ x) & 0xFF] ^ (c >> 8)
    return c


CRC_TABLE = []
for n in range(256):
    c = n
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    CRC_TABLE.append(c)


def set_ui_version(d: bytearray, text: bytes):
    """The UI config block (0x80, 0xF4 bytes) carries the version "V2.20" at +48 and a
    CRC32 (zero start, no final xor) over the block with its own 8 bytes replaced by
    fixed values. Version number odd -> the firmware enables the game (0x812C59)."""
    blk = bytearray(d[CONFIG:CONFIG + 0xF4])
    stored = struct.unpack_from("<I", blk, 0xF0)[0]
    tmp = bytearray(blk); tmp[0xEC:0xF0] = bytes([104, 69, 113, 25]); tmp[0xF0:0xF4] = bytes([0x88, 0x96, 0x77, 0x23])
    assert config_crc(CRC_TABLE, tmp) == stored, "config checksum not understood"
    assert blk[48:53] == b"V2.20", blk[48:53]
    blk[48:48 + len(text)] = text
    tmp = bytearray(blk); tmp[0xEC:0xF0] = bytes([104, 69, 113, 25]); tmp[0xF0:0xF4] = bytes([0x88, 0x96, 0x77, 0x23])
    crc = config_crc(CRC_TABLE, tmp)
    struct.pack_into("<II", blk, 0xEC, crc ^ 0xFFFFFFFF, crc)
    d[CONFIG:CONFIG + 0xF4] = blk


class Heap:
    def __init__(self, d):
        self.d, self.pos = d, HEAP

    def put(self, data: bytes) -> int:
        a = self.pos
        assert a + len(data) < HEAP_END, "heap full"
        assert not any(self.d[a:a + len(data)]), "heap area not empty"
        self.d[a:a + len(data)] = data
        self.pos = (a + len(data) + 15) & ~15
        return a


def record(d, index):
    return bytearray(d[IMG_TABLE + 12 * index:IMG_TABLE + 12 * index + 12])


def picture_record(heap, img: Image.Image, fmt: int, matte=(0, 0, 0), argb=False) -> bytes:
    """fmt 0 = RGB565. fmt 2 = ARGB4444: argb=True encodes a true-colour RGBA picture;
    argb=False takes a PNG in l99_assets' export representation (16-bit values kept as is)."""
    if fmt == 2 and argb:
        import argb4444
        data = argb4444.encode(img)
    else:
        data = encode(img, fmt, matte)
    a = heap.put(data)
    return struct.pack("<IHHI", a, img.width, img.height, (fmt << 24) | len(data))


def swap_rows(img: Image.Image, split: int = 51, margin: int = 8) -> Image.Image:
    """Language list (2 rows, divider at rows 51-52): swap the rows' contents, keeping
    the card's rounded corners and the divider in place."""
    w, h = img.size
    top = img.crop((margin, margin, w - margin, split))
    bot = img.crop((margin, h - split + margin, w - margin, h))
    out = img.copy()
    out.paste(bot, (margin, margin)); out.paste(top, (margin, h - split + margin))
    return out


def build(src: Path, dst: Path, first: str = "en", bilingual: bool = False, patch: bool = True) -> None:
    langs = [first, "en" if first == "tr" else "tr"] if bilingual else [first]
    d = bytearray(src.read_bytes())
    assert struct.unpack_from("<II", d, 8) == (PAGE_TABLE, 30)
    assert struct.unpack_from("<II", d, 32) == (BG_TABLE, 30)
    assert struct.unpack_from("<II", d, 48) == (IMG_TABLE, N_IMG)
    assert not any(d[FREE:HEAP_END]), "free area is not empty"
    heap = Heap(d)
    gen = HERE / "gen"
    import make_home
    import argb4444
    make_home.build(argb4444.from_image_file(bytes(d), 0), gen)

    # ------------------------------------------------ page table (32 pages)
    ents = [list(struct.unpack_from("<IH", d, PAGE_TABLE + 6 * i)) for i in range(60)]
    ents += [[0, 0]] * 4
    slot = [BLOCKS]

    def put_block(data: bytes) -> list:
        a = slot[0]; slot[0] += 0x1000
        assert len(data) < 0x1000
        d[a:a + len(data)] = data
        return [a, len(data)]

    # home: Deck (row 3, col 3) and Game (row 3, col 4) buttons
    ha, hl = ents[2 * HOME + 1]
    ents[2 * HOME + 1] = put_block(bytes(d[ha:ha + hl]) + touch_button(163, 270, 223, 339, goto=DECK)
                                   + touch_button(237, 270, 297, 339, goto=GAME))

    # status bar + back arrow of the Mini Numpad page, without its title (picture 318)
    na, nl = ents[2 * NUMPAD]
    numpad_w = display_widgets(d, na, nl)
    title = next(w for w in numpad_w if w[0] == 3 and struct.unpack_from("<H", w, 11)[0] == 318)
    status = b"".join(w for w in numpad_w if w is not title)

    # page 30 Deck: as v5 + a "Live" button top right
    ents[2 * DECK] = put_block(status)
    deck_touch = b""
    X0, Y0, TW, TH, GX, GY = 16, 125, 90, 78, 11, 10
    for k, code in enumerate([0x34, 0x35, 0x36, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3F, 0x33, 0x37]):
        x, y = X0 + (k % 3) * (TW + GX), Y0 + (k // 3) * (TH + GY)
        deck_touch += touch_button(x, y, x + TW, y + TH, ret=code, report=True)
    deck_touch += touch_button(10, 28, 89, 77, goto=HOME)
    deck_touch += touch_button(222, 30, 312, 76, goto=LIVE)
    ents[2 * DECK + 1] = put_block(deck_touch)

    # page 24 Tetris
    g = []
    g.append(w_icon(*mp.SCORE_LABEL, IC_SCORE))
    g.append(w_icon(*mp.LEVEL_LABEL, IC_LEVEL))
    g.append(w_number(0x256, 2, *mp.SCORE_DIGITS, 5, DIGITS))       # score: u32 BE in vars 0x256-0x257
    g.append(w_number(0x328, 1, *mp.LEVEL_DIGIT, 1, DIGITS))        # level 0-3
    gx1, gy1, gx2, gy2 = mp.GAMEOVER
    g.append(w_text(0x258, 9, (gx1, gy1, gx2, gy2), 20, (255, 90, 90), (0, 0, 0), 1))
    ents[2 * GAME] = put_block(b"".join(g))
    t = b""
    for k, (name, x1, y1, x2, y2, code) in enumerate(mp.GAME_BUTTONS):
        icons = (IC_BTN + 2 * k, IC_BTN + 2 * k + 1)
        if code is None:
            t += touch_button(x1, y1, x2, y2, goto=HOME, icons=icons)
        else:
            t += touch_button(x1, y1, x2, y2, ret=code, report=True, icons=icons)
    ents[2 * GAME + 1] = put_block(t)

    # page 31 Live info
    lw = [status, w_icon(160 - TITLE_W // 2, title[9] | title[10] << 8, IC_LIVE_TITLE)]
    for (x, y), ic in zip(mp.LIVE_LABEL_POS, (IC_W, IC_M, IC_I)):
        lw.append(w_icon(x, y, ic))
    for var, x1, y1, x2, y2, size, colour, n in mp.LIVE_LINES:
        lw.append(w_text(var, n, (x1, y1, x2, y2), size, colour, mp.CARD, 0))
    ents[2 * LIVE] = put_block(b"".join(lw))
    ents[2 * LIVE + 1] = put_block(touch_button(10, 28, 89, 77, goto=HOME))

    for i, (a, l) in enumerate(ents):
        struct.pack_into("<IH", d, NEW_PT + 6 * i, a, l)
    struct.pack_into("<II", d, 8, NEW_PT, 32)

    # ------------------------------------------------ backgrounds (32 records)
    lt0 = BG_TABLE + 360
    assert d[lt0:lt0 + 2] == b"LT"
    lt0_size = struct.unpack_from("<I", d, lt0 + 8)[0]
    assert lt0_size >= 24
    d[MOVED_LT0:MOVED_LT0 + lt0_size] = d[lt0:lt0 + lt0_size]
    d[lt0:lt0 + lt0_size] = bytes(lt0_size)
    for rec in range(30):
        if struct.unpack_from("<I", d, BG_TABLE + 12 * rec)[0] == lt0:
            struct.pack_into("<I", d, BG_TABLE + 12 * rec, MOVED_LT0)
    deck = Image.open(HERE / "deck_default.png").convert("RGB")
    deck = draw_live_pill(deck, "Canlı" if first == "tr" else "Live")
    blob = deck_blob(deck)
    assert not any(d[DECK_IMAGE:DECK_IMAGE + len(blob)])
    d[DECK_IMAGE:DECK_IMAGE + len(blob)] = blob
    struct.pack_into("<IHHI", d, BG_TABLE + 12 * DECK, DECK_IMAGE + DECK_LT_OFFSET, 320, 480, (3 << 24) | DECK_LT_SIZE)
    for page, addr, img in ((GAME, GAME_BG, mp.game_background()), (LIVE, LIVE_BG, mp.live_background())):
        lt = encode_lt(img)
        assert len(lt) < 0x30000
        d[addr:addr + len(lt)] = lt
        struct.pack_into("<IHHI", d, BG_TABLE + 12 * page, addr, 320, 480, (3 << 24) | len(lt))
    struct.pack_into("<II", d, 32, BG_TABLE, 32)

    # ------------------------------------------------ pictures, two languages
    tables = {lang: [record(d, i) for i in range(N_IMG)] for lang in langs}

    def set_pic(lang, index, img, fmt=None, matte=(0, 0, 0), argb=False):
        if fmt is None:
            fmt = record(d, index)[11]
            fmt = 2 if fmt not in (0, 2) else fmt
        tables[lang][index] = picture_record(heap, img, fmt, matte, argb)

    def set_both(index, img, fmt=2, matte=(0, 0, 0)):
        rec = picture_record(heap, img, fmt, matte)
        for lang in langs:
            tables[lang][index] = rec

    for lang in langs:
        set_pic(lang, 0, Image.open(gen / f"home_{lang}.png"), 2, argb=True)
    tr_dir, en_dir = HERE / "tr_assets", HERE / "en_assets"
    done = []
    for f in sorted(tr_dir.glob("asset_*.png")) if "tr" in langs else []:
        i = int(f.name[6:9])
        if i in (102, 103):
            continue
        set_pic("tr", i, Image.open(f)); done.append(i)
    for f in sorted(en_dir.glob("asset_*.png")) if "en" in langs and bilingual else []:
        i = int(f.name[6:9])
        if i not in (102, 103):
            set_pic("en", i, Image.open(f))
    # language list: rows = [first language, second language]
    lp = {i: en_dir / f"asset_{i}_292x104.png" for i in (102, 103)}
    if bilingual and all(p.exists() for p in lp.values()):
        en_first = [Image.open(lp[102]), Image.open(lp[103])]          # English / Türkçe rows
        imgs = en_first if first == "en" else [swap_rows(en_first[1]), swap_rows(en_first[0])]
        set_both(102, imgs[0]); set_both(103, imgs[1])

    for lang in langs:
        T = mp.GAME_TEXT
        set_pic(lang, IC_SCORE, mp.text_image(T["score"][lang], 12), 0)
        set_pic(lang, IC_LEVEL, mp.text_image(T["level"][lang], 12), 0)
        L = mp.LIVE_TEXT
        set_pic(lang, IC_LIVE_TITLE, fixed_width(mp.text_image(L["title"][lang], 18, fg=mp.WHITE), TITLE_W), 0)
        for key, ic in (("weather", IC_W), ("music", IC_M), ("info", IC_I)):
            set_pic(lang, ic, mp.text_image(L[key][lang], 11, fg=mp.ACCENT, bg=mp.CARD), 0)
    for k, (name, x1, y1, x2, y2, code) in enumerate(mp.GAME_BUTTONS):
        set_both(IC_BTN + 2 * k, mp.button_image(name, x2 - x1, y2 - y1), 0)
        set_both(IC_BTN + 2 * k + 1, mp.button_image(name, x2 - x1, y2 - y1, pressed=True), 0)

    table = b"".join(bytes(r) for lang in langs for r in tables[lang])
    d[NEW_IT:NEW_IT + len(table)] = table
    struct.pack_into("<II", d, 48, NEW_IT, N_IMG)       # count stays per language

    # ------------------------------------------------ game switch + placeholder texts
    set_ui_version(d, b"V2.21")
    hello = {0x1000: "L99 Live", 0x1010: "PC bekleniyor..." if first == "tr" else "Waiting for the PC..."}
    for var, text in hello.items():
        raw = text.encode("ascii")
        d[VAR_INIT + 2 * var:VAR_INIT + 2 * var + len(raw)] = raw

    dst.write_bytes(d)
    if patch:
        make_patch(src.read_bytes(), bytes(d), dst.with_suffix(".patch"))
    print(f"translated pictures: {len(done)}; heap used {heap.pos - HEAP} bytes")
    print(f"wrote {dst}  sha1 {hashlib.sha1(d).hexdigest()}")


def fixed_width(img: Image.Image, w: int) -> Image.Image:
    out = Image.new("RGB", (w, img.height), (0, 0, 0))
    out.paste(img, ((w - img.width) // 2, 0))
    return out


def draw_live_pill(img: Image.Image, text: str) -> Image.Image:
    img = img.copy()
    big = img.resize((320 * 4, 480 * 4))
    dr = ImageDraw.Draw(big)
    dr.rounded_rectangle((226 * 4, 36 * 4, 308 * 4, 70 * 4), radius=17 * 4, fill=(44, 46, 58))
    dr.ellipse((238 * 4, 49 * 4, 246 * 4, 57 * 4), fill=mp.ACCENT)
    dr.text((277 * 4, 53 * 4), text, font=mp._font(14 * 4), fill=(255, 255, 255), anchor="mm")
    small = big.crop((220 * 4, 30 * 4, 316 * 4, 76 * 4)).resize((96, 46), Image.LANCZOS)
    img.paste(small, (220, 30))
    return img


if __name__ == "__main__":
    first = sys.argv[sys.argv.index("--first") + 1] if "--first" in sys.argv else "en"
    build(Path(sys.argv[1]), Path(sys.argv[2]), first, bilingual="--bilingual" in sys.argv)
