import ctypes
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import math
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from api_client import ApiClient, BridgeClient, MultiBridge
from glass import GlassButton
from runtime import resource_path

# Give the app its own taskbar identity on Windows
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tetomidi.app")
    except Exception:
        pass

BG = "#000000"
TEXT = "#ffffff"
MUTED = "#8a8a8a"
W = 272  # content width (compact window)
PAD = 16  # side padding of the window
DISCORD_URL = "https://discord.gg/peaqm6uqgB"
AUDIO_TYPES = "*.mp3 *.mp4 *.m4a *.wav *.flac *.ogg *.aac *.mkv *.webm"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_CONFIG = {"mode": "server", "server": "http://127.0.0.1:8765", "token": "",
                  "file_path": "/transcribe", "link_path": "/transcribe-url"}

GUIDE_TEXT = (
    "How to use tetomidi\n\n"
    "1. Click the dashed box and pick an audio file, or paste a link.\n\n"
    "2. Click \"Transcribe\" and wait until it says Done. Click \u2715 to stop.\n\n"
    "3. Click the folder icon to open the MIDI, or \"Back\" to start again.\n\n"
    "MIDI files are saved in the midi folder next to the app. "
    "Only use audio you have the right to convert."
)


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


def _log(msg):
    """Append a line to tetomidi.log next to the app, so problems leave a trace."""
    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(_app_dir(), "tetomidi.log"), "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    _log("Unhandled error:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _excepthook


def load_config():
    """Read tetomidi.json next to the app. Create it with defaults on first run."""
    path = os.path.join(_app_dir(), "tetomidi.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(json.load(f))
        return cfg
    except Exception:
        _log("Could not read tetomidi.json, using defaults")
        return dict(DEFAULT_CONFIG)


def make_client(cfg):
    """'bridge' uses one or more endpoints (first one that answers wins); 'server' uses server.py."""
    if cfg.get("mode") == "bridge":
        endpoints = cfg.get("endpoints") or [{
            "server": cfg["server"],
            "file_path": cfg.get("file_path", "/transcribe"),
            "link_path": cfg.get("link_path", "/transcribe-url"),
        }]
        multi = MultiBridge(endpoints, cfg.get("token", ""))
        multi.start_keepalive(minutes=10)
        return multi
    return ApiClient(cfg["server"], cfg.get("token", ""))


def output_dir():
    """'midi' folder next to the app; fall back to Documents if not writable."""
    d = os.path.join(_app_dir(), "midi")
    try:
        os.makedirs(d, exist_ok=True)
        if not os.access(d, os.W_OK):
            raise OSError
    except OSError:
        d = os.path.join(os.path.expanduser("~"), "Documents", "tetomidi", "midi")
        os.makedirs(d, exist_ok=True)
    return d


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    i = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return path


MAX_SECONDS = 40 * 60


def _audio_length(path):
    """Length in seconds, or None if the format cannot be read (then the file is accepted)."""
    try:
        from mutagen import File
        f = File(path)
        return f.info.length if f is not None and f.info is not None else None
    except Exception:
        return None


def _folder_icon(size=26):
    """Draw a simple yellow folder icon (no external asset needed)."""
    from PIL import ImageDraw as _D
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = _D.Draw(img)
    gold = "#E8C15A"
    d.rounded_rectangle((S * 0.08, S * 0.18, S * 0.42, S * 0.36), radius=S * 0.05, fill=gold)
    d.rounded_rectangle((S * 0.08, S * 0.30, S * 0.92, S * 0.82), radius=S * 0.08, fill=gold)
    return img.resize((size, size), Image.LANCZOS)


def _discord_icon(size=26):
    """Simple Discord-style icon used only if assets/discord.png is missing."""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((S * 0.08, S * 0.18, S * 0.92, S * 0.82), radius=S * 0.22, fill="#5865F2")
    d.ellipse((S * 0.30, S * 0.38, S * 0.46, S * 0.58), fill="#ffffff")
    d.ellipse((S * 0.54, S * 0.38, S * 0.70, S * 0.58), fill="#ffffff")
    return img.resize((size, size), Image.LANCZOS)


def _note_icon(size=84):
    """White music note for the upload box, drawn in code."""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    w = int(S * 0.08)
    # stems
    d.rectangle((S * 0.36, S * 0.20, S * 0.36 + w, S * 0.72), fill=white)
    d.rectangle((S * 0.74, S * 0.14, S * 0.74 + w, S * 0.66), fill=white)
    # beam
    d.polygon([(S * 0.36, S * 0.20), (S * 0.82, S * 0.10), (S * 0.82, S * 0.22),
               (S * 0.36, S * 0.32)], fill=white)
    # note heads
    d.ellipse((S * 0.14, S * 0.60, S * 0.46, S * 0.84), fill=white)
    d.ellipse((S * 0.52, S * 0.54, S * 0.84, S * 0.78), fill=white)
    return img.resize((size, size), Image.LANCZOS)


def _blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("tetomidi")
        self.geometry(f"{W + PAD * 2}x440")
        self.resizable(False, False)
        self.configure(fg_color=BG, background=BG)

        ico = resource_path("assets/tetomidi.ico")
        if os.path.exists(ico):
            self.after(250, lambda: self.iconbitmap(ico))

        self.cfg = load_config()
        self.api = make_client(self.cfg)
        self.src = None
        self.job_id = None
        self.t0 = None
        self._hb = 0
        self._submitting = False
        self._poll_busy = False
        self.folder = None
        self.last_mid = None
        self.logs = []
        self._log_box = None
        self._log_win = None
        self._guide = None

        self._build()
        self._check_server()
        self._fit_and_center()

        # Intro splash: hide the main window, show splash, then reveal the app
        self.withdraw()
        self._show_intro()

    # ---------- intro ----------
    def _show_intro(self):
        splash = ctk.CTkToplevel(self)
        splash.overrideredirect(True)
        splash.configure(fg_color=BG)
        w, h = 420, 380
        x = (splash.winfo_screenwidth() - w) // 2
        y = (splash.winfo_screenheight() - h) // 2
        splash.geometry(f"{w}x{h}+{x}+{y}")
        splash.attributes("-topmost", True)
        splash.attributes("-alpha", 0.0)

        c = tk.Canvas(splash, width=w, height=h, bg=BG, highlightthickness=0, bd=0)
        c.pack()
        cx, cy, r = w // 2, 130, 86
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#161616", width=3)
        ring = c.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=110,
                            style="arc", outline=TEXT, width=3)
        logo_path = resource_path("assets/logo.png")
        if os.path.exists(logo_path):
            splash._logo = ImageTk.PhotoImage(Image.open(logo_path).convert("RGBA").resize((120, 120)))
            c.create_image(cx, cy, image=splash._logo)

        title = c.create_text(cx, 262, text="", fill=TEXT, font=("Segoe UI", 28, "bold"))
        sub = c.create_text(cx, 302, text="Transcriber MIDI", fill=BG, font=("Segoe UI", 13))
        c.create_rectangle(cx - 120, 340, cx + 120, 343, fill="#161616", outline="")
        bar = c.create_rectangle(cx - 120, 340, cx - 120, 343, fill=TEXT, outline="")
        status = c.create_text(cx, 362, text="", fill=MUTED, font=("Segoe UI", 10))

        word = "tetomidi"
        total_ms, tick_ms, fade_ms = 3000, 30, 500
        splash._t = 0

        def step():
            t = splash._t
            if t < fade_ms:
                alpha = t / fade_ms
            elif t > total_ms - fade_ms:
                alpha = (total_ms - t) / fade_ms
            else:
                alpha = 1.0
            try:
                splash.attributes("-alpha", max(0.0, min(1.0, alpha)))
                c.itemconfig(ring, start=(t / total_ms) * 720 % 360)
                n = min(len(word), int(len(word) * min(1.0, t / (total_ms * 0.55))) + 1)
                c.itemconfig(title, text=word[:n])
                c.itemconfig(sub, fill=_blend(BG, MUTED, min(1.0, max(0.0, (t - 600) / 700))))
                frac = min(1.0, t / total_ms)
                c.coords(bar, cx - 120, 340, cx - 120 + 240 * frac, 343)
                if t > total_ms * 0.6:
                    c.itemconfig(status, text="Loading")
            except Exception:
                return
            splash._t += tick_ms
            if t >= total_ms:
                self._finish_intro(splash)
            else:
                splash.after(tick_ms, step)

        step()

    def _finish_intro(self, splash):
        try:
            splash.destroy()
        except Exception:
            pass
        self._fit_and_center()
        self.deiconify()
        self.lift()
        self.focus_force()
        # Centre again once the window is really on screen (and again a moment
        # later), so nothing - DPI scaling, the title bar - can push it off-centre.
        self._center_now()
        self.after(60, self._center_now)
        self.after(350, self._center_now)

    def _center_now(self):
        """Centre the window on the screen using its real, on-screen size."""
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            if w <= 1 or h <= 1:
                w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            # the title bar sits above the client area: count it as part of the window
            bar = max(0, self.winfo_rooty() - self.winfo_y())
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - (h + bar)) // 2
            tk.Tk.wm_geometry(self, f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    # ---------- window size / position ----------
    @staticmethod
    def _scale_of(win):
        for getter in (lambda: win._get_window_scaling(),
                       lambda: ctk.ScalingTracker.get_window_scaling(win)):
            try:
                v = float(getter())
                if v > 0:
                    return v
            except Exception:
                continue
        return 1.0

    def _center_window(self, win, w, h, parent=None):
        """Put `win` (size w x h, in unscaled px) in the middle of the screen,
        or in the middle of `parent` when one is given."""
        sc = self._scale_of(win)
        rw, rh = int(w * sc), int(h * sc)
        try:
            if parent is not None:
                parent.update_idletasks()
                x = parent.winfo_x() + (parent.winfo_width() - rw) // 2
                y = parent.winfo_y() + (parent.winfo_height() - rh) // 2
            else:
                x = (win.winfo_screenwidth() - rw) // 2
                y = (win.winfo_screenheight() - rh) // 2
        except Exception:
            x = y = 80
        win.geometry(f"{w}x{h}+{max(0, int(x))}+{max(0, int(y))}")

    def _fit_and_center(self):
        """Size the window to its content (plus room for the progress bar and
        a two-line status) and show it in the middle of the screen."""
        w = W + PAD * 2
        try:
            self.update_idletasks()
            h = int(self.winfo_reqheight() / self._scale_of(self)) + 32
        except Exception:
            h = 440
        self._center_window(self, w, max(h, 300))

    # ---------- background work ----------
    def _bg(self, fn, on_ok, on_err=None):
        """Run a network call in a thread and deliver the result on the UI thread."""
        q = queue.Queue()

        def run():
            try:
                q.put(("ok", fn()))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def check():
            try:
                kind, val = q.get_nowait()
            except queue.Empty:
                self.after(100, check)
                return
            if kind == "ok":
                on_ok(val)
            elif on_err:
                on_err(val)
            else:
                self._ui_log("Error: " + val)

        self.after(100, check)

    def _check_server(self):
        self.server_lbl.configure(text="Server: checking")

        def ok(_):
            self.server_lbl.configure(text="Server: online", text_color=TEXT)

        def err(msg):
            self.server_lbl.configure(text="Server offline", text_color=MUTED)
            self._ui_log("Server check failed: " + msg)

        self._bg(lambda: self.api.health(), ok, err)

    # ---------- layout ----------
    def _build(self):
        wrap = ctk.CTkFrame(self, fg_color=BG)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=8)

        # Footer first, so it always keeps its space at the bottom
        footer = ctk.CTkFrame(wrap, fg_color=BG)
        footer.pack(side="top", pady=(0, 4))
        folder_small = ctk.CTkImage(light_image=_folder_icon(), dark_image=_folder_icon(),
                                    size=(17, 17))
        ctk.CTkButton(footer, text="", image=folder_small, width=30, height=30,
                      corner_radius=15, fg_color="#151515", hover_color="#262626",
                      command=self.open_result).pack(side="left", padx=5)
        ctk.CTkButton(footer, text="?", width=30, height=30, corner_radius=15,
                      fg_color="#151515", hover_color="#262626", text_color=TEXT,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.show_guide).pack(side="left", padx=5)
        discord_path = resource_path("assets/discord.png")
        if os.path.exists(discord_path):
            discord_pil = Image.open(discord_path).convert("RGBA")
        else:
            discord_pil = _discord_icon()
        discord_img = ctk.CTkImage(light_image=discord_pil, dark_image=discord_pil, size=(21, 21))
        ctk.CTkButton(footer, text="", image=discord_img, width=30, height=30,
                      corner_radius=15, fg_color="#151515", hover_color="#262626",
                      command=lambda: webbrowser.open(DISCORD_URL)).pack(side="left", padx=5)
        ctk.CTkButton(footer, text="LOG", width=30, height=30, corner_radius=8,
                      fg_color="#151515", hover_color="#262626", text_color=TEXT,
                      font=ctk.CTkFont(size=8, weight="bold"),
                      command=self.show_log).pack(side="left", padx=5)

        # Header
        logo_path = resource_path("assets/logo.png")
        if os.path.exists(logo_path):
            logo = ctk.CTkImage(Image.open(logo_path), size=(44, 44))
            ctk.CTkLabel(wrap, image=logo, text="").pack(pady=(0, 1))
        ctk.CTkLabel(wrap, text="tetomidi", text_color=TEXT,
                     font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(0, 7))

        # Upload box: one click opens the file dialog
        self.drop = tk.Canvas(wrap, width=W, height=136, bg=BG, highlightthickness=0,
                              bd=0, cursor="hand2")
        self.drop.pack(pady=(0, 2))
        self._drop_photo = ImageTk.PhotoImage(_note_icon(38))
        self._draw_drop(False)
        self.drop.bind("<Button-1>", lambda e: self.pick_file())
        self.drop.bind("<Enter>", lambda e: self._draw_drop(True))
        self.drop.bind("<Leave>", lambda e: self._draw_drop(False))
        self.file_lbl = ctk.CTkLabel(wrap, text="No file selected", text_color=MUTED,
                                     font=ctk.CTkFont(size=10), wraplength=W - 16)
        self.file_lbl.pack(pady=(1, 4))

        # Link box
        self.link_entry = ctk.CTkEntry(
            wrap, width=W, height=32, corner_radius=16,
            fg_color="#151515", border_color="#2a2a2a", text_color=TEXT,
            placeholder_text="Paste a SoundCloud or other link", placeholder_text_color=MUTED)
        self.link_entry.pack()

        self.server_lbl = ctk.CTkLabel(wrap, text="", text_color=MUTED,
                                       font=ctk.CTkFont(size=9), wraplength=W - 16)
        self.server_lbl.pack(pady=(4, 6))

        # Run / cancel / result row
        self.row = ctk.CTkFrame(wrap, fg_color=BG, width=W, height=38)
        self.row.pack()
        self.row.pack_propagate(False)

        self.go_btn = GlassButton(self.row, "Transcribe", W, 38, command=self.start,
                                  boost=1.7, font=ctk.CTkFont(size=13, weight="bold"))
        self.go_btn.pack()
        self.busy_btn = GlassButton(self.row, "Processing", W - 46, 38, interactive=False,
                                    boost=1.7, font=ctk.CTkFont(size=13, weight="bold"))
        self.cancel_btn = GlassButton(self.row, "\u2715", 38, 38, command=self.cancel,
                                      font=ctk.CTkFont(size=13))
        folder_img = ctk.CTkImage(light_image=_folder_icon(), dark_image=_folder_icon(),
                                  size=(19, 19))
        self.folder_btn = ctk.CTkButton(self.row, text="", image=folder_img, width=38, height=38,
                                        corner_radius=19, fg_color="#151515",
                                        hover_color="#262626", command=self.open_result)
        self.back_btn = GlassButton(self.row, "Back", W - 46, 38, command=self.reset,
                                    boost=1.7, font=ctk.CTkFont(size=13, weight="bold"))

        self.status = ctk.CTkLabel(wrap, text="", text_color=MUTED,
                                   font=ctk.CTkFont(size=10), wraplength=W - 16)
        self.status.pack(pady=(8, 0))
        self.hint = ctk.CTkLabel(wrap, text="", text_color=MUTED,
                                 font=ctk.CTkFont(size=8), wraplength=W - 16)
        self.hint.pack(pady=(1, 0))

        self.bar = ctk.CTkProgressBar(wrap, mode="determinate", height=3,
                                      fg_color="#151515", progress_color=TEXT)
        self.bar.set(0)

    def _draw_drop(self, hover):
        c = self.drop
        c.delete("all")
        x1, y1, x2, y2, r = 3, 3, W - 3, 133, 20
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        c.create_polygon(pts, smooth=True, fill="",
                         outline="#8a8a8a" if hover else "#4a4a4a",
                         width=2, dash=(7, 5))
        c.create_image(W // 2, 36, image=self._drop_photo)
        c.create_text(W // 2, 76, text="Drag & drop your audio file", fill=TEXT,
                      font=("Segoe UI", 10, "bold"))
        c.create_text(W // 2, 95, text="or click to browse", fill=MUTED,
                      font=("Segoe UI", 9))
        c.create_text(W // 2, 116, text="MP3, WAV, FLAC, M4A \u2014 max 40 min",
                      fill=MUTED, font=("Segoe UI", 7))

    # ---------- log window ----------
    def _ui_log(self, msg):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        self.logs.append(line)
        _log(msg)
        box = self._log_box
        if box is not None:
            try:
                box.configure(state="normal")
                box.insert("end", line + "\n")
                box.see("end")
                box.configure(state="disabled")
            except Exception:
                self._log_box = None

    def show_log(self):
        if self._log_win is not None:
            try:
                self._log_win.lift()
                return
            except Exception:
                self._log_win = None
        win = ctk.CTkToplevel(self)
        win.title("Log")
        self._center_window(win, 300, 210, parent=self)
        win.configure(fg_color=BG)
        win.transient(self)
        box = ctk.CTkTextbox(win, width=276, height=176, fg_color="#0d0d0d",
                             text_color=TEXT, font=ctk.CTkFont(family="Consolas", size=10))
        box.pack(padx=12, pady=12, fill="both", expand=True)
        box.insert("end", "\n".join(self.logs) + ("\n" if self.logs else ""))
        box.configure(state="disabled")

        def close():
            self._log_box = None
            self._log_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close)
        self._log_win = win
        self._log_box = box

    def show_guide(self):
        if self._guide is not None:
            try:
                self._guide.lift()
                return
            except Exception:
                self._guide = None
        g = ctk.CTkToplevel(self)
        g.title("How to use")
        self._center_window(g, 300, 300, parent=self)
        g.resizable(False, False)
        g.configure(fg_color=BG)
        g.transient(self)
        self._guide = g
        ctk.CTkLabel(g, text=GUIDE_TEXT, text_color=TEXT, justify="left", anchor="w",
                     wraplength=262, font=ctk.CTkFont(size=11)).pack(
            fill="both", expand=True, padx=18, pady=(14, 6))
        ctk.CTkButton(g, text="Close", width=110, height=30, fg_color="#151515",
                      hover_color="#262626", command=g.destroy).pack(pady=(0, 14))

    # ---------- actions ----------
    def pick_file(self):
        p = filedialog.askopenfilename(
            filetypes=[("Audio / Video", AUDIO_TYPES), ("All files", "*.*")])
        if not p:
            return
        length = _audio_length(p)
        if length is not None and length > MAX_SECONDS:
            self.src = None
            self.file_lbl.configure(text="This file is longer than 40 minutes.",
                                    text_color="#ff6b6b")
            return
        self.src = p
        self.link_entry.delete(0, "end")
        self.file_lbl.configure(text=os.path.basename(p), text_color=TEXT)
        self.status.configure(text="")

    def _show_running_ui(self):
        self.go_btn.pack_forget()
        self.folder_btn.pack_forget()
        self.back_btn.pack_forget()
        self.busy_btn.pack(side="left")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.bar.set(0)
        self.bar.pack(fill="x", pady=(9, 0), before=self.status)
        self.status.configure(text="Sending to server")

    def start(self):
        if self.job_id or self._submitting:
            return
        link = self.link_entry.get().strip()
        if not link and not self.src:
            self.status.configure(text="Select a file or paste a link first.")
            return
        if self.cfg.get("mode") != "bridge" and not self.cfg.get("token"):
            self.status.configure(text="Set your token in tetomidi.json first.")
            return
        self._submitting = True
        self._show_running_ui()
        if link:
            label = link
            fn = lambda: self.api.submit_link(link)
        else:
            label = os.path.basename(self.src)
            fn = lambda: self.api.submit_file(self.src)
        self._ui_log(f"Sending: {label}")

        def ok(jid):
            self._submitting = False
            self._begin_job(jid)

        def err(msg):
            self._submitting = False
            self.finish("Error: " + msg)

        self._bg(fn, ok, err)

    def _begin_job(self, jid):
        self.job_id = jid
        self.t0 = time.time()
        self._hb = 0
        self._last_pct = 0
        self._ui_log(f"Job started on server: {jid[:8]}")
        self.after(800, self._poll)

    def _poll(self):
        if not self.job_id or self._poll_busy:
            return
        self._poll_busy = True
        jid = self.job_id

        def ok(st):
            self._poll_busy = False
            self._apply(st)

        def err(msg):
            self._poll_busy = False
            self.finish("Lost connection to server: " + msg)

        self._bg(lambda: self.api.status(jid), ok, err)

    def _apply(self, st):
        if not self.job_id:
            return  # cancelled or finished in the meantime
        state = st.get("state")
        elapsed = time.time() - self.t0
        minutes = int(elapsed // 60)
        if state == "queued":
            self.status.configure(text=f"Waiting in server queue, position {st.get('position', 0)}")
            self.after(1000, self._poll)
        elif state == "running":
            p = st.get("progress")
            if p is not None:
                pct = max(1, min(99, int(p * 100)))
            else:
                # The endpoint sends no progress, so estimate: approaches 99% over about 3 minutes
                pct = max(1, min(99, int(99 * (1 - math.exp(-elapsed / 180)))))
            self.bar.set(pct / 100)
            self.status.configure(text=f"Transcribing to MIDI: {pct}%")
            # log every single percent step, so the log shows 1% ... 100%
            while self._last_pct < pct:
                self._last_pct += 1
                self._ui_log(f"Transcribing: {self._last_pct}%")
            if minutes > self._hb:
                self._hb = minutes
                self._ui_log(f"Still working, {minutes} min elapsed")
            self.after(1000, self._poll)
        elif state == "done":
            self._download(st)
        elif state == "error":
            self.finish("Error: " + (st.get("error") or "unknown").strip()[-300:])
        elif state == "cancelled":
            self.finish("Cancelled.")

    def _download(self, st):
        jid = self.job_id
        name = st.get("name") or "result.mid"
        dst = unique_path(output_dir(), name)
        self.status.configure(text="Downloading MIDI")

        def ok(_):
            self.finish(f"Done: {os.path.basename(dst)}", os.path.dirname(dst), dst)

        def err(msg):
            self.finish("Error downloading MIDI: " + msg)

        self._bg(lambda: self.api.download_midi(jid, dst), ok, err)

    def cancel(self):
        if not self.job_id:
            return
        jid = self.job_id
        self._bg(lambda: self.api.cancel(jid),
                 lambda _: self.finish("Cancelled."),
                 lambda m: self.finish("Cancel failed: " + m))

    def finish(self, text, folder=None, mid=None):
        self.job_id = None
        self.bar.set(1.0 if mid else 0)
        self.bar.pack_forget()
        self.busy_btn.pack_forget()
        self.cancel_btn.pack_forget()
        if mid:
            while self._last_pct < 100:
                self._last_pct += 1
                self._ui_log(f"Transcribing: {self._last_pct}%")
            self._ui_log("Transcriber done!")
            self.status.configure(text="Transcriber done!", text_color=TEXT)
            self.hint.configure(text="Open the folder icon to view the MIDI file")
        else:
            self._ui_log(text.replace("\n", " "))
            self.status.configure(text=text, text_color=MUTED)
            self.hint.configure(text="")
        if mid:
            self.folder = folder
            self.last_mid = mid
            self.go_btn.pack_forget()
            self.folder_btn.pack(side="left")
            self.back_btn.pack(side="left", padx=(10, 0))
        else:
            self.go_btn.pack()

    def open_result(self):
        """Open Explorer with the converted file selected, or the midi folder."""
        if sys.platform == "win32":
            if self.last_mid and os.path.exists(self.last_mid):
                subprocess.Popen(f'explorer /select,"{os.path.normpath(self.last_mid)}"')
            else:
                os.startfile(output_dir())
        else:
            # Linux / Mac: open the folder that holds the result
            folder = os.path.dirname(self.last_mid) if self.last_mid else output_dir()
            subprocess.Popen(["xdg-open", folder])

    def reset(self):
        """Back to the start state so a new file or link can be sent."""
        self.src = None
        self.last_mid = None
        self.link_entry.delete(0, "end")
        self.file_lbl.configure(text="No file selected", text_color=MUTED)
        self.status.configure(text="", text_color=MUTED)
        self.hint.configure(text="")
        self.folder_btn.pack_forget()
        self.back_btn.pack_forget()
        self.go_btn.pack()


if __name__ == "__main__":
    App().mainloop()
