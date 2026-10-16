"""ShortsMaker: desktop app that turns an English script into a YouTube Shorts video."""
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "ShortsMaker"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "config.json"
OUT_DIR = Path.home() / "Videos" / "ShortsMaker"

# A windowed .exe has no console: send any output to a log file
if sys.stdout is None or sys.stderr is None:
    _log = open(APP_DIR / "log.txt", "a", encoding="utf-8")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

# Build check: ShortsMaker.exe --selftest <output.mp4>
if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
    import faulthandler

    _tlog = open(APP_DIR / "selftest_log.txt", "w", encoding="utf-8")
    sys.stdout = sys.stderr = _tlog
    faulthandler.dump_traceback_later(150, exit=True, file=_tlog)
    try:
        import engine

        ok = engine.selftest(sys.argv[2])
    except Exception:
        traceback.print_exc()
        ok = False
    _tlog.flush()
    sys.exit(0 if ok else 1)

import tkinter as tk
from tkinter import messagebox, ttk

import engine

EXAMPLE = """[octopus underwater] Did you know that octopuses have three hearts?

Two of them pump blood to the gills, while the third keeps it moving through the rest of the body.

And here's the strangest part: their blood is blue, because it uses copper instead of iron.

[ocean waves] Follow for more weird facts about the ocean."""


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.last_file = None
        root.title("ShortsMaker")
        root.geometry("760x700")
        root.minsize(600, 560)

        pad = {"padx": 14, "pady": 6}
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Ssenari (ingiliscə). Hər abzas bir səhnədir, aralarında boş sətir buraxın.",
                  wraplength=700).pack(anchor="w", **pad)
        ttk.Label(main, text="İstəsəniz abzasın əvvəlinə [açar söz] yazaraq videonu özünüz seçin.",
                  foreground="#666").pack(anchor="w", padx=14)

        box = ttk.Frame(main)
        box.pack(fill="both", expand=True, **pad)
        self.text = tk.Text(box, wrap="word", font=("Segoe UI", 11), undo=True)
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.insert("1.0", self.cfg.get("last_script") or EXAMPLE)

        row = ttk.Frame(main)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Pixabay açarı:").grid(row=0, column=0, sticky="w")
        self.key = ttk.Entry(row, show="•")
        self.key.grid(row=0, column=1, sticky="ew", padx=8)
        self.key.insert(0, self.cfg.get("pixabay_key", ""))
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text="Səs:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.voice = ttk.Combobox(row, values=list(engine.VOICES), state="readonly")
        self.voice.set(self.cfg.get("voice", list(engine.VOICES)[0]))
        self.voice.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(row, text="Sürət:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.rate = ttk.Combobox(row, values=["-10%", "+0%", "+5%", "+10%", "+20%"], state="readonly", width=8)
        self.rate.set(self.cfg.get("rate", "+5%"))
        self.rate.grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))

        self.status = ttk.Label(main, text="Hazır.")
        self.status.pack(anchor="w", **pad)
        self.bar = ttk.Progressbar(main, maximum=1.0)
        self.bar.pack(fill="x", padx=14)

        btns = ttk.Frame(main)
        btns.pack(fill="x", padx=14, pady=12)
        self.go = ttk.Button(btns, text="▶  Video yarat", command=self.start)
        self.go.pack(side="left")
        ttk.Button(btns, text="📁  Qovluğu aç", command=self.open_folder).pack(side="left", padx=8)

    def set_progress(self, msg, frac):
        self.root.after(0, lambda: (self.status.config(text=msg), self.bar.config(value=frac)))

    def start(self):
        script = self.text.get("1.0", "end").strip()
        key = self.key.get().strip()
        if not key:
            messagebox.showwarning("ShortsMaker", "Pixabay açarını daxil edin.")
            return
        self.cfg.update(pixabay_key=key, voice=self.voice.get(), rate=self.rate.get(), last_script=script)
        save_config(self.cfg)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"short_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        self.go.config(state="disabled")
        voice = engine.VOICES[self.voice.get()]
        threading.Thread(target=self.run, args=(script, key, voice, self.rate.get(), out), daemon=True).start()

    def run(self, script, key, voice, rate, out):
        try:
            dur = engine.generate(script, key, voice, rate, out, self.set_progress)
            self.last_file = out
            self.root.after(0, lambda: messagebox.showinfo(
                "ShortsMaker", f"Video hazırdır ({dur:.0f} san.):\n{out}"))
        except Exception as e:
            traceback.print_exc()
            msg = str(e) or e.__class__.__name__
            self.root.after(0, lambda: messagebox.showerror("Xəta", msg))
            self.set_progress("Xəta baş verdi.", 0)
        finally:
            self.root.after(0, lambda: self.go.config(state="normal"))

    def open_folder(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(OUT_DIR) if hasattr(os, "startfile") else None


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
