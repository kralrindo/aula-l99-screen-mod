"""Encode an image as an LT 01/03 palette+RLE resource (the format the L99 uses for page backgrounds)."""
import struct
from PIL import Image

def to565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)

DECK_LT_SIZE = 120_000   # fixed size of the deck picture, so the PC app can replace it in place


def encode_lt(img: Image.Image, fixed_total: int | None = None) -> bytes:
    img = img.convert("RGB")
    w, h = img.size
    q = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette()[:768]
    pal += [0] * (768 - len(pal))
    palette = b"".join(struct.pack("<H", to565(pal[3*i], pal[3*i+1], pal[3*i+2])) for i in range(256))
    idx = q.tobytes()
    runs = []                                # [length, colour]
    for y in range(h):                       # runs never cross a row, like AULA's files
        row = idx[y * w:(y + 1) * w]
        x = 0
        while x < w:
            c = row[x]; n = 1
            while x + n < w and row[x + n] == c and n < 256:
                n += 1
            runs.append([n, c]); x += n
    if fixed_total is not None:
        want = (fixed_total - 528) // 2
        if len(runs) > want or (fixed_total - 528) % 2:
            raise ValueError(f"picture too detailed: {len(runs)} runs > {want}")
        # pad by splitting the longest runs; the decoded picture is unchanged
        import heapq
        heap = [(-n, k) for k, (n, c) in enumerate(runs)]
        heapq.heapify(heap)
        extra = want - len(runs)
        splits = {}
        while extra:
            n, k = heapq.heappop(heap); n = -n
            if n < 2:
                raise ValueError("cannot pad further")
            splits[k] = splits.get(k, 1) + 1
            extra -= 1
            heapq.heappush(heap, (-(runs[k][0] // splits[k]), k))
        out = []
        for k, (n, c) in enumerate(runs):
            parts = splits.get(k, 1)
            base, rem = divmod(n, parts)
            out += [[base + (1 if p < rem else 0), c] for p in range(parts)]
        runs = out
    rle = bytearray()
    for n, c in runs:
        rle += bytes([n - 1, c])
    total = 16 + 512 + len(rle)
    header = b"LT" + struct.pack("<HH", w, h) + bytes([1, 3]) + struct.pack("<II", total, len(rle) | 0x01000000)
    return header + palette + bytes(rle)

def decode_lt(data: bytes) -> Image.Image:
    w, h = struct.unpack_from("<HH", data, 2); total = struct.unpack_from("<I", data, 8)[0]
    pal = [struct.unpack_from("<H", data, 16 + 2*i)[0] for i in range(256)]
    pal = [(((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31) for v in pal]
    out = bytearray()
    for i in range(528, total, 2):
        out += bytes(pal[data[i + 1]]) * (data[i] + 1)
    assert len(out) == w * h * 3, (len(out), w * h * 3)
    return Image.frombytes("RGB", (w, h), bytes(out))


# The screen only accepts an upload whose first bytes are a normal picture header
# (size, width, height, 0, CRC16 of the pixels - the format AULA's Image2Bin makes).
# So the Deck picture is wrapped in one: [11-byte header][5 pad][LT resource][zeros]
# = exactly one 320x480 "picture". The page background points at the LT part.
DECK_LT_OFFSET = 16
DECK_BLOB_SIZE = 11 + 320 * 480 * 2


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def deck_blob(img: Image.Image) -> bytes:
    lt = encode_lt(img, DECK_LT_SIZE)
    payload = bytearray(DECK_BLOB_SIZE - 11)
    payload[DECK_LT_OFFSET - 11:DECK_LT_OFFSET - 11 + len(lt)] = lt
    header = struct.pack("<IHHB", len(payload), 320, 480, 0) + struct.pack("<H", _crc16(bytes(payload)))
    return header + bytes(payload)
