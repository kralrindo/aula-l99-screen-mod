"""Pictures for the new pages: Tetris (page 24) and Live info (page 31).

Everything that has words in it is a separate small image ("icon") so the
screen can swap it with the language (Settings > Language).
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 320, 480
FONTS = ["/usr/share/fonts/opentype/inter/Inter-Bold.otf", r"C:\Windows\Fonts\segoeuib.ttf",
         r"C:\Windows\Fonts\arialbd.ttf"]
FONT = next((f for f in FONTS if Path(f).exists()), None)

BLACK = (0, 0, 0)
CARD = (30, 32, 42)
BTN = (44, 46, 58)
BTN_DOWN = (86, 92, 130)
LINE = (70, 72, 92)
WHITE = (255, 255, 255)
MUTED = (150, 154, 170)
ACCENT = (46, 196, 120)

# ----------------------------------------------------------------- Tetris ---
BOARD = (3, 11, 202, 470)            # drawn by the firmware (10 x 23 cells of 20 px)
PREVIEW = (208, 11, 307, 110)        # next piece, drawn by the firmware
SCORE_LABEL = (210, 118)
SCORE_DIGITS = (306, 136)            # RIGHT edge: the number widget (flags 2) right-aligns at x (seen on hardware)
LEVEL_LABEL = (210, 168)
LEVEL_DIGIT = (306, 162)             # right edge, like SCORE_DIGITS
GAMEOVER = (206, 190, 318, 210)      # text variable 0x258 ("Game Over !!!", first 9 letters)
# (name, x1, y1, x2, y2, report value): 0x50 W rotate, 0x51 S down, 0x52 A left, 0x53 D right,
# 0x54 space start/pause, 0x55 new game; "home" = page jump
GAME_BUTTONS = [
    ("rotate", 208, 214, 316, 264, 0x50),
    ("left", 208, 270, 259, 326, 0x52),
    ("right", 265, 270, 316, 326, 0x53),
    ("down", 208, 332, 316, 382, 0x51),
    ("pause", 208, 388, 259, 432, 0x54),
    ("new", 265, 388, 316, 432, 0x55),
    ("home", 208, 438, 316, 474, None),
]


def _font(size):
    return ImageFont.truetype(FONT, size)


def _glyph(d: ImageDraw.ImageDraw, name, cx, cy, s, col=WHITE):
    """Simple vector glyphs, s = half size."""
    if name == "left":
        d.polygon([(cx - s, cy), (cx + s * 0.6, cy - s), (cx + s * 0.6, cy + s)], fill=col)
    elif name == "right":
        d.polygon([(cx + s, cy), (cx - s * 0.6, cy - s), (cx - s * 0.6, cy + s)], fill=col)
    elif name == "down":
        d.polygon([(cx, cy + s), (cx - s, cy - s * 0.6), (cx + s, cy - s * 0.6)], fill=col)
    elif name == "rotate":
        w = max(3, s // 3)
        d.arc((cx - s, cy - s, cx + s, cy + s), 200, 500, fill=col, width=w)
        # arrow head at the arc's end (angle 140 deg)
        import math
        a = math.radians(140)
        ex, ey = cx + s * math.cos(a), cy + s * math.sin(a)
        d.polygon([(ex - s * 0.55, ey - s * 0.15), (ex + s * 0.45, ey - s * 0.25), (ex - s * 0.05, ey + s * 0.6)], fill=col)
    elif name == "pause":
        d.polygon([(cx - s, cy - s * 0.8), (cx - s, cy + s * 0.8), (cx - s * 0.1, cy)], fill=col)
        d.rectangle((cx + s * 0.15, cy - s * 0.8, cx + s * 0.45, cy + s * 0.8), fill=col)
        d.rectangle((cx + s * 0.7, cy - s * 0.8, cx + s * 1.0, cy + s * 0.8), fill=col)
    elif name == "new":
        w = max(4, s // 3)
        d.rectangle((cx - w // 2, cy - s * 0.85, cx + w // 2, cy + s * 0.85), fill=col)
        d.rectangle((cx - s * 0.85, cy - w // 2, cx + s * 0.85, cy + w // 2), fill=col)
    elif name == "home":
        d.polygon([(cx, cy - s), (cx - s * 1.1, cy), (cx + s * 1.1, cy)], fill=col)
        d.rectangle((cx - s * 0.75, cy, cx + s * 0.75, cy + s * 0.9), fill=col)
        d.rectangle((cx - s * 0.2, cy + s * 0.35, cx + s * 0.2, cy + s * 0.9), fill=BTN)


def button_image(name, w, h, pressed=False, scale=4):
    big = Image.new("RGB", (w * scale, h * scale), BLACK)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius=10 * scale,
                        fill=BTN_DOWN if pressed else BTN)
    s = int(min(w, h) * scale * (0.26 if name != "home" else 0.3))
    _glyph(d, name, w * scale // 2, h * scale // 2, s)
    return big.resize((w, h), Image.LANCZOS)


def text_image(text, size, fg=MUTED, bg=BLACK, pad=(0, 0)):
    f = _font(size * 4)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    l, t, r, b = tmp.textbbox((0, 0), text, font=f, anchor="ls")
    asc, desc = -f.getbbox("ÇĞİÖŞÜ", anchor="ls")[1], f.getbbox("gşç", anchor="ls")[3]
    w4, h4 = r - l + pad[0] * 8, asc + desc + pad[1] * 8
    w4 += (-w4) % 4; h4 += (-h4) % 4
    big = Image.new("RGB", (w4, h4), bg)
    ImageDraw.Draw(big).text((pad[0] * 4 - l, pad[1] * 4 + asc), text, font=f, fill=fg, anchor="ls")
    return big.resize((w4 // 4, h4 // 4), Image.LANCZOS)


def game_background():
    im = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(im)
    x1, y1, x2, y2 = BOARD
    d.rectangle((x1 - 2, y1 - 2, x2 + 2, y2 + 2), outline=LINE, width=1)
    px1, py1, px2, py2 = PREVIEW
    d.rectangle((px1 - 2, py1 - 2, px2 + 2, py2 + 2), outline=LINE, width=1)
    for name, bx1, by1, bx2, by2, _ in GAME_BUTTONS:
        im.paste(button_image(name, bx2 - bx1, by2 - by1), (bx1, by1))
    return im


GAME_TEXT = {"score": {"tr": "SKOR", "en": "SCORE"}, "level": {"tr": "SEVİYE", "en": "LEVEL"}}

# ------------------------------------------------------------- Live info ---
LIVE_TITLE_Y = 40
LIVE_CARDS = [  # (y1, y2, label key)
    (90, 196, "weather"),
    (204, 334, "music"),
    (342, 470, "info"),
]
LIVE_LABEL_POS = [(22, 99), (22, 213), (22, 351)]
# text variables the PC writes: (var, x1, y1, x2, y2, font, colour, max letters)
LIVE_LINES = [
    (0x1000, 22, 120, 298, 154, 32, WHITE, 18),
    (0x1010, 22, 162, 298, 184, 20, MUTED, 28),
    (0x1020, 22, 234, 298, 256, 20, WHITE, 28),
    (0x1030, 22, 264, 298, 286, 20, MUTED, 28),
    (0x1040, 22, 296, 298, 318, 20, MUTED, 28),
    (0x1050, 22, 374, 298, 396, 20, WHITE, 28),
    (0x1060, 22, 404, 298, 426, 20, MUTED, 28),
    (0x1070, 22, 434, 298, 456, 20, MUTED, 28),
]
LIVE_TEXT = {
    "title": {"tr": "Canlı Bilgi", "en": "Live Info"},
    "weather": {"tr": "HAVA DURUMU", "en": "WEATHER"},
    "music": {"tr": "ŞİMDİ ÇALIYOR", "en": "NOW PLAYING"},
    "info": {"tr": "SİSTEM", "en": "SYSTEM"},
}


def live_background():
    im = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(im)
    for y1, y2, _ in LIVE_CARDS:
        d.rounded_rectangle((10, y1, 310, y2), radius=14, fill=CARD)
    return im


def to565(c):
    r, g, b = c
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


if __name__ == "__main__":
    out = Path(__file__).parent / "gen"
    out.mkdir(exist_ok=True)
    g = game_background()
    d = ImageDraw.Draw(g)
    for lang, ox in (("tr", 0),):
        g.paste(text_image(GAME_TEXT["score"][lang], 12), SCORE_LABEL)
        g.paste(text_image(GAME_TEXT["level"][lang], 12), LEVEL_LABEL)
    g.save(out / "game_preview.png")
    lv = live_background()
    lv.paste(text_image(LIVE_TEXT["title"]["tr"], 18, fg=WHITE), (160 - 50, LIVE_TITLE_Y))
    for (key), pos in zip(["weather", "music", "info"], LIVE_LABEL_POS):
        lv.paste(text_image(LIVE_TEXT[key]["tr"], 11, fg=ACCENT, bg=CARD), pos)
    lv.save(out / "live_preview.png")
    s = Image.new("RGB", (660, 480), (60, 60, 60)); s.paste(g, (0, 0)); s.paste(lv, (340, 0))
    s.save(out / "pages_preview.png")
