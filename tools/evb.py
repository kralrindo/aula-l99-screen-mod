"""Unpack an Enigma Virtual Box executable (the format of AULA's screen updater).

AULA ships the L99 screen updater as one .exe with its files stored inside,
uncompressed. This reads that file table and copies the files out.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

MAGIC = b"EVB\x00"
NODE = struct.Struct("<I8sI")
FILE_INFO = struct.Struct("<2sI4s8s8s8s15sI")


def _name(data: bytes, pos: int) -> tuple[str, int, int]:
    end = pos
    while data[end:end + 2] != b"\0\0":
        end += 2
    return data[pos:end].decode("utf-16-le"), data[end + 2], end + 3


def list_files(data: bytes) -> list[tuple[str, int, int]]:
    """[(relative path, offset, size)] of every file inside the executable."""
    start = data.find(MAGIC)
    if start < 0:
        raise ValueError("not an Enigma Virtual Box executable")
    pos = start + 64
    main_size, _, root_count = NODE.unpack_from(data, pos)
    pos += NODE.size
    offset = pos + main_size - 12          # file data follows the whole table
    pos -= 1
    out = []

    def walk(count: int, prefix: str):
        nonlocal pos, offset
        for _ in range(count):
            _, _, children = NODE.unpack_from(data, pos)
            pos += NODE.size
            name, kind, pos = _name(data, pos)
            if kind == 2:                  # file
                info = FILE_INFO.unpack_from(data, pos)
                pos += FILE_INFO.size
                size, stored = info[1], info[-1]
                if size != stored:
                    raise ValueError(f"{name} is compressed - not supported")
                out.append((prefix + name, offset, size))
                offset += stored
            elif kind == 3:                # folder
                pos += 25
                walk(children, "" if name == "%DEFAULT FOLDER%" else prefix + name + "/")
            else:
                raise ValueError(f"unknown entry type {kind}")
    walk(root_count, "")
    return out


def extract(data: bytes, dest: Path, skip: tuple[str, ...] = ()) -> list[str]:
    names = []
    for rel, off, size in list_files(data):
        if rel in skip:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data[off:off + size])
        names.append(rel)
    return names


def read_file(data: bytes, name: str) -> bytes:
    for rel, off, size in list_files(data):
        if rel == name:
            return data[off:off + size]
    raise FileNotFoundError(name)


if __name__ == "__main__":
    import sys
    blob = Path(sys.argv[1]).read_bytes()
    for rel, off, size in list_files(blob):
        print(f"{size:>12,}  {rel}")
    if len(sys.argv) > 2:
        extract(blob, Path(sys.argv[2]))
        print("extracted to", sys.argv[2])
