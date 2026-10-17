"""L99 Deck (by Rindobolical) editor - pick what each Deck tile does, change icons, send to the keyboard.

Run:  pythonw l99_deck_gui.py      (or double-click "L99 Deck Editor.bat")
Needs: pip install pillow pyserial icoextract pycaw comtypes
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import l99_deck as deck  # noqa: E402

ICON_DIR = HERE / "icons"
TYPES = ["Empty", "Program", "Website", "Shortcut keys", "Media / Microphone"]
MEDIA = {
    "Play / Pause": "media_play_pause", "Next track": "media_next", "Previous track": "media_prev",
    "Mute / unmute microphone": "mic_toggle", "Mute sound": "volume_mute",
    "Volume up": "volume_up", "Volume down": "volume_down",
}
KEY_PRESETS = [f"f{n}" for n in range(13, 25)] + [f"ctrl+alt+f{n}" for n in range(13, 25)] + [
    "win+shift+s", "win+d", "ctrl+shift+esc", "win+e", "win+l", "alt+tab", "ctrl+shift+m"]
GLYPHS = ["(none)", "mic", "play", "music", "shot", "desk", "task", "plus"]
SCALE = 1.25
START_MENUS = [Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
               Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
               Path.home() / "Desktop", Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"]


def tile_type(t: dict) -> str:
    if "run" in t:
        return "Program"
    if "url" in t:
        return "Website"
    if "keys" in t:
        return "Shortcut keys"
    if "action" in t:
        return "Media / Microphone"
    return "Empty"


def resolve_lnk(path: str, field: str = "TargetPath") -> str:
    """TargetPath / IconLocation of a Windows .lnk shortcut (via PowerShell, no extra packages)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(New-Object -ComObject WScript.Shell).CreateShortcut('{path}').{field}"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        return out.stdout.strip()
    except Exception:
        return ""


def lnk_icon(path: str) -> str:
    """Icon file a shortcut uses (e.g. Google Play Games shortcuts point at a cached .ico)."""
    loc = resolve_lnk(path, "IconLocation")
    loc = os.path.expandvars(loc.rsplit(",", 1)[0]) if loc else ""
    return loc if loc and os.path.exists(loc) else ""


class DeckEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("L99 Deck — by Rindobolical")
        root.resizable(False, False)
        self.cfg = deck.load_config()
        self.cfg["tiles"] = (self.cfg.get("tiles", []) + [{}] * 12)[:12]
        self.sel = 0
        self.dirty = False
        self._loading = False

        main = ttk.Frame(root, padding=10)
        main.grid()
        # --- left: virtual screen
        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="n", padx=(0, 12))
        ttk.Label(left, text="Keyboard screen (click a tile)").grid(sticky="w")
        self.canvas = tk.Canvas(left, width=int(deck.W * SCALE), height=int(deck.H * SCALE),
                                highlightthickness=1, highlightbackground="#444", bg="black")
        self.canvas.grid()
        self.canvas.bind("<Button-1>", self.on_click)

        # --- right: tile editor
        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="n")
        head = ttk.Frame(right)
        head.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.title = ttk.Label(head, text="", font=("Segoe UI", 12, "bold"))
        self.title.grid(row=0, column=0, sticky="w")
        self.where = ttk.Label(head, text="", foreground="#777")
        self.where.grid(row=1, column=0, sticky="w")

        ttk.Label(right, text="Label").grid(row=1, column=0, sticky="w")
        self.v_label = tk.StringVar()
        ttk.Entry(right, textvariable=self.v_label, width=34).grid(row=1, column=1, columnspan=2, sticky="we")

        ttk.Label(right, text="Does").grid(row=2, column=0, sticky="w", pady=4)
        self.v_type = tk.StringVar()
        cb = ttk.Combobox(right, textvariable=self.v_type, values=TYPES, state="readonly", width=31)
        cb.grid(row=2, column=1, columnspan=2, sticky="we", pady=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_change())

        # type-specific frames
        self.f_prog = ttk.Frame(right)
        self.v_run = tk.StringVar()
        self.v_args = tk.StringVar()
        ttk.Label(self.f_prog, text="Program").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.f_prog, textvariable=self.v_run, width=34).grid(row=0, column=1, columnspan=2, sticky="we")
        ttk.Button(self.f_prog, text="Pick from Start menu…", command=self.pick_start_menu).grid(row=1, column=1, sticky="we", pady=2)
        ttk.Button(self.f_prog, text="Browse…", command=self.browse_program).grid(row=1, column=2, sticky="we", pady=2)
        ttk.Label(self.f_prog, text="Arguments").grid(row=2, column=0, sticky="w")
        ttk.Entry(self.f_prog, textvariable=self.v_args, width=34).grid(row=2, column=1, columnspan=2, sticky="we")

        self.f_url = ttk.Frame(right)
        self.v_url = tk.StringVar()
        ttk.Label(self.f_url, text="Web address").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.f_url, textvariable=self.v_url, width=34).grid(row=0, column=1, sticky="we")

        self.f_keys = ttk.Frame(right)
        self.v_keys = tk.StringVar()
        ttk.Label(self.f_keys, text="Keys").grid(row=0, column=0, sticky="w")
        ttk.Combobox(self.f_keys, textvariable=self.v_keys, values=KEY_PRESETS, width=31).grid(row=0, column=1, sticky="we")
        ttk.Label(self.f_keys, text="e.g. f13, ctrl+alt+f14, win+shift+s\nF13–F24 are free keys you can bind in Discord, OBS, games…",
                  foreground="#666").grid(row=1, column=1, sticky="w")

        self.f_media = ttk.Frame(right)
        self.v_media = tk.StringVar()
        ttk.Label(self.f_media, text="Action").grid(row=0, column=0, sticky="w")
        ttk.Combobox(self.f_media, textvariable=self.v_media, values=list(MEDIA), state="readonly",
                     width=31).grid(row=0, column=1, sticky="we")

        for f in (self.f_prog, self.f_url, self.f_keys, self.f_media):
            f.grid(row=3, column=0, columnspan=3, sticky="we", pady=4)
            f.grid_remove()

        # icon
        ttk.Separator(right).grid(row=4, column=0, columnspan=3, sticky="we", pady=8)
        ttk.Label(right, text="Icon").grid(row=5, column=0, sticky="nw")
        icon_box = ttk.Frame(right)
        icon_box.grid(row=5, column=1, columnspan=2, sticky="w")
        self.icon_preview = ttk.Label(icon_box)
        self.icon_preview.grid(row=0, column=0, rowspan=3, padx=(0, 8))
        ttk.Button(icon_box, text="Choose image…", command=self.choose_icon).grid(row=0, column=1, sticky="we")
        ttk.Button(icon_box, text="Use program's icon", command=self.auto_icon).grid(row=1, column=1, sticky="we", pady=2)
        self.v_glyph = tk.StringVar()
        g = ttk.Combobox(icon_box, textvariable=self.v_glyph, values=GLYPHS, state="readonly", width=16)
        g.grid(row=2, column=1, sticky="we")
        g.bind("<<ComboboxSelected>>", lambda e: self.on_change())

        # tile buttons
        row = ttk.Frame(right)
        row.grid(row=6, column=0, columnspan=3, sticky="we", pady=(10, 0))
        ttk.Button(row, text="Test this tile", command=self.test_tile).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(row, text="Clear tile", command=self.clear_tile).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(row, text="◀ Move", command=lambda: self.move(-1)).grid(row=0, column=2)
        ttk.Button(row, text="Move ▶", command=lambda: self.move(1)).grid(row=0, column=3)

        # bottom: save / send / start
        ttk.Separator(right).grid(row=7, column=0, columnspan=3, sticky="we", pady=10)
        bottom = ttk.Frame(right)
        bottom.grid(row=8, column=0, columnspan=3, sticky="we")
        self.btn_send = ttk.Button(bottom, text="Save & send to keyboard", command=self.send)
        self.btn_send.grid(row=0, column=0, sticky="we")
        ttk.Button(bottom, text="Save only", command=self.save).grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(right, length=300, maximum=100)
        self.progress.grid(row=9, column=0, columnspan=3, sticky="we", pady=6)
        self.status = ttk.Label(right, text="", foreground="#555", wraplength=330, justify="left")
        self.status.grid(row=10, column=0, columnspan=3, sticky="w")

        ttk.Separator(right).grid(row=11, column=0, columnspan=3, sticky="we", pady=10)
        self.v_startup = tk.BooleanVar(value=self.startup_file().exists())
        ttk.Checkbutton(right, text="Start L99 Deck with Windows", variable=self.v_startup,
                        command=self.toggle_startup).grid(row=12, column=0, columnspan=2, sticky="w")
        ttk.Button(right, text="Start L99 Deck now", command=self.start_runner).grid(row=12, column=2, sticky="e")
        ttk.Label(main, text="L99 Deck · made by Rindobolical", foreground="#999").grid(row=1, column=0, sticky="w", pady=(8, 0))

        # Live info page + language of the Deck picture
        ttk.Separator(right).grid(row=13, column=0, columnspan=3, sticky="we", pady=10)
        ttk.Label(right, text="Live page city").grid(row=14, column=0, sticky="w")
        self.v_city = tk.StringVar(value=self.cfg.get("live", {}).get("city", "Istanbul"))
        ttk.Entry(right, textvariable=self.v_city, width=20).grid(row=14, column=1, sticky="w")
        ttk.Label(right, text="Deck language").grid(row=15, column=0, sticky="w", pady=4)
        self.v_lang = tk.StringVar(value="Türkçe" if self.cfg.get("language") == "tr" else "English")
        lc = ttk.Combobox(right, textvariable=self.v_lang, values=["Türkçe", "English"], state="readonly", width=17)
        lc.grid(row=15, column=1, sticky="w", pady=4)
        lc.bind("<<ComboboxSelected>>", lambda e: self.on_settings())
        self.v_city.trace_add("write", lambda *_: self.on_settings())

        for var in (self.v_label, self.v_run, self.v_args, self.v_url, self.v_keys, self.v_media):
            var.trace_add("write", lambda *_: self.on_change())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.load_tile(0)
        self.redraw()

    # ---------- drawing
    def redraw(self):
        img = deck.render(self.cfg).convert("RGB")
        d = ImageDraw.Draw(img)
        c, r = self.sel % deck.COLS, self.sel // deck.COLS
        x, y = deck.X0 + c * (deck.TW + deck.GX), deck.Y0 + r * (deck.TH + deck.GY)
        d.rounded_rectangle((x - 3, y - 3, x + deck.TW + 3, y + deck.TH + 3), radius=14, outline=(255, 200, 0), width=3)
        img = img.resize((int(deck.W * SCALE), int(deck.H * SCALE)), Image.LANCZOS)
        self._tk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk)
        icon = deck._icon_for(self.cfg["tiles"][self.sel], 48)
        self._tk_icon = ImageTk.PhotoImage(icon)
        self.icon_preview.configure(image=self._tk_icon)

    def on_click(self, ev):
        x, y = ev.x / SCALE, ev.y / SCALE
        for k in range(12):
            c, r = k % deck.COLS, k // deck.COLS
            tx, ty = deck.X0 + c * (deck.TW + deck.GX), deck.Y0 + r * (deck.TH + deck.GY)
            if tx <= x <= tx + deck.TW and ty <= y <= ty + deck.TH:
                self.load_tile(k)
                self.redraw()
                return

    # ---------- tile <-> fields
    def load_tile(self, k: int):
        self._loading = True
        self.sel = k
        t = self.cfg["tiles"][k]
        self.update_title()
        self.v_label.set(t.get("label", ""))
        self.v_type.set(tile_type(t))
        self.v_run.set(t.get("run", ""))
        self.v_args.set(" ".join(t.get("args", [])))
        self.v_url.set(t.get("url", ""))
        self.v_keys.set(t.get("keys", ""))
        inv = {v: k2 for k2, v in MEDIA.items()}
        self.v_media.set(inv.get(t.get("action", ""), ""))
        self.v_glyph.set(t.get("glyph", "(none)"))
        self._loading = False
        self.show_type_frame()

    def update_title(self):
        """Header above the editor: which tile is selected and where it sits on the screen."""
        k = self.sel
        name = self.v_label.get().strip() if hasattr(self, "v_label") else ""
        self.title.configure(text=f"Editing: {name or 'empty tile'}")
        self.where.configure(text=f"tile {k + 1} of 12 — row {k // 3 + 1}, column {k % 3 + 1} on the screen")

    def show_type_frame(self):
        frames = {"Program": self.f_prog, "Website": self.f_url, "Shortcut keys": self.f_keys,
                  "Media / Microphone": self.f_media}
        for f in frames.values():
            f.grid_remove()
        if self.v_type.get() in frames:
            frames[self.v_type.get()].grid()

    def on_change(self):
        self.update_title()
        if self._loading:
            return
        self.show_type_frame()
        old = self.cfg["tiles"][self.sel]
        t = {"label": self.v_label.get()}
        kind = self.v_type.get()
        if kind == "Program" and self.v_run.get():
            t["run"] = self.v_run.get()
            if self.v_args.get().strip():
                t["args"] = self.v_args.get().split()
            for key in ("focus",):
                if key in old and old.get("run") == t["run"]:
                    t[key] = old[key]
        elif kind == "Website" and self.v_url.get():
            t["url"] = self.v_url.get()
        elif kind == "Shortcut keys" and self.v_keys.get():
            t["keys"] = self.v_keys.get().strip().lower()
        elif kind == "Media / Microphone" and self.v_media.get():
            t["action"] = MEDIA[self.v_media.get()]
        if old.get("icon"):
            t["icon"] = old["icon"]
        glyph = self.v_glyph.get()
        if glyph not in ("", "(none)") and not (glyph == "plus" and kind != "Empty"):
            t["glyph"] = glyph
        self.cfg["tiles"][self.sel] = t
        self.dirty = True
        self.redraw()

    # ---------- pickers
    def browse_program(self):
        p = filedialog.askopenfilename(title="Choose a program", filetypes=[("Programs & shortcuts", "*.exe *.lnk *.url"), ("All files", "*.*")])
        if p:
            self.set_program(p)

    def set_program(self, path: str, name: str | None = None):
        path = os.path.normpath(path)
        icon = ""
        run = path
        focus = ""
        if path.lower().endswith(".lnk"):
            target = resolve_lnk(path)
            if target.lower().endswith(".exe") and os.path.exists(target):
                icon, focus = target, os.path.basename(target)
            icon = lnk_icon(path) or icon       # e.g. Google Play Games / Steam game shortcuts
        elif path.lower().endswith(".exe"):
            icon, focus = path, os.path.basename(path)
        elif path.lower().endswith(".url"):
            icon = path
        self._loading = True
        self.v_type.set("Program")
        self.v_run.set(run)
        if not self.v_label.get() or name:
            self.v_label.set((name or Path(path).stem)[:14])
        self._loading = False
        self.on_change()
        t = self.cfg["tiles"][self.sel]
        if icon:
            t["icon"] = icon
        if focus:
            t["focus"] = focus
        t.pop("glyph", None)
        self.v_glyph.set("(none)")
        self.redraw()

    def pick_start_menu(self):
        items = []
        for base in START_MENUS:
            if base.exists():
                for p in base.rglob("*"):
                    if p.suffix.lower() in (".lnk", ".url") and "uninstall" not in p.stem.lower():
                        items.append((p.stem, str(p)))
        items.sort(key=lambda x: x[0].lower())
        win = tk.Toplevel(self.root)
        win.title("Pick a program")
        win.transient(self.root)
        q = tk.StringVar()
        ttk.Entry(win, textvariable=q, width=50).grid(padx=8, pady=8, sticky="we")
        lb = tk.Listbox(win, width=60, height=22)
        lb.grid(padx=8, sticky="nsew")
        shown: list = []

        def fill(*_):
            lb.delete(0, "end")
            shown.clear()
            for name, path in items:
                if q.get().lower() in name.lower():
                    shown.append((name, path))
                    lb.insert("end", name)
        q.trace_add("write", fill)
        fill()

        def choose(*_):
            if lb.curselection():
                name, path = shown[lb.curselection()[0]]
                win.destroy()
                self.set_program(path, name)
        lb.bind("<Double-Button-1>", choose)
        ttk.Button(win, text="Use this", command=choose).grid(pady=8)

    def choose_icon(self):
        p = filedialog.askopenfilename(title="Choose an icon", filetypes=[("Images", "*.png *.ico *.jpg *.jpeg *.bmp *.exe"), ("All files", "*.*")])
        if not p:
            return
        ICON_DIR.mkdir(exist_ok=True)
        if not p.lower().endswith(".exe"):
            dst = ICON_DIR / f"tile{self.sel + 1}_{Path(p).name}"
            shutil.copyfile(p, dst)
            p = str(dst)
        self.cfg["tiles"][self.sel]["icon"] = p
        self.cfg["tiles"][self.sel].pop("glyph", None)
        self.v_glyph.set("(none)")
        self.dirty = True
        self.redraw()

    def auto_icon(self):
        t = self.cfg["tiles"][self.sel]
        t.pop("icon", None)
        t.pop("glyph", None)
        if t.get("run", "").lower().endswith(".lnk"):
            target = resolve_lnk(t["run"])
            if target.lower().endswith(".exe"):
                t["icon"] = target
        self.v_glyph.set("(none)")
        self.dirty = True
        self.redraw()

    # ---------- tile actions
    def test_tile(self):
        threading.Thread(target=deck.run_tile, args=(dict(self.cfg["tiles"][self.sel]),), daemon=True).start()

    def clear_tile(self):
        self.cfg["tiles"][self.sel] = {}
        self.dirty = True
        self.load_tile(self.sel)
        self.redraw()

    def move(self, step: int):
        k, j = self.sel, (self.sel + step) % 12
        tiles = self.cfg["tiles"]
        tiles[k], tiles[j] = tiles[j], tiles[k]
        self.dirty = True
        self.load_tile(j)
        self.redraw()

    def on_settings(self):
        self.cfg.setdefault("live", {})["city"] = self.v_city.get().strip() or "Istanbul"
        self.cfg["language"] = "en" if self.v_lang.get() == "English" else "tr"
        self.dirty = True
        self.redraw()

    # ---------- save / send
    def save(self) -> bool:
        try:
            deck.CONFIG.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            self.dirty = False
            self.status.configure(text="Saved. The running Deck picks up the new actions automatically.")
            return True
        except Exception as exc:
            messagebox.showerror("L99 Deck", f"Could not save deck.json:\n{exc}")
            return False

    def send(self):
        if not self.save():
            return
        self.btn_send.state(["disabled"])
        self.progress["value"] = 0
        self.status.configure(text="Sending to the keyboard… (its screen freezes until this finishes)")

        def work():
            try:
                deck.upload_picture(self.cfg, int(str(self.cfg.get("deck_addr", deck.DECK_ADDR)), 0),
                                    lambda p: self.root.after(0, self.progress.configure, {"value": p}))
                msg = "Done! Open another app on the keyboard screen, then Deck to see the new tiles."
            except Exception as exc:
                msg = f"Not sent: {exc}"
            self.root.after(0, lambda: (self.status.configure(text=msg), self.btn_send.state(["!disabled"])))
        threading.Thread(target=work, daemon=True).start()

    # ---------- runner / startup
    @staticmethod
    def startup_file() -> Path:
        return Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\L99 Deck.bat"

    def toggle_startup(self):
        f = self.startup_file()
        if self.v_startup.get():
            f.write_text(f'@echo off\r\nstart "" pythonw "{HERE / "l99_deck.py"}"\r\n', encoding="utf-8")
            self.status.configure(text="L99 Deck will start with Windows.")
        elif f.exists():
            f.unlink()
            self.status.configure(text="L99 Deck will no longer start with Windows.")

    def start_runner(self):
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else sys.executable)
        subprocess.Popen([exe, str(HERE / "l99_deck.py")], cwd=str(HERE), creationflags=0x08000000)
        self.status.configure(text="L99 Deck started in the background (if it was already running, nothing changes).")

    def on_close(self):
        if self.dirty and messagebox.askyesno("L99 Deck", "Save your changes before closing?"):
            self.save()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    DeckEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
