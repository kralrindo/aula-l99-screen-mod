"""AULA L99 screen UI asset tool (export / import / verify).

Works on the UartTFT-II_Flash.bin UI image found inside AULA's L99 updater.

Image format (confirmed against the verified original image):
  * table at 0x622A84: u32 data_start, then 12-byte records
      u16 width, u16 height, u32 (format<<24 | byte_length), u32 end_offset
  * format 2 = RGB565 little-endian with a COLOUR KEY: the exact value 0x0FFF
    (a cyan) means "transparent".  Anything that is not exactly 0x0FFF is drawn
    opaque.  format 0 = plain RGB565 (no transparency).

Why the first Turkish test looked glitchy: the edited PNG's cyan background
was no longer exactly 0x0FFF (it became 0x179E), so ~48,000 pixels that should
have been see-through were drawn as a solid cyan block, and the anti-aliased
text edges were blended into that cyan.

This tool exports assets as RGBA PNGs (transparent where the key was) and
imports edited RGBA PNGs back, writing the key only where alpha is low.
Semi-transparent edge pixels are blended onto a matte colour (default black,
the UI background) so no cyan halo appears.  The source image is never
modified; import always writes a new file.

Usage:
  python l99_assets.py export  ORIGINAL.bin  assets_dir
  python l99_assets.py import  ORIGINAL.bin  assets_dir  NEW.bin [--matte 000000]
  python l99_assets.py patch   ORIGINAL.bin  NEW.bin  asset_000_287x272.png [...]
  python l99_assets.py verify  ORIGINAL.bin             (round-trip self test)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

TABLE = 0x622A84
COUNT = 0x239
KEY = 0x0FFF
EXPECTED_SIZE = 127_599_044


def entries(data: bytes):
    start = struct.unpack_from("<I", data, TABLE)[0]
    for index in range(COUNT):
        w, h, packed, end = struct.unpack_from("<HHII", data, TABLE + 4 + index * 12)
        length, fmt = packed & 0xFFFFFF, packed >> 24
        if fmt in (0, 2) and w and h and length == w * h * 2 and end - start == length and end <= len(data):
            yield index, w, h, fmt, start
        start = end


def to_rgb(v: int) -> tuple[int, int, int]:
    return ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31


def to_565(r: int, g: int, b: int) -> int:
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def decode(data: bytes, w: int, h: int, fmt: int, off: int) -> Image.Image:
    values = struct.unpack_from(f"<{w*h}H", data, off)
    px = [(0, 0, 0, 0) if fmt == 2 and v == KEY else (*to_rgb(v), 255) for v in values]
    img = Image.new("RGBA", (w, h))
    img.putdata(px)
    return img


def encode(img: Image.Image, fmt: int, matte: tuple[int, int, int]) -> bytes:
    img = img.convert("RGBA")
    out = []
    mr, mg, mb = matte
    pixels = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
    for r, g, b, a in pixels:
        if fmt == 2 and a < 128:
            out.append(KEY)
            continue
        if a < 255:  # blend soft edges onto the matte so no key-colour halo
            r = (r * a + mr * (255 - a)) // 255
            g = (g * a + mg * (255 - a)) // 255
            b = (b * a + mb * (255 - a)) // 255
        v = to_565(r, g, b)
        if fmt == 2 and v == KEY:
            v = 0x0FFE  # a visible pixel must never accidentally become transparent
        out.append(v)
    return struct.pack(f"<{len(out)}H", *out)


def load(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) != EXPECTED_SIZE or struct.unpack_from("<I", data, TABLE)[0] != 0x625FDC:
        sys.exit(f"{path} does not look like the L99 UI image this tool was mapped against")
    return data


def cmd_export(src: Path, out: Path) -> None:
    data = load(src)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, w, h, fmt, off in entries(data):
        name = f"asset_{index:03d}_{w}x{h}.png"
        decode(data, w, h, fmt, off).save(out / name)
        raw = data[off:off + w * h * 2]
        manifest.append(dict(index=index, file=name, w=w, h=h, format=fmt, offset=off,
                             sha1=hashlib.sha1(raw).hexdigest()))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"exported {len(manifest)} assets to {out}")


def cmd_import(src: Path, folder: Path, dst: Path, matte: tuple[int, int, int]) -> None:
    if dst.exists():
        sys.exit(f"refusing to overwrite {dst}")
    data = bytearray(load(src))
    manifest = json.loads((folder / "manifest.json").read_text())
    changed = 0
    for m in manifest:
        img = Image.open(folder / m["file"])
        if img.size != (m["w"], m["h"]):
            sys.exit(f"{m['file']}: must stay {m['w']}x{m['h']}, got {img.size[0]}x{img.size[1]}")
        if img.mode != "RGBA" and m["format"] == 2:
            print(f"warning: {m['file']} has no transparency; it will be fully opaque")
        raw = encode(img, m["format"], matte)
        old = bytes(data[m["offset"]:m["offset"] + len(raw)])
        if raw != old:
            data[m["offset"]:m["offset"] + len(raw)] = raw
            changed += 1
            print(f"patched #{m['index']:03d} {m['file']}")
    shutil.copyfile(src, dst)  # keep file metadata, then write content
    dst.write_bytes(data)
    # sanity: only asset regions may differ
    a, b = src.read_bytes(), dst.read_bytes()
    regions = [(m["offset"], m["offset"] + m["w"] * m["h"] * 2) for m in manifest]
    for i in range(0, len(a), 1 << 16):
        if a[i:i + 65536] != b[i:i + 65536]:
            for j in range(i, min(i + 65536, len(a))):
                if a[j] != b[j] and not any(s <= j < e for s, e in regions):
                    sys.exit(f"BUG: byte 0x{j:X} outside any asset changed; do not flash {dst}")
    print(f"{changed} assets changed -> {dst}")


def cmd_patch(src: Path, dst: Path, pngs: list[Path], matte) -> None:
    """Patch individual assets; the index comes from the file name (asset_NNN_WxH.png)."""
    if dst.exists():
        sys.exit(f"refusing to overwrite {dst}")
    data = bytearray(load(src))
    table = {i: (w, h, f, o) for i, w, h, f, o in entries(data)}
    for png in pngs:
        index = int(png.name.split("_")[1])
        w, h, fmt, off = table[index]
        img = Image.open(png)
        if img.size != (w, h):
            sys.exit(f"{png.name}: must be {w}x{h}, got {img.size[0]}x{img.size[1]}")
        raw = encode(img, fmt, matte)
        data[off:off + len(raw)] = raw
        print(f"patched #{index:03d} at 0x{off:X} ({w}x{h})")
    dst.write_bytes(data)
    print(f"wrote {dst}\nsha1 {hashlib.sha1(data).hexdigest()}")


def cmd_verify(src: Path) -> None:
    data = load(src)
    n = 0
    for index, w, h, fmt, off in entries(data):
        raw = data[off:off + w * h * 2]
        if encode(decode(data, w, h, fmt, off), fmt, (0, 0, 0)) != raw:
            sys.exit(f"round-trip mismatch on asset {index}")
        n += 1
    print(f"round-trip OK: {n} assets re-encode byte-identical")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("src", type=Path); e.add_argument("out", type=Path)
    i = sub.add_parser("import"); i.add_argument("src", type=Path); i.add_argument("folder", type=Path)
    i.add_argument("dst", type=Path); i.add_argument("--matte", default="000000")
    v = sub.add_parser("verify"); v.add_argument("src", type=Path)
    q = sub.add_parser("patch"); q.add_argument("src", type=Path); q.add_argument("dst", type=Path)
    q.add_argument("pngs", type=Path, nargs="+"); q.add_argument("--matte", default="000000")
    a = p.parse_args()
    if a.cmd == "export":
        cmd_export(a.src, a.out)
    elif a.cmd == "patch":
        m = a.matte
        cmd_patch(a.src, a.dst, a.pngs, (int(m[0:2], 16), int(m[2:4], 16), int(m[4:6], 16)))
    elif a.cmd == "import":
        m = a.matte
        cmd_import(a.src, a.folder, a.dst, (int(m[0:2], 16), int(m[2:4], 16), int(m[4:6], 16)))
    else:
        cmd_verify(a.src)


if __name__ == "__main__":
    main()
