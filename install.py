"""Build a modded screen updater for the AULA L99 from AULA's own download.

    python install.py [AULA's "L99 reset firmware ....zip", the screen .exe inside it,
                       an unpacked updater folder, or its UartTFT-II_Flash.bin]

Without an argument it looks in this folder, the folder above, Downloads,
Downloads\\Compressed and Desktop.
Only the exact screen file this mod was made for is accepted (checked by SHA-1),
so a different AULA version can't be flashed by mistake.
Output: an "L99-mod-updater" folder next to this script = AULA's screen updater
with the modded UartTFT-II_Flash.bin. Nothing of AULA's is stored in this repo.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
import evb  # noqa: E402

UI_NAME = "UartTFT-II_Flash.bin"
UI_SIZE = 127_599_044
UI_SHA1 = "ff0fbf34f50be098339af341d35addd9039d84aa"     # AULA L99 screen UI V2.20 ("L99 reset firmware 2026.5.25")
OUT_DIR = HERE / "L99-mod-updater"
TOOL = "HFD_ISP_TOOL_V1.31.exe"


def is_screen_exe(name: str) -> bool:
    n = name.lower()
    return n.endswith(".exe") and "screen" in n and "l99" in n


def search(arg: str | None):
    if arg:
        yield Path(arg.strip('"'))
        return
    home = Path.home()
    for base in (HERE, HERE.parent, home / "Downloads", home / "Downloads" / "Compressed", home / "Desktop"):
        if not base.exists():
            continue
        for pattern in ("*L99*reset*firmware*.zip", "*L99*Screen*reset*.exe", f"*/*L99*Screen*reset*.exe",
                        UI_NAME, f"*/{UI_NAME}"):
            for p in base.glob(pattern):
                if OUT_DIR not in p.resolve().parents:
                    yield p


def load(path: Path) -> dict[str, bytes] | None:
    """All files of AULA's screen updater found at path, {relative name: bytes}."""
    if path.is_dir():
        if not (path / UI_NAME).exists():
            return None
        return {str(p.relative_to(path)).replace("\\", "/"): p.read_bytes()
                for p in path.rglob("*") if p.is_file()}
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            member = next((i for i in z.infolist() if is_screen_exe(i.filename)), None)
            if member is None:
                return None
            print(f"  unpacking {Path(member.filename).name[:60]}... from the zip")
            path_data = z.read(member)
        return {rel: path_data[off:off + size] for rel, off, size in evb.list_files(path_data)}
    if path.suffix.lower() == ".exe":
        data = path.read_bytes()
        return {rel: data[off:off + size] for rel, off, size in evb.list_files(data)}
    if path.name == UI_NAME:
        files = {UI_NAME: path.read_bytes()}
        if (path.parent / TOOL).exists():               # unpacked updater folder
            return load(path.parent)
        return files
    return None


def find() -> dict[str, bytes]:
    for p in search(sys.argv[1] if len(sys.argv) > 1 else None):
        if not p.exists():
            print(f"  not found: {p}")
            continue
        print(f"  checking {p} ...")
        try:
            files = load(p)
        except Exception as exc:
            print(f"  -> can't read it ({exc})")
            continue
        ui = (files or {}).get(UI_NAME)
        if ui is None:
            print("  -> no screen file inside")
        elif len(ui) != UI_SIZE or hashlib.sha1(ui).hexdigest() != UI_SHA1:
            print("  -> not the screen file this mod was made for (another AULA version, or already modded)")
        else:
            return files
    sys.exit(f"\nAULA's screen updater was not found.\n"
             "Download \"L99 reset firmware 2026.5.25.zip\" from aulastar.com (see README.md) and drag it onto install.bat.")


def main() -> None:
    print("AULA L99 screen mod - builder\n")
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Missing Pillow: run  pip install pillow")
    if OUT_DIR.exists():
        sys.exit(f"{OUT_DIR} already exists - delete or rename it first.")
    files = find()
    print("\nAULA's screen file found and verified.")

    import build_l99
    with tempfile.TemporaryDirectory() as tmp:
        orig = Path(tmp) / UI_NAME
        orig.write_bytes(files[UI_NAME])
        OUT_DIR.mkdir()
        for rel, data in files.items():
            if rel != UI_NAME:
                (OUT_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
                (OUT_DIR / rel).write_bytes(data)
        print("Building the modded screen file ...")
        build_l99.build(orig, OUT_DIR / UI_NAME, first="en", bilingual=False, patch=False)
    has_tool = (OUT_DIR / TOOL).exists()
    print(f"\nDone: {OUT_DIR}")
    if has_tool:
        print(f"Next: put the keyboard in screen download mode and run {TOOL} from that folder (see README.md).")
    else:
        print(f"Only the screen file was found; copy it into AULA's updater folder as {UI_NAME}.")


if __name__ == "__main__":
    main()
