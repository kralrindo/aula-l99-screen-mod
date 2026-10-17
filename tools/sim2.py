"""Re-read a built image the way the screen does and draw some pages for both languages.
python sim2.py IMAGE.bin OUT.png [pages...]"""
import struct, sys
sys.path.insert(0, __import__('os').path.dirname(__file__) or '.')
from lt_encode import decode_lt
from PIL import Image, ImageDraw

d = open(sys.argv[1], 'rb').read()
pages = [int(p) for p in sys.argv[3:]] or [1, 24, 30, 31, 14]
pt, npages = struct.unpack_from('<II', d, 8)
bg, nbg = struct.unpack_from('<II', d, 32)
it, nimg = struct.unpack_from('<II', d, 48)
VAR0 = struct.unpack_from('<I', d, 0)[0]


def pic(index, lang):
    a, w, h, p = struct.unpack_from('<IHHI', d, it + 12 * (lang * nimg + index))
    fmt = p >> 24
    if fmt not in (0, 2) or not w:
        return None
    v = struct.unpack_from(f'<{w*h}H', d, a)
    im = Image.new('RGBA', (w, h))
    if fmt == 2:      # ARGB4444
        im.putdata([(((x >> 8) & 15) * 17, ((x >> 4) & 15) * 17, (x & 15) * 17, (x >> 12) * 17) for x in v])
    else:
        im.putdata([(((x >> 11) & 31) * 255 // 31, ((x >> 5) & 63) * 255 // 63, (x & 31) * 255 // 31, 255) for x in v])
    return im


def var(v, n=2):
    return d[VAR0 + 2 * v:VAR0 + 2 * v + n]


def blocks(p):
    da, dl = struct.unpack_from('<IH', d, pt + 6 * (2 * p))
    ta, tl = struct.unpack_from('<IH', d, pt + 6 * (2 * p + 1))
    disp, i = [], da
    while i < da + dl:
        n = d[i + 4]; disp.append(d[i:i + 5 + n]); i += 5 + n
    touch, i = [], ta
    while i < ta + tl:
        n = d[i + 2]; touch.append(d[i:i + 3 + n]); i += 3 + n
    return disp, touch


def render(p, lang, demo=None):
    a, w, h, lf = struct.unpack_from('<IHHI', d, bg + 12 * p)
    im = Image.new('RGBA', (320, 480), (0, 0, 0, 255))
    if lf >> 24 == 3:
        im.paste(decode_lt(d[a:a + (lf & 0xFFFFFF)]))
    dr = ImageDraw.Draw(im)
    disp, touch = blocks(p)
    for wd in disp:
        t = wd[0]
        if t == 3:
            v, x, y, i0, i1, lo, hi = struct.unpack_from('<7H', wd, 5)
            val = int.from_bytes(var(v), 'big') if i1 != 0xFFFF else 0
            ic = pic(i0 + max(0, val - lo), lang)
            if ic: im.alpha_composite(ic, (x, y))
        elif t == 4:
            v, mode, x, y = struct.unpack_from('<HBHH', wd, 5)
            nd = wd[12]; i0 = struct.unpack_from('<H', wd, 15)[0]
            val = (demo or {}).get(v, 0)
            s = str(val)
            for k, ch in enumerate(s):        # flags 2 = right-aligned, x is the right edge
                ic = pic(i0 + int(ch), lang)
                if ic: im.alpha_composite(ic, (x - (len(s) - k) * ic.width, y))
        elif t == 1:
            v, n, x1, y1, x2, y2 = struct.unpack_from('<6H', wd, 5)
            size = wd[17]
            txt = (demo or {}).get(v) or var(v, n).split(b'\0')[0].decode('latin1')
            dr.rectangle((x1, y1, x2, y2), outline=(80, 80, 0))
            dr.text((x1 + 2, y1 + 2), txt[:n], fill=(255, 255, 255))
    for tw in touch:
        if tw[0] == 1:
            x1, y1, x2, y2, ret, ui, pi, goto = struct.unpack_from('<8H', tw, 3)
            dr.rectangle((x1, y1, x2, y2), outline=(255, 60, 60) if tw[1] & 0x40 else (60, 255, 60))
            dr.text((x1 + 2, y1 + 1), f"{ret:x}" if tw[1] & 0x40 else f"->{goto}", fill=(255, 255, 0))
    if p == 24:      # what the firmware draws
        dr.rectangle((3, 11, 202, 470), fill=(0, 0, 0))
        for cx, cy, col in ((4, 22, (255, 80, 80)), (5, 22, (255, 80, 80)), (5, 21, (255, 80, 80)), (6, 22, (255, 80, 80)), (2, 3, (80, 160, 255)), (3, 3, (80, 160, 255)), (3, 4, (80, 160, 255)), (3, 5, (80, 160, 255))):
            dr.rectangle((3 + cx * 20 + 1, 11 + cy * 20 + 1, 3 + cx * 20 + 18, 11 + cy * 20 + 18), fill=col)
    return im.convert('RGB')


demo = {0x256: 1230, 0x328: 2, 0x258: "Game Over", 0x1000: "Istanbul  18 C", 0x1010: "Parcali bulutlu - nem %62",
        0x1020: "Bohemian Rhapsody", 0x1030: "Queen", 0x1040: "Spotify  2:31 / 5:55", 0x1050: "CPU %23  RAM %41",
        0x1060: "GPU 54 C", 0x1070: "Cumartesi 17 Ekim 14:05"}
rows = []
for lang in (0, 1):
    rows.append([render(p, lang, demo) for p in pages])
out = Image.new('RGB', (330 * len(pages), 490 * 2), (60, 60, 60))
for r, row in enumerate(rows):
    for c, im in enumerate(row):
        out.paste(im, (330 * c, 490 * r))
out.save(sys.argv[2])
print('pages', npages, 'bgs', nbg, 'image table', hex(it), nimg)
