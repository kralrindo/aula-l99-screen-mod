"""Home-menu sprites (asset 000) for both languages.

English: the original sprite + Deck and Game icons with labels.
Turkish: all labels redrawn in Turkish; the sprite grows to 287x286 so the
row-3 labels can use two lines ("Hesap / Makinesi").
Labels copy the original style: white letters, anti-aliased with real alpha.
Usage: python make_home.py ORIGINAL.bin OUT_DIR
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
             r"C:\Windows\Fonts\arial.ttf"]
FONT = next((f for f in FONT_DIRS if Path(f).exists()), None)
CX = [30, 104, 178, 252]
ROW_TOP = [59, 159, 259]          # top of the capital letters in each label row
ROW_WIPE = [(52, 100), (152, 200), (252, 300)]
LINE = 14
TR_H = 286

TR_LABELS = [
    [["Ayarlar"], ["Ekran", "Ayarları"], ["Renk"], ["Özel", "Animasyon"]],
    [["Oynat"], ["Anlık", "Veriler"], ["Fotoğraf", "Çerçevesi"], ["Mini", "Tuş Takımı"]],
    [["Hesap", "Makinesi"], ["Mouse"], ["Deck"], ["Oyun"]],
]
EN_NEW = {(2, 2): ["Deck"], (2, 3): ["Game"]}


def label_layer(size, labels: dict) -> Image.Image:
    """labels: {(row, col): [lines]} -> RGBA layer in the original label style."""
    w, h = size
    big = 4
    mask = Image.new("L", (w * big, h * big), 0)
    d = ImageDraw.Draw(mask)
    for (row, col), lines in labels.items():
        for fs in range(13, 9, -1):
            font = ImageFont.truetype(FONT, fs * big)
            if max(d.textlength(t, font=font) for t in lines) <= 72 * big:
                break
        asc = font.getbbox("H", anchor="ls")[1]          # negative: cap height above baseline
        for i, text in enumerate(lines):
            tw = d.textlength(text, font=font)
            x = min(max(1 * big, CX[col] * big - tw / 2), w * big - tw - 1 * big)
            y = (ROW_TOP[row] + i * LINE) * big - asc
            d.text((x, y), text, font=font, fill=255, anchor="ls")
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    out.putalpha(mask)                                   # white text with real alpha (ARGB4444 on the screen)
    return out


def _icon(img, x, y, colour, art):
    """50x50 rounded icon drawn 4x larger and scaled down (anti-aliased like the originals)."""
    k = 4
    big = Image.new("RGBA", (51 * k, 51 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, 50 * k, 50 * k), radius=11 * k, fill=colour + (255,))
    art(d, k)
    img.alpha_composite(big.resize((51, 51), Image.LANCZOS), (x, y))


def deck_icon(img, x=153, y=201):
    def art(d, k):
        for n in range(4):
            cx, cy = 10 + (n % 2) * 17, 10 + (n // 2) * 17
            d.rounded_rectangle((cx * k, cy * k, (cx + 13) * k, (cy + 13) * k), radius=3 * k,
                                outline=(255, 255, 255, 255), width=2 * k)
    _icon(img, x, y, (108, 123, 255), art)


def game_icon(img, x=227, y=201):
    def art(d, k):
        s = 11
        cells = [(1, 0), (0, 1), (1, 1), (2, 1), (0, 2.2), (1, 2.2), (2, 2.2)]
        ox, oy = 25 - 3 * s / 2, 7
        for cx, cy in cells:
            x0, y0 = ox + cx * s, oy + cy * s
            d.rounded_rectangle(((x0 + 1) * k, (y0 + 1) * k, (x0 + s - 1) * k, (y0 + s - 1) * k), radius=2 * k,
                                outline=(255, 255, 255, 255), width=2 * k)
    _icon(img, x, y, (98, 196, 66), art)


def wipe(img, rows):
    px = img.load()
    for y0, y1 in rows:
        for y in range(y0, min(y1, img.height)):
            for x in range(img.width):
                px[x, y] = (0, 0, 0, 0)


def build(orig: Image.Image, out: Path):
    """orig = the original sprite in true colours (argb4444.from_image_file(bin, 0))."""
    assert orig.size == (287, 272)
    out.mkdir(parents=True, exist_ok=True)

    en = orig.copy()
    deck_icon(en); game_icon(en)
    en.alpha_composite(label_layer(en.size, EN_NEW))
    en.save(out / "home_en.png")

    tr = Image.new("RGBA", (287, TR_H), (0, 0, 0, 0))
    tr.paste(orig, (0, 0))
    wipe(tr, ROW_WIPE)
    deck_icon(tr); game_icon(tr)
    labels = {(r, c): TR_LABELS[r][c] for r in range(3) for c in range(4)}
    tr.alpha_composite(label_layer(tr.size, labels))
    tr.save(out / "home_tr.png")

    prev = Image.new("RGBA", (600, TR_H), (0, 0, 0, 255))   # true on-screen colours
    prev.alpha_composite(en, (0, 0)); prev.alpha_composite(tr, (305, 0))
    prev.resize((1200, TR_H * 2), Image.NEAREST).save(out / "home_preview.png")


if __name__ == "__main__":
    import argb4444
    build(argb4444.from_image_file(Path(sys.argv[1]).read_bytes(), 0), Path(sys.argv[2]))
