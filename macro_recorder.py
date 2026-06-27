"""
MacroRecorder v2.0 - Sends input to a target window by name,
works even when MacroRecorder itself is in the foreground.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json, time, threading, os
from datetime import datetime

try:
    from pynput import mouse, keyboard
    from pynput.mouse import Button, Controller as MouseController
    from pynput.keyboard import Key, Controller as KeyboardController
    PYNPUT_OK = True
except ImportError:
    PYNPUT_OK = False

try:
    import win32gui, win32con, win32api, win32process
    import ctypes
    WIN32_OK = True
except ImportError:
    WIN32_OK = False

try:
    import schedule
    SCHEDULE_OK = True
except ImportError:
    SCHEDULE_OK = False


# ─────────────────────────────────────────────
#  WINDOW TARGETING HELPERS (Windows only)
# ─────────────────────────────────────────────

def list_windows():
    """Return list of (hwnd, title) for all visible windows."""
    results = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                results.append((hwnd, title))
    if WIN32_OK:
        win32gui.EnumWindows(callback, None)
    return results

def send_click_to_window(hwnd, x, y, button="left"):
    """Send a mouse click to an absolute screen position in a window."""
    if not WIN32_OK or not hwnd:
        return
    # Convert screen coords to client coords
    client_x, client_y = win32gui.ScreenToClient(hwnd, (x, y))
    lparam = win32api.MAKELONG(client_x, client_y)

    if button == "left":
        win32gui.SendMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.02)
        win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    else:
        win32gui.SendMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lparam)
        time.sleep(0.02)
        win32gui.SendMessage(hwnd, win32con.WM_RBUTTONUP, 0, lparam)

def send_key_to_window(hwnd, key_str, press=True):
    """Send a keypress to a window via PostMessage."""
    if not WIN32_OK or not hwnd:
        return
    vk = _vk_code(key_str)
    if vk is None:
        return
    if press:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
        win32gui.PostMessage(hwnd, win32con.WM_CHAR,    vk, 0)
    else:
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)

def send_scroll_to_window(hwnd, x, y, dy):
    if not WIN32_OK or not hwnd:
        return
    client_x, client_y = win32gui.ScreenToClient(hwnd, (x, y))
    lparam = win32api.MAKELONG(client_x, client_y)
    delta = int(dy * 120)
    wparam = win32api.MAKELONG(0, delta)
    win32gui.SendMessage(hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)

def _vk_code(key_str):
    """Map pynput key string to virtual key code."""
    special = {
        "Key.space": 0x20, "Key.enter": 0x0D, "Key.backspace": 0x08,
        "Key.tab": 0x09, "Key.esc": 0x1B, "Key.shift": 0x10,
        "Key.ctrl": 0x11, "Key.alt": 0x12, "Key.delete": 0x2E,
        "Key.up": 0x26, "Key.down": 0x28, "Key.left": 0x25, "Key.right": 0x27,
        "Key.home": 0x24, "Key.end": 0x23, "Key.page_up": 0x21, "Key.page_down": 0x22,
        "Key.f1":0x70,"Key.f2":0x71,"Key.f3":0x72,"Key.f4":0x73,
        "Key.f5":0x74,"Key.f6":0x75,"Key.f7":0x76,"Key.f8":0x77,
        "Key.f9":0x78,"Key.f10":0x79,"Key.f11":0x7A,"Key.f12":0x7B,
    }
    if key_str in special:
        return special[key_str]
    if len(key_str) == 1:
        return win32api.VkKeyScan(key_str) & 0xFF if WIN32_OK else ord(key_str.upper())
    return None


# ─────────────────────────────────────────────
#  MACRO ENGINE
# ─────────────────────────────────────────────

class MacroEngine:
    def __init__(self):
        self.events = []
        self.recording = False
        self.playing = False
        self._start_time = None
        self._mouse_listener = None
        self._keyboard_listener = None
        self._mouse_ctrl = MouseController() if PYNPUT_OK else None
        self._keyboard_ctrl = KeyboardController() if PYNPUT_OK else None
        self.target_hwnd = None   # if set, replay goes to this window

    def start_recording(self):
        if not PYNPUT_OK:
            raise RuntimeError("pynput not available — pip install pynput")
        self.events = []
        self.recording = True
        self._start_time = time.time()
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release)
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop_recording(self):
        self.recording = False
        if self._mouse_listener:  self._mouse_listener.stop()
        if self._keyboard_listener: self._keyboard_listener.stop()

    def _ts(self):
        return round(time.time() - self._start_time, 4)

    def _on_move(self, x, y):
        self.events.append({"type":"move","x":x,"y":y,"t":self._ts()})
    def _on_click(self, x, y, button, pressed):
        self.events.append({"type":"click","x":x,"y":y,"button":button.name,"pressed":pressed,"t":self._ts()})
    def _on_scroll(self, x, y, dx, dy):
        self.events.append({"type":"scroll","x":x,"y":y,"dx":dx,"dy":dy,"t":self._ts()})
    def _on_key_press(self, key):
        self.events.append({"type":"key_press","key":self._key_name(key),"t":self._ts()})
    def _on_key_release(self, key):
        self.events.append({"type":"key_release","key":self._key_name(key),"t":self._ts()})
    def _key_name(self, key):
        try: return key.char
        except AttributeError: return str(key)

    def play(self, speed=1.0, repeat=1, on_done=None):
        if not self.events: return
        self.playing = True

        def _run():
            for _ in range(repeat):
                if not self.playing: break
                prev_t = 0
                for ev in self.events:
                    if not self.playing: break
                    delay = (ev["t"] - prev_t) / speed
                    if delay > 0: time.sleep(delay)
                    prev_t = ev["t"]
                    self._replay(ev)
            self.playing = False
            if on_done: on_done()

        threading.Thread(target=_run, daemon=True).start()

    def stop_playback(self):
        self.playing = False

    def _replay(self, ev):
        hwnd = self.target_hwnd
        t = ev["type"]

        if hwnd and WIN32_OK:
            # ── Send directly to target window (background-safe) ──
            if t == "move":
                pass  # move not needed for window-targeted playback
            elif t == "click":
                if ev["pressed"]:
                    send_click_to_window(hwnd, ev["x"], ev["y"], ev.get("button","left"))
            elif t == "scroll":
                send_scroll_to_window(hwnd, ev["x"], ev["y"], ev.get("dy", 0))
            elif t == "key_press":
                send_key_to_window(hwnd, ev["key"], press=True)
            elif t == "key_release":
                send_key_to_window(hwnd, ev["key"], press=False)
        else:
            # ── Fallback: system-wide pynput (requires focus) ──
            if not PYNPUT_OK: return
            m = self._mouse_ctrl
            k = self._keyboard_ctrl
            if t == "move":
                m.position = (ev["x"], ev["y"])
            elif t == "click":
                btn = Button.left if ev.get("button") == "left" else Button.right
                m.position = (ev["x"], ev["y"])
                if ev["pressed"]: m.press(btn)
                else: m.release(btn)
            elif t == "scroll":
                m.scroll(ev.get("dx",0), ev.get("dy",0))
            elif t == "key_press":
                self._pk(k, ev["key"])
            elif t == "key_release":
                self._rk(k, ev["key"])

    def _pk(self, ctrl, key_str):
        try:
            sp = self._special(key_str)
            ctrl.press(sp if sp else key_str)
        except Exception: pass

    def _rk(self, ctrl, key_str):
        try:
            sp = self._special(key_str)
            ctrl.release(sp if sp else key_str)
        except Exception: pass

    def _special(self, s):
        m = {"Key.space":Key.space,"Key.enter":Key.enter,"Key.backspace":Key.backspace,
             "Key.tab":Key.tab,"Key.shift":Key.shift,"Key.ctrl":Key.ctrl,"Key.alt":Key.alt,
             "Key.esc":Key.esc,"Key.up":Key.up,"Key.down":Key.down,"Key.left":Key.left,
             "Key.right":Key.right,"Key.delete":Key.delete,"Key.home":Key.home,
             "Key.end":Key.end,"Key.page_up":Key.page_up,"Key.page_down":Key.page_down}
        return m.get(s)

    def save(self, path, name="", description=""):
        data = {"name": name or os.path.basename(path), "description": description,
                "created": datetime.now().isoformat(), "event_count": len(self.events),
                "duration": self.events[-1]["t"] if self.events else 0, "events": self.events}
        with open(path, "w") as f: json.dump(data, f, indent=2)

    def load(self, path):
        with open(path) as f: data = json.load(f)
        self.events = data.get("events", [])
        return data


# ─────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────

class MacroScheduler:
    def __init__(self):
        self._jobs = []; self._running = False

    def add_job(self, engine, interval_sec, repeat_times, label=""):
        job = {"label": label, "interval": interval_sec, "repeat": repeat_times,
               "engine": engine, "next_run": time.time() + interval_sec}
        self._jobs.append(job)
        if not self._running:
            self._running = True
            threading.Thread(target=self._loop, daemon=True).start()
        return job

    def remove_all(self): self._jobs.clear()
    def stop(self): self._running = False

    def _loop(self):
        while self._running:
            now = time.time()
            for job in list(self._jobs):
                if now >= job["next_run"] and not job["engine"].playing:
                    job["engine"].play(repeat=job["repeat"])
                    job["next_run"] = now + job["interval"]
            time.sleep(0.5)


# ─────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────

DARK_BG  = "#1a1d27"; PANEL_BG = "#22263a"; ACCENT  = "#6c63ff"
ACCENT2  = "#ff6584"; TEXT     = "#e8e8f0"; MUTED   = "#7b7fa8"
SUCCESS  = "#43d98c"; DANGER   = "#ff4d6d"; BORDER  = "#2e3250"; WARN = "#f5a623"
FONT_MONO = ("Consolas", 10); FONT_UI = ("Segoe UI", 10); FONT_HEAD = ("Segoe UI", 13, "bold")

def _btn(parent, text, cmd, color=ACCENT, fg="white", width=14):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg,
                     activebackground=color, activeforeground=fg,
                     font=("Segoe UI", 10, "bold"), bd=0,
                     padx=10, pady=8, width=width, cursor="hand2", relief=tk.FLAT)


# ─────────────────────────────────────────────
#  EDIT EVENT DIALOG
# ─────────────────────────────────────────────

class EditEventDialog(tk.Toplevel):
    def __init__(self, parent, event, on_save):
        super().__init__(parent)
        self.event = dict(event); self.on_save = on_save
        self.title("Edit Event"); self.configure(bg=DARK_BG)
        self.resizable(False, False); self.grab_set()
        self._fields = {}; self._build(); self.geometry("400x380")

    def _build(self):
        tk.Label(self, text="Edit Event", bg=DARK_BG, fg=TEXT, font=FONT_HEAD, pady=12).pack(anchor="w", padx=20)
        tk.Label(self, text=f"Type:  {self.event.get('type','').upper()}",
                 bg=DARK_BG, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20)
        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=8)

        form = tk.Frame(self, bg=DARK_BG); form.pack(fill=tk.X, padx=20)
        etype = self.event.get("type", "")
        editable = ["t"]
        if etype in ("move","click","scroll"): editable += ["x","y"]
        if etype == "click":  editable += ["button"]
        if etype == "scroll": editable += ["dx","dy"]
        if etype in ("key_press","key_release"): editable += ["key"]

        for i, key in enumerate(editable):
            tk.Label(form, text=key, bg=DARK_BG, fg=MUTED, font=FONT_UI,
                     width=10, anchor="w").grid(row=i, column=0, pady=4, sticky="w")
            var = tk.StringVar(value=str(self.event.get(key, "")))
            tk.Entry(form, textvariable=var, bg=PANEL_BG, fg=TEXT, font=FONT_MONO,
                     bd=0, insertbackground=TEXT, width=24).grid(row=i, column=1, pady=4, padx=8, sticky="w")
            self._fields[key] = (var, type(self.event.get(key, "")))

        self._pressed_var = None
        if etype == "click":
            self._pressed_var = tk.BooleanVar(value=self.event.get("pressed", True))
            tk.Label(form, text="pressed", bg=DARK_BG, fg=MUTED, font=FONT_UI,
                     width=10, anchor="w").grid(row=len(editable), column=0, pady=4, sticky="w")
            tk.Checkbutton(form, variable=self._pressed_var, bg=DARK_BG, fg=TEXT,
                           selectcolor=ACCENT, activebackground=DARK_BG).grid(row=len(editable), column=1, sticky="w")

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=12)
        row = tk.Frame(self, bg=DARK_BG); row.pack(pady=4)
        _btn(row, "✓  Save", self._save, SUCCESS, width=10).grid(row=0, column=0, padx=6)
        _btn(row, "✕  Cancel", self.destroy, MUTED, width=10).grid(row=0, column=1, padx=6)

    def _save(self):
        updated = dict(self.event)
        for key, (var, orig_type) in self._fields.items():
            raw = var.get().strip()
            try:
                if orig_type == int: updated[key] = int(raw)
                elif orig_type == float: updated[key] = float(raw)
                else: updated[key] = raw
            except ValueError:
                messagebox.showerror("Invalid value", f"'{raw}' is not valid for '{key}'"); return
        if self._pressed_var is not None:
            updated["pressed"] = self._pressed_var.get()
        self.on_save(updated); self.destroy()


# ─────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MacroRecorder"); self.geometry("980x680")
        self.minsize(800, 540); self.configure(bg=DARK_BG)
        self.engine = MacroEngine()
        self.scheduler = MacroScheduler()
        self._status_var = tk.StringVar(value="Ready")
        self._record_start = None; self._timer_id = None
        self._always_on_top = False
        self._build_ui(); self._update_buttons()

        if not PYNPUT_OK:
            messagebox.showwarning("Missing dependency",
                "Install pynput:\n  pip install pynput\n\nRecording disabled.")
        if not WIN32_OK:
            messagebox.showwarning("Windows-only feature",
                "Install pywin32 for background window targeting:\n  pip install pywin32")

    # ── Layout ─────────────────────────────────

    def _build_ui(self):
        self._build_sidebar(); self._build_main(); self._build_statusbar()

    def _build_sidebar(self):
        sb = tk.Frame(self, bg=PANEL_BG, width=215)
        sb.pack(side=tk.LEFT, fill=tk.Y); sb.pack_propagate(False)

        tk.Label(sb, text="⬡  MacroRecorder", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 12, "bold"), pady=20).pack(fill=tk.X, padx=12)
        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12)

        self._nav_buttons = []
        for label, cmd in [("🔴  Recorder", self._show_recorder),
                            ("▶  Library",   self._show_library),
                            ("🕐  Scheduler",self._show_scheduler),
                            ("⚙  Settings", self._show_settings)]:
            b = tk.Button(sb, text=label, bg=PANEL_BG, fg=TEXT,
                          activebackground=ACCENT, activeforeground="white",
                          font=FONT_UI, bd=0, padx=16, pady=10, anchor="w",
                          cursor="hand2", command=cmd)
            b.pack(fill=tk.X, pady=1); self._nav_buttons.append(b)
        self._nav_buttons[0].configure(bg=ACCENT, fg="white")

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)

        # Always on Top
        aot = tk.Frame(sb, bg=PANEL_BG); aot.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(aot, text="Always on Top", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._aot_btn = tk.Button(aot, text="OFF", bg=BORDER, fg=MUTED,
                                   font=("Segoe UI", 8, "bold"), bd=0, padx=8, pady=2,
                                   cursor="hand2", command=self._toggle_aot)
        self._aot_btn.pack(side=tk.RIGHT)

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)

        # Timer
        self._timer_var = tk.StringVar(value="00:00.0")
        tk.Label(sb, textvariable=self._timer_var, bg=PANEL_BG, fg=ACCENT2,
                 font=("Consolas", 22, "bold")).pack(pady=4)
        tk.Label(sb, text="recording time", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack()

        tk.Frame(sb, bg=PANEL_BG).pack(expand=True, fill=tk.Y)
        tk.Label(sb, text="v2.0.0", bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 8)).pack(pady=8)

    def _toggle_aot(self):
        self._always_on_top = not self._always_on_top
        self.wm_attributes("-topmost", self._always_on_top)
        if self._always_on_top:
            self._aot_btn.configure(text="ON", bg=ACCENT, fg="white")
        else:
            self._aot_btn.configure(text="OFF", bg=BORDER, fg=MUTED)
        self.set_status("Always on Top: " + ("ON ✓" if self._always_on_top else "OFF"))

    def _build_main(self):
        self._main = tk.Frame(self, bg=DARK_BG)
        self._main.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self._frames = {}
        for name, cls in [("recorder",RecorderPanel),("library",LibraryPanel),
                           ("scheduler",SchedulerPanel),("settings",SettingsPanel)]:
            f = cls(self._main, self)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = f
        self._show_panel("recorder")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=28)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, textvariable=self._status_var, bg=PANEL_BG,
                 fg=MUTED, font=("Segoe UI", 9), padx=12).pack(side=tk.LEFT)

    def _show_panel(self, n): self._frames[n].tkraise()
    def _highlight_nav(self, idx):
        for i, b in enumerate(self._nav_buttons):
            b.configure(bg=ACCENT if i==idx else PANEL_BG, fg="white" if i==idx else TEXT)
    def _show_recorder(self):  self._show_panel("recorder");  self._highlight_nav(0)
    def _show_library(self):   self._frames["library"].refresh(); self._show_panel("library");  self._highlight_nav(1)
    def _show_scheduler(self): self._show_panel("scheduler"); self._highlight_nav(2)
    def _show_settings(self):  self._show_panel("settings");  self._highlight_nav(3)

    def start_recording(self):
        try:
            self.engine.start_recording()
            self._record_start = time.time()
            self._tick_timer()
            self.set_status("🔴 Recording…")
            self._update_buttons()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def stop_recording(self):
        self.engine.stop_recording()
        if self._timer_id: self.after_cancel(self._timer_id)
        self.set_status(f"Recorded {len(self.engine.events)} events  ·  {self._timer_var.get()}")
        self._update_buttons()

    def start_playback(self):
        rp = self._frames["recorder"]
        speed  = float(rp.speed_var.get())
        repeat = int(rp.repeat_var.get())
        self.engine.play(speed=speed, repeat=repeat, on_done=self._on_play_done)
        target = self.engine.target_hwnd
        label  = win32gui.GetWindowText(target) if (target and WIN32_OK) else "system (focus-based)"
        self.set_status(f"▶ Playing into: {label}  ×{repeat}  at {speed}×")
        self._update_buttons()

    def stop_playback(self):
        self.engine.stop_playback()
        self.set_status("Stopped."); self._update_buttons()

    def _on_play_done(self):
        self.after(0, self._update_buttons)
        self.after(0, lambda: self.set_status("Playback complete."))

    def _tick_timer(self):
        if not self.engine.recording: return
        elapsed = time.time() - self._record_start
        self._timer_var.set(f"{int(elapsed//60):02d}:{elapsed%60:04.1f}")
        self._timer_id = self.after(100, self._tick_timer)

    def _update_buttons(self):
        self.after(0, self._frames["recorder"].sync_buttons)

    def set_status(self, msg): self._status_var.set(msg)
    def save_macro(self, path, name, desc): self.engine.save(path, name, desc)
    def load_macro(self, path):
        meta = self.engine.load(path)
        self.set_status(f"Loaded: {meta.get('name','')}  ({len(self.engine.events)} events)")
        self._update_buttons(); return meta


# ─────────────────────────────────────────────
#  RECORDER PANEL
# ─────────────────────────────────────────────

class RecorderPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app; self._build()

    def _build(self):
        tk.Label(self, text="Macro Recorder", bg=DARK_BG, fg=TEXT,
                 font=FONT_HEAD, pady=14).pack(anchor="w", padx=24)

        # ── Target Window selector ──────────────
        tgt_frame = tk.Frame(self, bg=PANEL_BG)
        tgt_frame.pack(fill=tk.X, padx=24, pady=(0, 10))
        inner = tk.Frame(tgt_frame, bg=PANEL_BG); inner.pack(fill=tk.X, padx=12, pady=8)

        tk.Label(inner, text="Target Window:", bg=PANEL_BG, fg=MUTED,
                 font=FONT_UI).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.target_var = tk.StringVar(value="— System (focus-based) —")
        self.target_cb  = ttk.Combobox(inner, textvariable=self.target_var,
                                        font=FONT_UI, width=42, state="readonly")
        self.target_cb.grid(row=0, column=1, padx=(0, 8))
        self.target_cb.bind("<<ComboboxSelected>>", self._on_target_select)

        _btn(inner, "🔄 Refresh", self._refresh_windows, PANEL_BG, width=10).grid(row=0, column=2)
        self._refresh_windows()

        # ── Transport ──────────────────────────
        btns = tk.Frame(self, bg=DARK_BG); btns.pack(padx=24, anchor="w", pady=(4,0))
        self.btn_record    = _btn(btns, "⏺  Record",    self.app.start_recording, DANGER)
        self.btn_stop      = _btn(btns, "⏹  Stop",      self._stop,               MUTED)
        self.btn_play      = _btn(btns, "▶  Play",      self.app.start_playback,  SUCCESS)
        self.btn_stop_play = _btn(btns, "⏹  Stop Play", self.app.stop_playback,   MUTED)
        for i, b in enumerate([self.btn_record, self.btn_stop, self.btn_play, self.btn_stop_play]):
            b.grid(row=0, column=i, padx=4)

        # ── Options ────────────────────────────
        opts = tk.Frame(self, bg=DARK_BG); opts.pack(padx=24, pady=8, anchor="w")
        tk.Label(opts, text="Speed:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=0, padx=(0,4))
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Combobox(opts, textvariable=self.speed_var, width=6,
                     values=["0.25","0.5","0.75","1.0","1.5","2.0","4.0"]).grid(row=0, column=1, padx=(0,16))
        tk.Label(opts, text="Repeat:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=2, padx=(0,4))
        self.repeat_var = tk.StringVar(value="1")
        tk.Spinbox(opts, textvariable=self.repeat_var, from_=1, to=9999,
                   width=6, bg=PANEL_BG, fg=TEXT, bd=0, font=FONT_UI).grid(row=0, column=3, padx=(0,16))

        # ── Save / Load ────────────────────────
        io = tk.Frame(self, bg=DARK_BG); io.pack(padx=24, anchor="w", pady=(0,8))
        _btn(io, "💾  Save Macro", self._save, ACCENT,   width=14).grid(row=0, column=0, padx=4)
        _btn(io, "📂  Load Macro", self._load, PANEL_BG, width=14).grid(row=0, column=1, padx=4)

        # ── Event table header ─────────────────
        th = tk.Frame(self, bg=DARK_BG); th.pack(fill=tk.X, padx=24, pady=(4,2))
        tk.Label(th, text="Event Log  (double-click to edit)",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        eb = tk.Frame(th, bg=DARK_BG); eb.pack(side=tk.RIGHT)
        _btn(eb, "✏ Edit",      self._edit_sel,   ACCENT,   width=8).grid(row=0, column=0, padx=2)
        _btn(eb, "＋ Add",      self._add_event,  PANEL_BG, width=8).grid(row=0, column=1, padx=2)
        _btn(eb, "🗑 Delete",   self._del_sel,    DANGER,   width=8).grid(row=0, column=2, padx=2)
        _btn(eb, "🗑 Clear All",self._clear_all,  DANGER,   width=10).grid(row=0, column=3, padx=2)
        _btn(eb, "⬆",          self._move_up,    PANEL_BG, width=3).grid(row=0, column=4, padx=2)
        _btn(eb, "⬇",          self._move_down,  PANEL_BG, width=3).grid(row=0, column=5, padx=2)

        # ── Event table ────────────────────────
        cols = ("#","Time(s)","Type","Details")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                  selectmode="extended", height=10)
        for col, w in zip(cols, [50,80,110,500]):
            self.tree.heading(col, text=col); self.tree.column(col, width=w, anchor="w")

        style = ttk.Style(); style.theme_use("clam")
        style.configure("Treeview", background=PANEL_BG, fieldbackground=PANEL_BG,
                        foreground=TEXT, rowheight=24, font=FONT_MONO)
        style.configure("Treeview.Heading", background=BORDER, foreground=MUTED,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])
        for tag, color in [("move",MUTED),("click",SUCCESS),("scroll",WARN),
                           ("key_press",ACCENT),("key_release","#a89bff")]:
            self.tree.tag_configure(tag, foreground=color)

        vsb = tk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True, fill=tk.BOTH, padx=(24,0), pady=(0,12))
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=(0,12), padx=(0,8))
        self.tree.bind("<Double-1>", lambda e: self._edit_sel())
        self._sched_refresh()

    # ── Window list ────────────────────────────

    def _refresh_windows(self):
        self._window_map = {"— System (focus-based) —": None}
        for hwnd, title in list_windows():
            label = title[:60]
            self._window_map[label] = hwnd
        self.target_cb["values"] = list(self._window_map.keys())
        # Keep current selection if still valid
        if self.target_var.get() not in self._window_map:
            self.target_var.set("— System (focus-based) —")
            self.app.engine.target_hwnd = None

    def _on_target_select(self, _=None):
        label = self.target_var.get()
        hwnd  = self._window_map.get(label)
        self.app.engine.target_hwnd = hwnd
        if hwnd:
            self.app.set_status(f"Target: {label}  (hwnd={hwnd})")
        else:
            self.app.set_status("Target: system (focus-based playback)")

    # ── Table ──────────────────────────────────

    def _detail(self, ev):
        t = ev.get("type","")
        if t == "move":   return f"x={ev['x']}  y={ev['y']}"
        if t == "click":  return f"x={ev['x']}  y={ev['y']}  btn={ev.get('button','?')}  {'DOWN' if ev.get('pressed') else 'UP'}"
        if t == "scroll": return f"x={ev['x']}  y={ev['y']}  dy={ev.get('dy',0)}"
        if t in ("key_press","key_release"): return f"key={ev.get('key','?')}"
        return str(ev)

    def refresh_table(self):
        sel = {self.tree.index(s) for s in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())
        for i, ev in enumerate(self.app.engine.events):
            iid = self.tree.insert("", tk.END,
                                   values=(i+1, f"{ev.get('t',0):.3f}", ev.get("type",""), self._detail(ev)),
                                   tags=(ev.get("type",""),))
            if i in sel: self.tree.selection_add(iid)

    def _sched_refresh(self):
        if self.app.engine.recording:
            self.refresh_table()
            ch = self.tree.get_children()
            if ch: self.tree.see(ch[-1])
        self.after(400, self._sched_refresh)

    def _sel_indices(self):
        return [self.tree.index(i) for i in self.tree.selection()]

    def _edit_sel(self):
        idx = self._sel_indices()
        if not idx: messagebox.showinfo("Select an event","Click a row first."); return
        i = idx[0]; ev = self.app.engine.events[i]
        def save(u): self.app.engine.events[i] = u; self.refresh_table(); self.app.set_status(f"Event #{i+1} updated.")
        EditEventDialog(self, ev, save)

    def _del_sel(self):
        indices = sorted(self._sel_indices(), reverse=True)
        if not indices: messagebox.showinfo("Select events","Click one or more rows first.\n\nTip: hold Ctrl to select multiple, Shift for a range."); return
        if not messagebox.askyesno("Delete?", f"Delete {len(indices)} event(s)?"): return
        for i in indices: del self.app.engine.events[i]
        self.refresh_table(); self.app.set_status(f"Deleted {len(indices)} event(s).")

    def _clear_all(self):
        if not self.app.engine.events: return
        if not messagebox.askyesno("Clear All?", f"Delete all {len(self.app.engine.events)} events?\n\nThis cannot be undone."): return
        self.app.engine.events.clear()
        self.refresh_table()
        self.app.set_status("All events cleared.")
        self.app._update_buttons()

    def _add_event(self):
        t = round(self.app.engine.events[-1]["t"] + 0.1, 4) if self.app.engine.events else 0.0
        ev = {"type":"click","x":0,"y":0,"button":"left","pressed":True,"t":t}
        self.app.engine.events.append(ev)
        def save(u): self.app.engine.events[-1] = u; self.refresh_table()
        EditEventDialog(self, ev, save)

    def _move_up(self):
        idx = self._sel_indices()
        if not idx or min(idx) == 0: return
        evs = self.app.engine.events
        for i in sorted(idx): evs[i-1], evs[i] = evs[i], evs[i-1]
        self.refresh_table()

    def _move_down(self):
        idx = self._sel_indices(); evs = self.app.engine.events
        if not idx or max(idx) >= len(evs)-1: return
        for i in sorted(idx, reverse=True): evs[i], evs[i+1] = evs[i+1], evs[i]
        self.refresh_table()

    def _stop(self):
        if self.app.engine.recording: self.app.stop_recording(); self.refresh_table()
        elif self.app.engine.playing: self.app.stop_playback()

    def _save(self):
        if not self.app.engine.events:
            messagebox.showwarning("Nothing to save","Record a macro first."); return
        win = tk.Toplevel(self); win.title("Save Macro")
        win.geometry("360x220"); win.configure(bg=DARK_BG); win.resizable(False,False)
        tk.Label(win,text="Macro Name:",bg=DARK_BG,fg=TEXT,font=FONT_UI).pack(anchor="w",padx=20,pady=(16,2))
        ne = tk.Entry(win,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT)
        ne.pack(fill=tk.X,padx=20); ne.insert(0,f"Macro_{datetime.now().strftime('%H%M%S')}")
        tk.Label(win,text="Description:",bg=DARK_BG,fg=TEXT,font=FONT_UI).pack(anchor="w",padx=20,pady=(10,2))
        de = tk.Entry(win,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT)
        de.pack(fill=tk.X,padx=20)
        def do():
            path = filedialog.asksaveasfilename(defaultextension=".macro",
                    filetypes=[("Macro","*.macro"),("JSON","*.json")], initialfile=ne.get())
            if path: self.app.save_macro(path,ne.get(),de.get()); self.app.set_status(f"Saved: {os.path.basename(path)}"); win.destroy()
        _btn(win,"Save",do,ACCENT,width=10).pack(pady=12)

    def _load(self):
        path = filedialog.askopenfilename(filetypes=[("Macro","*.macro"),("JSON","*.json"),("All","*.*")])
        if path:
            try: self.app.load_macro(path); self.refresh_table()
            except Exception as e: messagebox.showerror("Load error",str(e))

    def sync_buttons(self):
        eng = self.app.engine
        rec,playing,has = eng.recording,eng.playing,bool(eng.events)
        self.btn_record.configure(state=tk.DISABLED if rec or playing else tk.NORMAL,
                                   bg=DANGER if not(rec or playing) else MUTED)
        self.btn_stop.configure(state=tk.NORMAL if rec or playing else tk.DISABLED)
        self.btn_play.configure(state=tk.NORMAL if has and not rec and not playing else tk.DISABLED)
        self.btn_stop_play.configure(state=tk.NORMAL if playing else tk.DISABLED)


# ─────────────────────────────────────────────
#  LIBRARY / SCHEDULER / SETTINGS (unchanged)
# ─────────────────────────────────────────────

class LibraryPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        h=tk.Frame(self,bg=DARK_BG); h.pack(fill=tk.X,padx=24,pady=(20,8))
        tk.Label(h,text="Macro Library",bg=DARK_BG,fg=TEXT,font=FONT_HEAD).pack(side=tk.LEFT)
        _btn(h,"📂 Open Folder",self._open_folder,PANEL_BG,width=12).pack(side=tk.RIGHT)
        cols=("Name","Events","Duration","Created","Path")
        self.tree=ttk.Treeview(self,columns=cols,show="headings",selectmode="browse")
        for col,w in zip(cols,[200,80,90,160,300]):
            self.tree.heading(col,text=col); self.tree.column(col,width=w,anchor="w")
        vsb=tk.Scrollbar(self,orient="vertical",command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True,fill=tk.BOTH,padx=24); vsb.pack(side=tk.RIGHT,fill=tk.Y)
        br=tk.Frame(self,bg=DARK_BG); br.pack(padx=24,pady=10,anchor="w")
        _btn(br,"▶  Load & Play",self._load_play,SUCCESS,width=13).grid(row=0,column=0,padx=4)
        _btn(br,"📥  Load",self._load_sel,ACCENT,width=10).grid(row=0,column=1,padx=4)
        _btn(br,"🗑  Delete",self._delete,DANGER,width=10).grid(row=0,column=2,padx=4)
    def refresh(self,folder=None):
        folder=folder or os.path.expanduser("~")
        self.tree.delete(*self.tree.get_children())
        for f in os.listdir(folder):
            if f.endswith(".macro") or (f.endswith(".json") and "macro" in f.lower()):
                path=os.path.join(folder,f)
                try:
                    with open(path) as fp: data=json.load(fp)
                    self.tree.insert("",tk.END,values=(data.get("name",f),data.get("event_count","?"),
                        f"{round(data.get('duration',0),1)}s",data.get("created","")[:16],path))
                except: pass
    def _open_folder(self):
        folder=filedialog.askdirectory()
        if folder: self.refresh(folder)
    def _sel_path(self):
        sel=self.tree.selection()
        if not sel: messagebox.showinfo("Select a macro","Click a macro first."); return None
        return self.tree.item(sel[0])["values"][4]
    def _load_sel(self):
        p=self._sel_path()
        if p: self.app.load_macro(p); self.app._show_recorder(); self.app._frames["recorder"].refresh_table()
    def _load_play(self):
        p=self._sel_path()
        if p: self.app.load_macro(p); self.app._show_recorder(); self.app._frames["recorder"].refresh_table(); self.after(300,self.app.start_playback)
    def _delete(self):
        p=self._sel_path()
        if p and messagebox.askyesno("Delete?",f"Delete {os.path.basename(p)}?"): os.remove(p); self.refresh()

class SchedulerPanel(tk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        tk.Label(self,text="Scheduler",bg=DARK_BG,fg=TEXT,font=FONT_HEAD,pady=20).pack(anchor="w",padx=24)
        form=tk.Frame(self,bg=PANEL_BG); form.pack(fill=tk.X,padx=24,pady=(0,12))
        inner=tk.Frame(form,bg=PANEL_BG); inner.pack(padx=16,pady=12)
        for col,label in enumerate(["Run every (seconds)","Repeat times","Label"]):
            tk.Label(inner,text=label,bg=PANEL_BG,fg=MUTED,font=FONT_UI).grid(row=0,column=col,padx=8,sticky="w")
        self.interval_var=tk.StringVar(value="60"); self.repeat_var=tk.StringVar(value="1"); self.label_var=tk.StringVar(value="Scheduled Macro")
        for col,(var,w) in enumerate([(self.interval_var,10),(self.repeat_var,8),(self.label_var,20)]):
            tk.Entry(inner,textvariable=var,width=w,bg=DARK_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT).grid(row=1,column=col,padx=8,pady=4,sticky="w")
        _btn(inner,"＋ Add Job",self._add_job,ACCENT,width=12).grid(row=1,column=3,padx=12)
        tk.Label(self,text="Active Jobs",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9),pady=4).pack(anchor="w",padx=24)
        self.jobs_box=tk.Listbox(self,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,selectbackground=ACCENT,height=12)
        self.jobs_box.pack(fill=tk.BOTH,expand=True,padx=24,pady=(0,8))
        _btn(self,"🗑  Clear All Jobs",self._clear,DANGER,width=16).pack(anchor="w",padx=24,pady=4)
    def _add_job(self):
        if not self.app.engine.events: messagebox.showwarning("No macro","Record or load a macro first."); return
        try: interval=float(self.interval_var.get()); repeat=int(self.repeat_var.get())
        except ValueError: messagebox.showerror("Invalid input","Enter valid numbers."); return
        label=self.label_var.get()
        self.app.scheduler.add_job(self.app.engine,interval,repeat,label)
        self.jobs_box.insert(tk.END,f"  ⏰  {label}  —  every {interval}s  ×{repeat}")
    def _clear(self): self.app.scheduler.remove_all(); self.jobs_box.delete(0,tk.END)

class SettingsPanel(tk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        tk.Label(self,text="Settings",bg=DARK_BG,fg=TEXT,font=FONT_HEAD,pady=20).pack(anchor="w",padx=24)
        for text,default in [("Record mouse movements",True),("Record keyboard events",True),
                              ("Show event log while recording",True),("Confirm before playback",False)]:
            var=tk.BooleanVar(value=default)
            tk.Checkbutton(self,text=text,variable=var,bg=DARK_BG,fg=TEXT,selectcolor=ACCENT,
                           activebackground=DARK_BG,activeforeground=TEXT,font=FONT_UI).pack(anchor="w",padx=28,pady=4)
        tk.Label(self,text="\nHotkeys",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9)).pack(anchor="w",padx=24)
        for key,action in [("F9","Start/Stop Recording"),("F10","Start Playback"),("Esc","Stop Playback")]:
            row=tk.Frame(self,bg=PANEL_BG); row.pack(fill=tk.X,padx=24,pady=2)
            tk.Label(row,text=f"  {key}",bg=PANEL_BG,fg=ACCENT,font=FONT_MONO,width=8).pack(side=tk.LEFT)
            tk.Label(row,text=action,bg=PANEL_BG,fg=TEXT,font=FONT_UI).pack(side=tk.LEFT,padx=8)
        tk.Label(self,text="\nDependencies",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9)).pack(anchor="w",padx=24)
        for lib,ok in [("pynput",PYNPUT_OK),("pywin32",WIN32_OK),("schedule",SCHEDULE_OK)]:
            status="✓  installed" if ok else "✗  missing  —  pip install "+lib
            tk.Label(self,text=f"  {lib}:  {status}",bg=DARK_BG,fg=SUCCESS if ok else DANGER,font=FONT_MONO).pack(anchor="w",padx=28)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
