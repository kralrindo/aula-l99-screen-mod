"""Format-2 pictures of the L99 UI are ARGB4444 (top nibble = alpha), not RGB565+key.
(0x0FFF, read as "the cyan key" before, is simply fully transparent white.)"""
import struct
from PIL import Image


def decode(raw: bytes, w: int, h: int) -> Image.Image:
    vals = struct.unpack_from(f"<{w*h}H", raw)
    im = Image.new("RGBA", (w, h))
    im.putdata([(((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17, (v >> 12) * 17) for v in vals])
    return im


def encode(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    px = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
    out = []
    for r, g, b, a in px:
        q = lambda c: (c * 15 + 127) // 255
        out.append(q(a) << 12 | q(r) << 8 | q(g) << 4 | q(b))
    return struct.pack(f"<{len(out)}H", *out)


def from_image_file(data: bytes, index: int, table: int = 0x622A84) -> Image.Image:
    a, w, h, p = struct.unpack_from("<IHHI", data, table + 12 * index)
    assert p >> 24 == 2
    return decode(data[a:a + w * h * 2], w, h)
