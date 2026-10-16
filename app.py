"""ShortsMaker: desktop app that turns an English script into a YouTube video (Shorts or long)."""
import json
import os
import sys
import threading
import traceback
import webbrowser
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

# Build check: ShortsMaker.exe --selftest <output_dir>
if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
    import faulthandler

    _tlog = open(APP_DIR / "selftest_log.txt", "w", encoding="utf-8")
    sys.stdout = sys.stderr = _tlog
    faulthandler.dump_traceback_later(240, exit=True, file=_tlog)
    try:
        import engine

        ok = engine.selftest(sys.argv[2])
    except Exception:
        traceback.print_exc()
        ok = False
    _tlog.flush()
    sys.exit(0 if ok else 1)

from tkinter import messagebox

import customtkinter as ctk

import engine

# ---------- Theme ----------
ctk.set_appearance_mode("dark")
ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6947F5"
BG = "#121216"
PANEL = "#1B1B22"
CARD = "#24242E"
BORDER = "#33333F"
MUTED = "#9A9AB0"
GREEN, ORANGE, RED = "#3DDC97", "#FFB547", "#FF5C70"
UI_FONT = "Segoe UI"

EXAMPLE = """[octopus underwater] Did you know that octopuses have three hearts?

Two of them pump blood to the gills, while the third keeps it moving through the rest of the body.

And here's the strangest part: their blood is blue, because it uses copper instead of iron.

[ocean waves] Follow for more weird facts about the ocean."""


def F(size, bold=False):
    return ctk.CTkFont(family=UI_FONT, size=size, weight="bold" if bold else "normal")


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Settings dialog ----------
class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Ayarlar")
        self.geometry("520x520")
        self.resizable(False, False)
        self.configure(fg_color=PANEL)
        self.transient(app)
        self.after(100, self.grab_set)

        ctk.CTkLabel(self, text="⚙️  Ayarlar", font=F(22, True)).pack(anchor="w", padx=28, pady=(26, 18))

        ctk.CTkLabel(self, text="Pixabay açarı", font=F(13, True), text_color=MUTED).pack(anchor="w", padx=28)
        self.key = ctk.CTkEntry(self, show="•", height=42, corner_radius=10, font=F(14),
                                fg_color=CARD, border_color=BORDER)
        self.key.pack(fill="x", padx=28, pady=(6, 4))
        self.key.insert(0, app.cfg.get("pixabay_key", ""))
        link = ctk.CTkLabel(self, text="Açarı haradan alım?  pixabay.com/api/docs ↗", font=F(12),
                            text_color=ACCENT, cursor="hand2")
        link.pack(anchor="w", padx=28)
        link.bind("<Button-1>", lambda e: webbrowser.open("https://pixabay.com/api/docs/"))

        ctk.CTkLabel(self, text="Səsləndirmə dili", font=F(13, True), text_color=MUTED).pack(anchor="w", padx=28, pady=(18, 0))
        self.lang = ctk.CTkSegmentedButton(self, values=list(engine.LANGUAGES), height=36, font=F(13),
                                           selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                           fg_color=CARD, unselected_color=CARD, command=self.on_lang)
        self.lang.set(app.lang())
        self.lang.pack(fill="x", padx=28, pady=(6, 0))

        ctk.CTkLabel(self, text="Səs", font=F(13, True), text_color=MUTED).pack(anchor="w", padx=28, pady=(18, 0))
        self.voice = ctk.CTkOptionMenu(self, values=["-"], height=40, corner_radius=10,
                                       font=F(14), fg_color=CARD, button_color=CARD,
                                       button_hover_color=BORDER, dropdown_font=F(13))
        self.voice.pack(fill="x", padx=28, pady=(6, 0))
        self.on_lang(app.lang(), app.cfg.get("voice"))

        ctk.CTkLabel(self, text="Danışıq sürəti", font=F(13, True), text_color=MUTED).pack(anchor="w", padx=28, pady=(18, 0))
        self.rate = ctk.CTkSegmentedButton(self, values=engine.RATES, height=36, font=F(13),
                                           selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                           fg_color=CARD, unselected_color=CARD)
        self.rate.set(app.cfg.get("rate", "+5%"))
        self.rate.pack(fill="x", padx=28, pady=(6, 0))

        ctk.CTkButton(self, text="Yadda saxla", height=44, corner_radius=12, font=F(15, True),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.save).pack(
            fill="x", padx=28, pady=(26, 0))

    def on_lang(self, lang, keep_voice=None):
        names = list(engine.LANGUAGES[lang]["voices"])
        self.voice.configure(values=names)
        self.voice.set(keep_voice if keep_voice in names else names[0])

    def save(self):
        self.app.cfg.update(pixabay_key=self.key.get().strip(), lang=self.lang.get(),
                            voice=self.voice.get(), rate=self.rate.get())
        save_config(self.app.cfg)
        self.app.update_stats()
        self.destroy()


# ---------- Main window ----------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.fmt = self.cfg.get("format", "shorts")
        self.busy = False
        self._stats_job = None

        self.title("ShortsMaker")
        scale = self._get_window_scaling()
        sw, sh = int(self.winfo_screenwidth() / scale), int(self.winfo_screenheight() / scale)
        w, h = min(1100, sw - 40), min(740, sh - 90)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(820, w), min(560, h))
        self.configure(fg_color=BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main()
        self.select_format(self.fmt)
        self.update_stats()
        if not self.cfg.get("pixabay_key"):
            self.after(400, self.open_settings)

    def lang(self):
        lang = self.cfg.get("lang", "English")
        return lang if lang in engine.LANGUAGES else "English"

    def voice_id(self):
        voices = engine.LANGUAGES[self.lang()]["voices"]
        return voices.get(self.cfg.get("voice"), list(voices.values())[0])

    # Sidebar: brand, format cards, settings
    def _build_sidebar(self):
        side = ctk.CTkFrame(self, width=270, corner_radius=0, fg_color=PANEL)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)

        ctk.CTkLabel(side, text="▶ ShortsMaker", font=F(24, True)).pack(anchor="w", padx=24, pady=(28, 0))
        ctk.CTkLabel(side, text="Ssenaridən hazır videoya", font=F(13), text_color=MUTED).pack(anchor="w", padx=24)

        ctk.CTkLabel(side, text="FORMAT", font=F(12, True), text_color=MUTED).pack(anchor="w", padx=24, pady=(34, 8))
        self.cards = {}
        for key, icon, title, sub in [
            ("shorts", "📱", "Shorts", "9:16 · maks. 59 saniyə"),
            ("long", "🖥", "Uzun video", "16:9 · limitsiz"),
        ]:
            card = ctk.CTkFrame(side, corner_radius=14, border_width=2, cursor="hand2")
            card.pack(fill="x", padx=18, pady=5)
            t = ctk.CTkLabel(card, text=f"{icon}  {title}", font=F(16, True), anchor="w", cursor="hand2")
            t.pack(fill="x", padx=16, pady=(12, 0))
            s = ctk.CTkLabel(card, text=sub, font=F(12), anchor="w", cursor="hand2")
            s.pack(fill="x", padx=16, pady=(0, 12))
            for w in (card, t, s):
                w.bind("<Button-1>", lambda e, k=key: self.select_format(k))
                w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.cards[key] = (card, t, s)

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=18, pady=22)
        for text, cmd in [("⚙️   Ayarlar", self.open_settings), ("📁   Qovluğu aç", self.open_folder)]:
            ctk.CTkButton(bottom, text=text, anchor="w", height=42, corner_radius=12, font=F(14),
                          fg_color="transparent", hover_color=CARD, text_color="#E6E6F0",
                          command=cmd).pack(fill="x", pady=3)

    # Main: script editor, stats, progress, action
    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(head, text="Ssenari", font=F(22, True)).pack(anchor="w")
        ctk.CTkLabel(head, text="Ayarlarda seçdiyiniz dildə yazın. Hər abzas bir səhnədir. Videonu özünüz seçmək "
                                "üçün abzasın əvvəlinə ingiliscə [açar söz] yazın.",
                     font=F(13), text_color=MUTED, wraplength=700, justify="left").pack(anchor="w", pady=(2, 12))

        self.text = ctk.CTkTextbox(main, corner_radius=16, font=F(15), fg_color=PANEL,
                                   border_color=BORDER, border_width=1, wrap="word")
        self.text.grid(row=1, column=0, sticky="nsew")
        self.text.insert("1.0", self.cfg.get("last_script") or EXAMPLE)
        self.text.bind("<KeyRelease>", lambda e: self.schedule_stats())

        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", pady=(10, 16))
        self.stats_lbl = ctk.CTkLabel(stats, text="", font=F(13), text_color=MUTED)
        self.stats_lbl.pack(side="left")
        self.badge = ctk.CTkLabel(stats, text="", font=F(13, True), corner_radius=10, height=28,
                                  fg_color=CARD, padx=12)
        self.badge.pack(side="right")

        # Progress card
        card = ctk.CTkFrame(main, corner_radius=16, fg_color=PANEL, border_color=BORDER, border_width=1)
        card.grid(row=3, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        self.status = ctk.CTkLabel(top, text="Hazırdır. Ssenarini yazıb video yaradın.", font=F(14),
                                   text_color="#E6E6F0")
        self.status.pack(side="left")
        self.pct = ctk.CTkLabel(top, text="0%", font=F(18, True), text_color=ACCENT)
        self.pct.pack(side="right")

        self.bar = ctk.CTkProgressBar(card, height=12, corner_radius=6, progress_color=ACCENT, fg_color=CARD)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

        self.go = ctk.CTkButton(card, text="✨  Video yarat", height=52, width=220, corner_radius=14,
                                font=F(17, True), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                command=self.start)
        self.go.grid(row=0, column=1, rowspan=2, padx=(0, 20), pady=16)

    # ---------- Behaviour ----------
    def select_format(self, key):
        if self.busy:
            return
        self.fmt = key
        for k, (card, t, s) in self.cards.items():
            on = k == key
            card.configure(fg_color=ACCENT if on else CARD, border_color=ACCENT if on else BORDER)
            s.configure(text_color="#ECE8FF" if on else MUTED)
        self.update_stats()

    def _hover(self, key, inside):
        if key != self.fmt:
            self.cards[key][0].configure(fg_color=BORDER if inside else CARD)

    def schedule_stats(self):
        if self._stats_job:
            self.after_cancel(self._stats_job)
        self._stats_job = self.after(250, self.update_stats)

    def update_stats(self):
        raw = self.text.get("1.0", "end")
        words = engine.word_count(raw)
        scenes = len(engine.parse_scenes(raw))
        est = engine.estimate_seconds(raw, self.cfg.get("rate", "+5%"), self.lang())
        self.stats_lbl.configure(text=f"{words} söz  ·  {scenes} səhnə  ·  {self.lang()}")
        if self.fmt == "shorts":
            limit = engine.SHORTS_MAX_SEC
            color = GREEN if est <= limit * 0.9 else ORANGE if est <= limit else RED
            self.badge.configure(text=f"⏱  ~{est:.0f} / {limit:.0f} san", text_color=color)
        else:
            m, s = divmod(int(est), 60)
            self.badge.configure(text=f"⏱  ~{m}:{s:02d} dəq", text_color=GREEN)

    def set_progress(self, msg, frac):
        def apply():
            self.status.configure(text=msg)
            self.bar.set(frac)
            self.pct.configure(text=f"{int(frac * 100)}%")
        self.after(0, apply)

    def open_settings(self):
        SettingsDialog(self)

    def open_folder(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(OUT_DIR)

    def start(self):
        if self.busy:
            return
        script = self.text.get("1.0", "end").strip()
        key = self.cfg.get("pixabay_key", "")
        if not key:
            self.open_settings()
            return
        if not engine.parse_scenes(script):
            messagebox.showwarning("ShortsMaker", "Ssenari boşdur.")
            return
        if self.fmt == "shorts":
            est = engine.estimate_seconds(script, self.cfg.get("rate", "+5%"), self.lang())
            if est > engine.SHORTS_MAX_SEC + 5:
                messagebox.showwarning(
                    "Ssenari uzundur",
                    f"Ssenari təxminən {est:.0f} saniyədir. Shorts üçün maksimum "
                    f"{engine.SHORTS_MAX_SEC:.0f} saniyədir.\n\nSsenarini qısaldın və ya Uzun video seçin.")
                return

        self.cfg.update(last_script=script, format=self.fmt)
        save_config(self.cfg)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        prefix = "short" if self.fmt == "shorts" else "video"
        out = OUT_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.mp4"

        self.busy = True
        self.go.configure(state="disabled", text="⏳  Yaradılır...")
        self.set_progress("Başlayır...", 0)
        args = (script, key, self.voice_id(), self.cfg.get("rate", "+5%"), out, self.fmt, self.lang())
        threading.Thread(target=self.run, args=args, daemon=True).start()

    def run(self, script, key, voice, rate, out, fmt, lang):
        try:
            dur = engine.generate(script, key, voice, rate, out, fmt, self.set_progress, lang)
            self.set_progress(f"Hazırdır! {dur:.0f} saniyəlik video: {out.name}", 1.0)
            self.after(0, lambda: messagebox.showinfo("Video hazırdır", f"{dur:.0f} saniyəlik video yaradıldı:\n{out}"))
        except engine.ScriptTooLong as e:
            msg = str(e)
            self.set_progress("Ssenari Shorts üçün uzundur.", 0)
            self.after(0, lambda: messagebox.showwarning("Ssenari uzundur", msg))
        except Exception as e:
            traceback.print_exc()
            msg = str(e) or e.__class__.__name__
            self.set_progress("Xəta baş verdi.", 0)
            self.after(0, lambda: messagebox.showerror("Xəta", msg))
        finally:
            def done():
                self.busy = False
                self.go.configure(state="normal", text="✨  Video yarat")
            self.after(0, done)


if __name__ == "__main__":
    App().mainloop()
