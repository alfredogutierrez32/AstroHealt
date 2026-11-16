"""
AstroHealth · by cyberAhedos
NASA Space Apps Challenge 2026 - Health Monitoring Software for Astronauts

Desktop interface (tkinter, included with Python).
Run:  python astrohealth.py   (or the Run button in PyCharm)

Synthetic data · Illustrative rules · Do not use for real medical decisions.
"""
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from database import Database, SaveError
from voice import Voice, VoiceWatcher
import mobile_server
from wristband import Wristband
from helmet import Helmet
from routines import Routines, ROUTINES, TEAM_GOAL
from simulation import (
    Mission, INDICATORS, IND, SCENARIO_NAMES, BROADCAST_PREFIX,
    OK, CAUTION, ALERT, NO_SIGNAL, INVALID, STATE_TEXT, NEXT_STEP,
    MODE_SIMULATION, status_of, format_value,
)

# --------------------------------------------------------------------------
# Style
# --------------------------------------------------------------------------
BG = "#0F1E30"
CARD = "#14283E"
EDGE = "#264763"
TEXT = "#E6F1FA"
DIM = "#8FA6BD"
DARK = "#122640"
ACCENT = "#3DDC97"
SEG_BG = "#0F1E30"

COLOR = {   # state -> (text, background)
    OK: ("#3DDC97", "#123B33"),
    CAUTION: ("#F2A541", "#3D2E14"),
    ALERT: ("#FF5C6C", "#3D1A22"),
    NO_SIGNAL: ("#8FA6BD", "#1E3047"),
    INVALID: ("#B79CE0", "#2A2342"),
}
ICON = {OK: "✓", CAUTION: "!", ALERT: "▲", NO_SIGNAL: "–", INVALID: "✕"}
ADVANCE_STEP = 10          # simulated minutes per block on long advances (the window stays responsive)

F = "Segoe UI"
F_TITLE = (F, 24, "bold")
F_H2 = (F, 14, "bold")
F_NORMAL = (F, 10)
F_SMALL = (F, 8)
F_CAPS = (F, 8, "bold")
F_VALUE = (F, 22, "bold")

MIN_WINDOW = 20        # minutes visible in the graph
SPEEDS = {"1x": 1000, "2x": 500, "5x": 200}


def button(parent_, text, command_, bg=CARD, fg=TEXT, source=F_NORMAL, padx=12, pady=6):
    """Button made with a Label so it looks the same on Windows, Mac and Linux."""
    b = tk.Label(parent_, text=text, bg=bg, fg=fg, font=source, padx=padx, pady=pady,
                 cursor="hand2", highlightthickness=1, highlightbackground=EDGE)
    b.bind("<Button-1>", lambda e: command_())
    return b


def make_clickable(widget, command_):
    widget.bind("<Button-1>", lambda e: command_())
    widget.configure(cursor="hand2")
    for child in widget.winfo_children():
        make_clickable(child, command_)


def chip(parent_, status, text=None):
    fg, bg = COLOR[status]
    return tk.Label(parent_, text=f"{ICON[status]} {text or STATE_TEXT[status]}",
                    fg=fg, bg=bg, font=F_SMALL, padx=6, pady=2)


def paint_chip(lbl, status, text=None):
    fg, bg = COLOR[status]
    lbl.configure(text=f"{ICON[status]} {text or STATE_TEXT[status]}", fg=fg, bg=bg)


def range_text(ind):
    lo, hi = ind.range_
    d = ind.decimals
    return f"Demo range: {lo:.{d}f}–{hi:.{d}f} {ind.unit}"


# --------------------------------------------------------------------------
# Graphs on Canvas
# --------------------------------------------------------------------------
def draw_graph(cv, values_, ind, complete=True):
    cv.delete("all")
    w, h = cv.winfo_width(), cv.winfo_height()
    if w < 20 or h < 20:
        return
    left_, right_, arr, aba = (44, 12, 10, 22) if complete else (2, 2, 3, 3)
    data_ = [v for v in values_ if v is not None]
    lo, hi = ind.range_
    clo, chi = ind.critical
    candidates = [lo, hi] + data_ + [c for c in (clo, chi) if c is not None]
    ymin, ymax = min(candidates), max(candidates)
    pad = (ymax - ymin) * 0.12 or 1
    ymin, ymax = ymin - pad, ymax + pad

    def y(v):
        return arr + (ymax - v) / (ymax - ymin) * (h - arr - aba)

    n = max(len(values_), 2)

    def x(i):
        return left_ + i / (n - 1) * (w - left_ - right_)

    # normal-range band
    cv.create_rectangle(left_, y(hi), w - right_, y(lo), fill="#16443F", outline="")
    if complete:
        # critical lines
        for c in (clo, chi):
            if c is not None:
                cv.create_line(left_, y(c), w - right_, y(c), fill="#E06B6B", dash=(4, 3))
        # y axis
        for k in range(5):
            v = ymin + (ymax - ymin) * k / 4
            cv.create_text(left_ - 6, y(v), text=f"{v:.{ind.decimals}f}", anchor="e",
                           font=F_SMALL, fill=DIM)
            cv.create_line(left_, y(v), w - right_, y(v), fill="#1E3650")
        # x axis
        cv.create_text(left_, h - 8, text=f"-{len(values_) - 1} min", anchor="w",
                       font=F_SMALL, fill=DIM)
        cv.create_text(w - right_, h - 8, text="now", anchor="e", font=F_SMALL, fill=DIM)

    # data line (with gaps where there was no signal)
    segment = []
    for i, v in enumerate(values_):
        if v is None:
            if len(segment) >= 4:
                cv.create_line(*segment, fill="#5FE0C0", width=2 if complete else 1.5, smooth=True)
            segment = []
        else:
            segment += [x(i), y(v)]
    if len(segment) >= 4:
        cv.create_line(*segment, fill="#5FE0C0", width=2 if complete else 1.5, smooth=True)

    last = values_[-1] if values_ else None
    if last is not None:
        fg = COLOR[status_of(ind.key, last)][0]
        r = 4 if complete else 2.5
        xi, yi = x(len(values_) - 1), y(last)
        cv.create_oval(xi - r, yi - r, xi + r, yi + r, fill=fg, outline="#0F1E30")
    elif complete:
        cv.create_text(w / 2, h / 2, text="NO SIGNAL", font=F_H2, fill=DIM)


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------
class AstroHealth(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AstroHealth · Ship console · cyberAhedos")
        self.geometry("1320x880")
        self.minsize(1180, 800)
        self.configure(bg=BG)
        self._blue_theme()

        self.mission = Mission()
        self.db = Database()                 # ship database (astrohealth.db)
        self.routines = Routines(self.mission)
        self._pending_advance = 0            # minutes left in a long advance
        self._save_error = None
        self._watch_id = None
        self._save(lambda: self.db.receive_transmission(self.mission, self.routines))   # minute 0
        # voice: reads red alerts and oxygen notices aloud (see voice.py)
        self.voice = Voice()
        self.voice_watcher = VoiceWatcher(self.mission, self.voice)
        self._voice_id = None
        self.mobile = None                     # server to use the phone as a wristband
        self._mobile_t = 0.0
        self.restarts = 0                     # times the simulation was restarted
        self.wristbands = []                  # open wristbands
        self.viewing = self.mission.me          # crew member shown in "My health"
        self.metric = "hr"                   # graph indicator
        self.playing = False
        self.view_name = "mi"
        self._after = None

        self._build_sidebar()
        body = tk.Frame(self, bg=BG)
        body.pack(side="left", fill="both", expand=True, padx=28, pady=16)
        self._build_header(body)
        self._build_explorer(body)

        self.container = tk.Frame(body, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.my_view = tk.Frame(self.container, bg=BG)
        self.crew_view = tk.Frame(self.container, bg=BG)
        self._build_my_view(self.my_view)
        self._build_crew_view(self.crew_view)

        self.show("mi")
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        # on start your wristband opens (the commander's)
        self.after(400, lambda: self.open_wristband(self.mission.me))
        self.after(600, lambda: self.open_wristband(self.mission.me, Helmet))
        self._watch_id = self.after(1000, self._watch)
        self._voice_id = self.after(100, self._voice_cycle)

    def _blue_theme(self):
        """Blue colors for the ttk controls (dropdowns) and menus."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")        # el tema nativo de Windows ignora los colores de fondo
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT,
                         arrowcolor=TEXT, bordercolor=EDGE, lightcolor=CARD, darkcolor=CARD)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)],
                   selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)],
                   background=[("readonly", CARD), ("active", CARD)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", "#1E3E5C")
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*Menu.background", CARD)
        self.option_add("*Menu.foreground", TEXT)
        self.option_add("*Menu.activeBackground", "#1E3E5C")
        self.option_add("*Menu.activeForeground", "white")

    # ---------------- voice ----------------
    def _voice_cycle(self):
        """Every 100 ms: looks for new alerts and reads at most one (the rules are in voice.py)."""
        self.voice_watcher.check()
        self.voice.tick()
        self._mobile_cycle()
        self._voice_id = self.after(100, self._voice_cycle)

    # ---------------- phone as wristband ----------------
    def _mobile_cycle(self):
        if self.mobile is None:
            return
        change = False
        for action in self.mobile.pending_actions():     # taps that arrived from the phone
            change |= mobile_server.execute_action(self.mission, self.routines, action)
        if change:
            self.record_action()
        self._sync_receivers()
        now_ = time.monotonic()
        if change or now_ - self._mobile_t >= 0.25:          # state to the phone ~4 times per second
            self._mobile_t = now_
            self.mobile.publish(mobile_server.snapshot(
                self.mission, self.routines, self.voice, self.mobile_notifier.check(), self.restarts))

    def _sync_receivers(self):
        """'Received' only counts devices that are really open: windows and connected phones."""
        open_ones = {wb.owner for wb in self.wristbands}
        if self.mobile is not None:
            open_ones |= self.mobile.connected()
        self.mission.receivers.clear()
        self.mission.receivers.update(open_ones)

    def open_phone(self):
        if self.mobile is None:
            try:
                self.mobile = mobile_server.MobileServer(len(self.mission.crew)).start()
            except OSError as e:
                self.mobile = None
                messagebox.showerror("AstroHealth", f"Could not start the phone server:\n{e}")
                return
            self.mobile_notifier = mobile_server.Notifier(self.mission, self.routines)
            self._save(lambda: self.db.record_change("console", "phone server started (local network)"))
        lines = "\n".join(f"{t.name_.split()[0]}:  {self.mobile.address(i)}"
                           for i, t in enumerate(self.mission.crew))
        messagebox.showinfo("📱 Phone as wristband",
                            "Connect the phone to the SAME WiFi as this computer and open in its browser:\n\n"
                            f"{lines}\n\nAccess code: {self.mobile.code_}\n\n"
                            "Tip: in the browser use \"Add to Home Screen\" to see it full screen.\n"
                            "If Windows asks about the firewall, allow access on PRIVATE networks.")

    def toggle_voice(self):
        if self.voice.engine is None:
            messagebox.showinfo("AstroHealth", "There is no voice engine on this computer.\n"
                                "Install one with:  pip install pyttsx3\n(and open the program again)")
            return
        self.voice.enabled = not self.voice.enabled
        self.btn_voice.configure(text="🔊 Voice: on" if self.voice.enabled else "🔇 Voice: off")
        self._save(lambda: self.db.record_change(
            "console", "alert voice " + ("enabled" if self.voice.enabled else "disabled")))

    # ---------------- sidebar ----------------
    def _build_sidebar(self):
        sb = tk.Frame(self, bg=DARK, width=150)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)
        tk.Label(sb, text="〰", bg=DARK, fg="#5FE0C0", font=(F, 26, "bold")).pack(pady=(22, 0))
        tk.Label(sb, text="ASTROHEALTH", bg=DARK, fg="#CFE3F5", font=F_CAPS).pack(pady=(0, 30))
        self.nav = {}
        for key, text in (("mi", "◉  My health"), ("crew", "◎  Crew")):
            b = tk.Label(sb, text=text, bg=DARK, fg="#CFE3F5", font=(F, 11),
                         anchor="w", padx=18, pady=10, cursor="hand2")
            b.pack(fill="x")
            b.bind("<Button-1>", lambda e, c=key: self.show(c))
            self.nav[key] = b
        tk.Label(sb, text="Space Apps 2026", bg=DARK, fg="#6F8AA6",
                 font=F_SMALL).pack(side="bottom", pady=14)

    # ---------------- header ----------------
    def _build_header(self, parent_):
        row = tk.Frame(parent_, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="AstroHealth", bg=BG, fg=TEXT, font=(F, 18, "bold")).pack(side="left")
        tk.Label(row, text="  /  ship console  ·  by cyberAhedos", bg=BG, fg=DIM,
                 font=(F, 11)).pack(side="left", pady=(4, 0))
        button(row, "Export session ↗", self.export).pack(side="right")
        self.btn_voice = button(row, "🔊 Voice: on" if self.voice.engine else "🔇 Voice: no engine", self.toggle_voice)
        self.btn_voice.pack(side="right", padx=4)
        button(row, "📱 Phone", self.open_phone).pack(side="right", padx=4)
        button(row, "⛑ All helmets", self.open_all_helmets).pack(side="right", padx=4)
        button(row, "⛑ Open helmet", lambda: self.open_target_wristband(Helmet)).pack(side="right", padx=4)
        button(row, "⌚ Open wristband", self.open_target_wristband).pack(side="right", padx=4)
        self.lbl_db = tk.Label(row, bg=BG, fg=DIM, font=F_SMALL)
        self.lbl_db.pack(side="right", padx=8)

        band = tk.Frame(parent_, bg=BG)
        band.pack(fill="x", pady=(10, 8))
        self.lbl_mode = tk.Label(band, text=self.mission.mode, bg="#16443F", fg=ACCENT, font=F_CAPS,
                                 padx=8, pady=3)
        self.lbl_mode.pack(side="left")
        self.lbl_mode_note = tk.Label(band, bg=BG, fg=DIM, font=F_SMALL)
        self.lbl_mode_note.pack(side="left")
        self.lbl_progress = tk.Label(band, bg=BG, fg=ACCENT, font=F_CAPS)
        self.lbl_progress.pack(side="right")

    # ---------------- scenario explorer ----------------
    def _build_explorer(self, parent_):
        ex = tk.Frame(parent_, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        ex.pack(fill="x", pady=(0, 12))
        left_ = tk.Frame(ex, bg=CARD)
        left_.pack(side="left", padx=14, pady=10)
        tk.Label(left_, text="EXPLORE A SCENARIO", bg=CARD, fg=TEXT, font=F_CAPS).pack(anchor="w")
        tk.Label(left_, text="1 step = 1 simulated minute · at rest", bg=CARD, fg=DIM,
                 font=F_SMALL).pack(anchor="w")

        # who it applies to
        aq = tk.Frame(ex, bg=CARD)
        aq.pack(side="left", padx=(10, 6))
        tk.Label(aq, text="Apply to", bg=CARD, fg=DIM, font=F_SMALL).pack(anchor="w")
        self.target_options = [f"{t.name_}" + ("  (you)" if i == self.mission.me else "")
                                  for i, t in enumerate(self.mission.crew)]
        self.target_options.append("Whole crew")
        self.combo = ttk.Combobox(aq, values=self.target_options, state="readonly", width=20)
        self.combo.current(self.mission.me)
        self.combo.pack()
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        # segmented control
        secs = tk.Frame(ex, bg=SEG_BG, padx=3, pady=3)
        secs.pack(side="left", padx=10)
        self.buttons_secs = {}
        for name_ in SCENARIO_NAMES:
            b = tk.Label(secs, text=name_, bg=SEG_BG, fg=DIM, font=F_NORMAL,
                         padx=12, pady=5, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, n=name_: self.choose_scenario(n))
            self.buttons_secs[name_] = b

        right_ = tk.Frame(ex, bg=CARD)
        right_.pack(side="right", padx=12)
        self.btn_play = button(right_, "▶  Play", self.toggle_playback, bg=ACCENT, fg="#06121F")
        self.btn_play.pack(side="left", padx=4)
        # advance button: one click opens +1 min, +10 min, +30 min, +1 h and +5 h
        self.btn_more = button(right_, "+1 min  ▾", self._advance_menu)
        self.btn_more.pack(side="left", padx=4)
        self.advance_menu = tk.Menu(self, tearoff=0, font=F_NORMAL)
        for text, minutes_ in (("+1 min", 1), ("+10 min", 10), ("+30 min", 30), ("+1 h", 60), ("+5 h", 300)):
            self.advance_menu.add_command(label=text, command=lambda n=minutes_: self.advance(n))
        button(right_, "⟳ Random mission", self.random_mission).pack(side="left", padx=4)
        button(right_, "↺ Restart", self.restart_simulation).pack(side="left", padx=4)
        self.spd = ttk.Combobox(right_, values=list(SPEEDS), state="readonly", width=4)
        self.spd.current(0)
        self.spd.pack(side="left", padx=4)

    def target_index(self):
        i = self.combo.current()
        return -1 if i == len(self.mission.crew) else i

    def choose_scenario(self, name_):
        i = self.target_index()
        if i == -1:
            for k in range(len(self.mission.crew)):
                self.mission.set_scenario(k, name_)
        else:
            self.mission.set_scenario(i, name_)
        who = "the whole crew" if i == -1 else self.mission.crew[i].pseudonym
        self._save(lambda: self.db.record_change("console", f"scenario '{name_}' for{who}"))
        self.refresh()

    def random_mission(self):
        self.mission.random_scenario()
        self._save(lambda: self.db.record_change("console", "random scenarios"))
        self.refresh()

    def restart_simulation(self, confirm=True):
        """↺ RESTART: goes back to minute 0 with a healthy crew, points at zero and no alarms.
        The database is not deleted: what came before stays saved as another session and a new one opens.
        Open wristbands and helmets are reopened in the same place; the phone stays connected."""
        if confirm and not messagebox.askyesno(
                "AstroHealth", "Restart the simulation?\n\nIt goes back to minute 0, with no alarms and points "
                               "at zero.\nThis session is NOT deleted: it stays saved in the database."):
            return False
        # 1) stop what is running
        if self.playing:
            self.toggle_playback()
        self._pending_advance = 0
        self.voice.silence()
        # 2) save the current session and open a new one (if it fails, nothing is restarted)
        try:
            previous = self.db.new_session(self.mission, self.routines, self.mission.mode)
        except SaveError as e:
            messagebox.showerror("AstroHealth", f"Not restarted, to avoid losing data:\n{e}")
            return False
        # 3) close the open devices (they are reopened afterwards in the same place)
        open_ = []
        for w in list(self.wristbands):
            open_.append((w.owner, type(w), getattr(w, "hud_scale", None) if isinstance(w, Helmet) else None,
                          (w.winfo_x(), w.winfo_y())))
            w.close_window()
        # 4) new simulation
        self.mission = Mission()
        self.routines = Routines(self.mission)
        self.voice_watcher = VoiceWatcher(self.mission, self.voice)
        self.restarts += 1
        if self.mobile is not None:
            self.mobile_notifier = mobile_server.Notifier(self.mission, self.routines,
                                                          start_at=self.mobile_notifier._n)
        self.viewing = self.mission.me
        self.combo.current(self.mission.me)
        self._save(lambda: self.db.record_change(
            "console", f"simulation restarted (previous session {previous})"))
        self._save(lambda: self.db.receive_transmission(self.mission, self.routines))   # minute 0
        for owner, cls_, scale, position in open_:
            self.open_wristband(owner, cls_, hud_scale=scale, position=position)
        self._sync_receivers()
        self.show("mi")
        self.refresh()
        return True

    def toggle_playback(self):
        self.playing = not self.playing
        self.btn_play.configure(text="❚❚  Pause" if self.playing else "▶  Play")
        if self.playing:
            self._tick()
        elif self._after:
            self.after_cancel(self._after)
            self._after = None

    def _tick(self):
        if not self.playing:
            return
        self.one_step()
        self._after = self.after(SPEEDS[self.spd.get()], self._tick)

    def one_step(self):
        self.advance(1)

    def _advance_menu(self):
        """Opens the advance menu below the button."""
        x = self.btn_more.winfo_rootx()
        y = self.btn_more.winfo_rooty() + self.btn_more.winfo_height()
        try:
            self.advance_menu.tk_popup(x, y)
        finally:
            self.advance_menu.grab_release()

    def advance(self, minutes_):
        """Advances several minutes without freezing the window: it is processed in blocks with after()."""
        if self._pending_advance:             # an advance is already running: it is added, not duplicated
            self._pending_advance += minutes_
            return
        self._pending_advance = minutes_
        self._advance_block()

    def _advance_block(self):
        n = min(ADVANCE_STEP, self._pending_advance)
        for _ in range(n):
            self.mission.step()
            self.routines.step()
            # the wristbands transmit; the ship saves every minute (transaction, cursor after commit)
            if not self._save(lambda: self.db.receive_transmission(self.mission, self.routines)):
                self._pending_advance = 0
                break
        else:
            self._pending_advance -= n
        if self._pending_advance > 0:
            self.lbl_progress.configure(text=f"Advancing… {self._pending_advance} min")
            self.refresh()
            self.after(1, self._advance_block)
        else:
            self._pending_advance = 0
            self.lbl_progress.configure(text="")
            self.refresh()

    # ---------------- persistence ----------------
    def _save(self, action):
        """Runs a database write. If it fails, it shows it (the data stays in memory and
        is retried on the next save, because the cursor did not advance)."""
        try:
            action()
        except SaveError as e:
            if self._save_error is None:
                self._save_error = str(e)
                try:
                    messagebox.showerror("AstroHealth", f"Could not save to the database:\n{e}\n"
                                         "It will be retried automatically.")
                except tk.TclError:
                    pass
            return False
        self._save_error = None
        return True

    def record_action(self):
        """A wristband responded to an alarm: it is saved right away (response + points) and refreshed."""
        self._save(lambda: self.db.sync(self.mission, self.routines))
        self.refresh()

    # ---------------- wristbands ----------------
    def open_target_wristband(self, cls_=Wristband):
        i = self.target_index()
        self.open_wristband(self.mission.me if i == -1 else i, cls_)

    def open_wristband(self, i, cls_=Wristband, hud_scale=None, position=None):
        """Opens the wristband (or the helmet HUD) of crew member i.
        hud_scale/position: helmets only (several helmets at once, at a reduced size)."""
        for wb in list(self.wristbands):
            if wb.owner == i and type(wb) is cls_:
                if hud_scale is None or getattr(wb, "hud_scale", 1.0) == hud_scale:
                    if position is not None:
                        wb.geometry(f"+{position[0]}+{position[1]}")
                    wb.lift()
                    return wb
                wb.close_window()               # same helmet at another size: reopened
        extra = {"hud_scale": hud_scale} if hud_scale is not None else {}
        wb = cls_(self, self.mission, i, db_=self.db, on_close=self._on_device_closed,
                   routines=self.routines, **extra)
        if position is None:
            position = (60 + 40 * len(self.wristbands), 100 + 40 * len(self.wristbands))
        wb.geometry(f"+{position[0]}+{position[1]}")
        self.wristbands.append(wb)
        self.mission.receivers.add(i)          # "received" only if there is an open device
        return wb

    def open_all_helmets(self):
        """Opens EACH crew member's helmet in its own window, tiled, to see the 5 points of view
        at the same time. Each helmet shows its own video, data and notices."""
        n = len(self.mission.crew)
        columns = 3 if n > 4 else 2
        rows_ = -(-n // columns)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        margin, bar = 8, 32                     # bar = approximate height of each window's title bar
        scale_ = min((screen_w - margin * (columns + 1)) / columns / Helmet.WIDTH,
                     (screen_h - 60 - (margin + bar) * rows_) / rows_ / Helmet.HEIGHT, 1.0)
        scale_ = round(max(0.3, scale_), 3)
        w, h = round(Helmet.WIDTH * scale_), round(Helmet.HEIGHT * scale_)
        for i in range(n):
            col, r = i % columns, i // columns
            x = margin + col * (w + margin)
            y = margin + r * (h + margin + bar)
            self.open_wristband(i, Helmet, hud_scale=scale_, position=(x, y))
        self._save(lambda: self.db.record_change("console", f"whole crew's helmets opened (scale {scale_})"))

    def _on_device_closed(self, wb):
        if wb in self.wristbands:
            self.wristbands.remove(wb)
        self._sync_receivers()

    def _watch(self):
        """Every second (even when paused): repeats unattended critical notices
        and saves new notices and events to the database."""
        if self.mission.check_repetitions():
            self.refresh()
        else:
            for wb in list(self.wristbands):
                wb.update_view()
        self._save(lambda: self.db.sync(self.mission, self.routines))
        self._watch_id = self.after(1000, self._watch)

    def close_day(self):
        self.routines.close_day()
        self.refresh()

    def quit_app(self):
        """Saves EVERYTHING pending before closing. If it fails, asks before losing data."""
        for ident in (self._watch_id, self._after, self._voice_id):
            if ident:
                try:
                    self.after_cancel(ident)
                except tk.TclError:
                    pass
        self.playing = False
        self._pending_advance = 0
        if self.mobile is not None:
            self.mobile.stop()
        try:
            self.db.close_window(self.mission, self.routines)
        except SaveError as e:
            if not messagebox.askyesno("AstroHealth", f"Could not save the latest data:\n{e}\n\n"
                                       "Close anyway? (unsaved data will be lost)"):
                self._watch_id = self.after(1000, self._watch)
                return
            self.db.conn.close()
        self.destroy()

    # ---------------- view: My health ----------------
    def _build_my_view(self, v):
        hero = tk.Frame(v, bg=BG)
        hero.pack(fill="x")
        txt = tk.Frame(hero, bg=BG)
        txt.pack(side="left", fill="x", expand=True)
        self.lbl_kicker = tk.Label(txt, bg=BG, fg=DIM, font=F_CAPS)
        self.lbl_kicker.pack(anchor="w")
        self.lbl_title = tk.Label(txt, bg=BG, fg=TEXT, font=F_TITLE)
        self.lbl_title.pack(anchor="w")
        sub_row = tk.Frame(txt, bg=BG)
        sub_row.pack(anchor="w")
        self.lbl_sub = tk.Label(sub_row, bg=BG, fg=DIM, font=F_NORMAL)
        self.lbl_sub.pack(side="left")
        self.btn_back = button(sub_row, "← Back to my data", self.back_to_me, source=F_SMALL, pady=3)

        st = tk.Frame(hero, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        st.pack(side="right", ipadx=14, ipady=8)
        tk.Label(st, text="DEMO STATE", bg=CARD, fg=DIM, font=F_CAPS).pack(anchor="w", padx=14, pady=(8, 2))
        row = tk.Frame(st, bg=CARD)
        row.pack(anchor="w", padx=14)
        self.lbl_st_icon = tk.Label(row, bg=CARD, font=(F, 18, "bold"), width=2)
        self.lbl_st_icon.pack(side="left")
        self.lbl_st_text = tk.Label(row, bg=CARD, font=(F, 14, "bold"))
        self.lbl_st_text.pack(side="left")
        self.lbl_clock = tk.Label(st, bg=CARD, fg=DIM, font=F_SMALL)
        self.lbl_clock.pack(anchor="w", padx=14)

        # indicator cards
        cards_row = tk.Frame(v, bg=BG)
        cards_row.pack(fill="x", pady=12)
        self.cards = {}
        for k, ind in enumerate(INDICATORS):
            c = tk.Frame(cards_row, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
            c.grid(row=0, column=k, sticky="nsew", padx=(0 if k == 0 else 8, 0))
            cards_row.columnconfigure(k, weight=1)
            tk.Label(c, text=ind.name_, bg=CARD, fg=DIM, font=F_SMALL).pack(anchor="w", padx=12, pady=(10, 0))
            fv = tk.Frame(c, bg=CARD)
            fv.pack(anchor="w", padx=12)
            val = tk.Label(fv, bg=CARD, fg=TEXT, font=F_VALUE)
            val.pack(side="left")
            tk.Label(fv, text=" " + ind.unit, bg=CARD, fg=DIM, font=F_NORMAL).pack(side="left", pady=(12, 0))
            ch = chip(c, OK)
            ch.pack(anchor="w", padx=12, pady=(2, 4))
            tk.Label(c, text=range_text(ind), bg=CARD, fg=DIM, font=F_SMALL).pack(anchor="w", padx=12, pady=(0, 10))
            make_clickable(c, lambda cl=ind.key: self.choose_metric(cl))
            self.cards[ind.key] = (c, val, ch)

        # graph
        g = tk.Frame(v, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        g.pack(fill="both", expand=True)
        top = tk.Frame(g, bg=CARD)
        top.pack(fill="x", padx=14, pady=(10, 0))
        left_ = tk.Frame(top, bg=CARD)
        left_.pack(side="left")
        tk.Label(left_, text="YOUR SIGNALS OVER TIME", bg=CARD, fg=DIM, font=F_CAPS).pack(anchor="w")
        self.lbl_graph = tk.Label(left_, bg=CARD, fg=TEXT, font=F_H2)
        self.lbl_graph.pack(anchor="w")
        tk.Label(left_, text="━ Simulated reading    ▇ Demo range    ╌ Demo critical limit",
                 bg=CARD, fg=DIM, font=F_SMALL).pack(anchor="w")
        self.lbl_graph_right = tk.Label(top, bg=CARD, fg=DIM, font=F_SMALL)
        self.lbl_graph_right.pack(side="right", anchor="n")
        self.cv = tk.Canvas(g, bg=CARD, highlightthickness=0, height=200)
        self.cv.pack(fill="both", expand=True, padx=10, pady=8)
        self.cv.bind("<Configure>", lambda e: self._draw_main())

        # next step
        sp = tk.Frame(v, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        sp.pack(fill="x", pady=(12, 0))
        tk.Label(sp, text="NEXT STEP", bg=CARD, fg=DIM, font=F_CAPS).pack(anchor="w", padx=14, pady=(10, 0))
        self.lbl_step = tk.Label(sp, bg=CARD, fg=TEXT, font=(F, 11, "bold"), wraplength=1050, justify="left")
        self.lbl_step.pack(anchor="w", padx=14)
        self.lbl_explain = tk.Label(sp, bg=CARD, fg=DIM, font=F_SMALL, wraplength=1050, justify="left")
        self.lbl_explain.pack(anchor="w", padx=14, pady=(2, 10))

    def choose_metric(self, key):
        self.metric = key
        self.refresh()

    def back_to_me(self):
        self.view_crew_member(self.mission.me)

    def view_crew_member(self, i):
        self.viewing = i
        self.combo.current(i)
        self.show("mi")
        self.refresh()

    def _draw_main(self):
        t = self.mission.crew[self.viewing]
        draw_graph(self.cv, t.history[self.metric][-(MIN_WINDOW + 1):], IND[self.metric])

    # ---------------- view: Crew ----------------
    def _build_crew_view(self, v):
        top = tk.Frame(v, bg=BG)
        top.pack(fill="x")
        left_ = tk.Frame(top, bg=BG)
        left_.pack(side="left")
        tk.Label(left_, text="MISSION OVERVIEW", bg=BG, fg=DIM, font=F_CAPS).pack(anchor="w")
        tk.Label(left_, text="Crew · 5 on board", bg=BG, fg=TEXT, font=F_TITLE).pack(anchor="w")
        tk.Label(left_, text="Tap a crew member to see their full data.",
                 bg=BG, fg=DIM, font=F_NORMAL).pack(anchor="w")
        self.summary_chips = {}
        fr = tk.Frame(top, bg=BG)
        fr.pack(side="right", anchor="s")
        for e in (OK, CAUTION, ALERT, NO_SIGNAL):
            c = chip(fr, e)
            c.configure(font=F_NORMAL, padx=10, pady=4)
            c.pack(side="left", padx=4)
            self.summary_chips[e] = c

        # team progress (no individual ranking)
        eq = tk.Frame(v, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        eq.pack(fill="x", pady=(10, 0))
        tk.Label(eq, text="TEAM", bg=CARD, fg=DIM, font=F_CAPS).pack(side="left", padx=(14, 8), pady=8)
        self.lbl_team = tk.Label(eq, bg=CARD, fg=TEXT, font=(F, 10, "bold"))
        self.lbl_team.pack(side="left")
        self.cv_goal = tk.Canvas(eq, bg=CARD, height=14, width=260, highlightthickness=0)
        self.cv_goal.pack(side="left", padx=12)
        self.lbl_goal = tk.Label(eq, bg=CARD, fg=DIM, font=F_SMALL)
        self.lbl_goal.pack(side="left")
        button(eq, "Close day ▸", self.close_day, source=F_SMALL, pady=3).pack(side="right", padx=10)

        body = tk.Frame(v, bg=BG)
        body.pack(fill="both", expand=True, pady=12)
        grid = tk.Frame(body, bg=BG)
        grid.pack(side="left", fill="both", expand=True)
        self.tcards = []
        for i, t in enumerate(self.mission.crew):
            c = tk.Frame(grid, bg=CARD, highlightthickness=2, highlightbackground=EDGE)
            c.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            grid.columnconfigure(i % 3, weight=1)
            grid.rowconfigure(i // 3, weight=1)

            hdr = tk.Frame(c, bg=CARD)
            hdr.pack(fill="x", padx=12, pady=(10, 4))
            av = tk.Label(hdr, text=t.initials, bg="#1E3E5C", fg="white", font=(F, 11, "bold"), width=3, height=1)
            av.pack(side="left", padx=(0, 10), ipady=6)
            nm = tk.Frame(hdr, bg=CARD)
            nm.pack(side="left")
            tk.Label(nm, text=t.name_ + ("  · YOU" if i == self.mission.me else ""), bg=CARD, fg=TEXT,
                     font=(F, 11, "bold")).pack(anchor="w")
            lbl_role = tk.Label(nm, bg=CARD, fg=DIM, font=F_SMALL)
            lbl_role.pack(anchor="w")
            ch = chip(c, OK)
            ch.pack(anchor="w", padx=12)

            vals = tk.Frame(c, bg=CARD)
            vals.pack(fill="x", padx=12, pady=6)
            cells = {}
            for k, ind in enumerate(INDICATORS):
                cel = tk.Frame(vals, bg=CARD)
                cel.grid(row=k // 4, column=k % 4, sticky="w", padx=(0, 14))
                short = {"hr": "HR", "spo2": "SpO₂", "temp": "Temp", "sbp": "SBP", "glu": "Glu",
                         "sleep": "Sleep h", "exercise": "Exer. min", "rad": "Rad. mSv"}[ind.key]
                tk.Label(cel, text=short, bg=CARD, fg=DIM, font=F_SMALL).pack(anchor="w")
                lv = tk.Label(cel, bg=CARD, font=(F, 12, "bold"))
                lv.pack(anchor="w")
                cells[ind.key] = lv
            tk.Label(c, text="Heart rate · last 20 min", bg=CARD, fg=DIM,
                     font=F_SMALL).pack(anchor="w", padx=12)
            sp = tk.Canvas(c, bg=CARD, height=44, highlightthickness=0)
            sp.pack(fill="x", padx=12, pady=(0, 10))
            sp.bind("<Configure>", lambda e, idx=i: self._draw_spark(idx))
            make_clickable(c, lambda idx=i: self.view_crew_member(idx))
            self.tcards.append({"frame": c, "av": av, "role": lbl_role, "chip": ch,
                                "cells": cells, "spark": sp})

        # event log (in the 6th cell of the grid)
        rec = tk.Frame(grid, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
        rec.grid(row=1, column=2, sticky="nsew", padx=6, pady=6)
        tk.Label(rec, text="EVENT LOG", bg=CARD, fg=DIM, font=F_CAPS).pack(anchor="w", padx=12, pady=(10, 0))
        self.lbl_response = tk.Label(rec, bg=CARD, fg=COLOR[ALERT][0], font=(F, 9, "bold"), anchor="w")
        self.lbl_response.pack(anchor="w", padx=12, pady=(0, 4))
        self.txt_events = tk.Text(rec, bg=CARD, fg=TEXT, insertbackground=TEXT, font=F_SMALL, relief="flat",
                                   height=8, wrap="word", highlightthickness=0)
        self.txt_events.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        for e in COLOR:
            self.txt_events.tag_configure(e, foreground=COLOR[e][0])
        self.txt_events.tag_configure("broadcast", foreground="white", background=COLOR[ALERT][0],
                                       font=(F, 8, "bold"))
        self.txt_events.configure(state="disabled")

    def _draw_spark(self, i):
        t = self.mission.crew[i]
        draw_graph(self.tcards[i]["spark"], t.history["hr"][-(MIN_WINDOW + 1):],
                        IND["hr"], complete=False)

    # ---------------- navigation ----------------
    def show(self, view_name):
        self.view_name = view_name
        self.my_view.pack_forget()
        self.crew_view.pack_forget()
        (self.my_view if view_name == "mi" else self.crew_view).pack(fill="both", expand=True)
        for key, b in self.nav.items():
            b.configure(bg="#1E3E5C" if key == view_name else DARK,
                        fg="white" if key == view_name else "#CFE3F5")
        self.after_idle(self.refresh)

    # ---------------- general refresh ----------------
    def refresh(self):
        m = self.mission

        # segmented control: highlights the target's scenario
        i = self.target_index()
        if i == -1:
            scens = {t.scenario for t in m.crew}
            running = scens.pop() if len(scens) == 1 else None
        else:
            running = m.crew[i].scenario
        for name_, b in self.buttons_secs.items():
            if name_ == running:
                b.configure(bg=ACCENT, fg="#06121F", font=(F, 10, "bold"))
            else:
                b.configure(bg=SEG_BG, fg=DIM, font=F_NORMAL)

        if self.view_name == "mi":
            self._refresh_mine()
        else:
            self._refresh_crew()

        mode = m.mode
        self.lbl_mode.configure(text=mode)
        self.lbl_mode_note.configure(
            text=("  Synthetic data · " if mode == MODE_SIMULATION else "  Origin shown per measurement · ")
            + "Demo thresholds · Do not use for real medical decisions")
        sent = self.db.last_sent or "--:--"
        self.lbl_db.configure(text=f"▣ Database: {self.db.session_readings} readings received · last sent {sent}")
        for wb in list(self.wristbands):
            wb.update_view()

    def _refresh_mine(self):
        m = self.mission
        t = m.crew[self.viewing]
        first_ = t.name_.split()[0]
        if self.viewing == m.me:
            self.lbl_kicker.configure(text="YOUR HEALTH COMPANION ON BOARD")
            self.lbl_title.configure(text="Your health, at a glance.")
            self.lbl_sub.configure(text=f"Hello, {first_}. Understand your signals and know the next step.")
            self.btn_back.pack_forget()
        else:
            self.lbl_kicker.configure(text=f"CREW MEMBER DATA · {t.role.upper()}")
            self.lbl_title.configure(text=f"{first_}'s health, at a glance.")
            self.lbl_sub.configure(text=f"You are viewing the data of {t.name_}. Scenario: {t.scenario}.   ")
            self.btn_back.pack(side="left")

        eg = t.overall_state()
        fg, _ = COLOR[eg]
        self.lbl_st_icon.configure(text=ICON[eg], fg=fg)
        self.lbl_st_text.configure(text=STATE_TEXT[eg], fg=fg)
        self.lbl_clock.configure(text=f"{m.clock()} UTC · simulated clock · min {m.minutes_}")

        for key, (c, val, ch) in self.cards.items():
            e = t.indicator_state(key)
            val.configure(text=format_value(key, t.values_[key]),
                          fg=TEXT if e in (OK, NO_SIGNAL) else COLOR[e][0])
            paint_chip(ch, e)
            c.configure(highlightbackground=ACCENT if key == self.metric else EDGE,
                        highlightthickness=2 if key == self.metric else 1)

        ind = IND[self.metric]
        self.lbl_graph.configure(text=f"{ind.name_} · {ind.system}")
        self.lbl_graph_right.configure(text=f"Last {MIN_WINDOW} min · {ind.unit}")
        self._draw_main()

        self.lbl_step.configure(text=NEXT_STEP[eg], fg=COLOR[eg][0] if eg != OK else TEXT)
        bad_ones = [IND[k].name_ for k in IND if t.indicator_state(k) in (CAUTION, ALERT)]
        extra = f"Out of range: {', '.join(bad_ones)}.  " if bad_ones else ""
        self.lbl_explain.configure(text=f"{extra}Why {ind.name_.lower()} matters: {ind.explanation}")

    def _refresh_crew(self):
        m = self.mission
        tally = m.summary()
        for e, c in self.summary_chips.items():
            paint_chip(c, e, f"{tally[e]}  {STATE_TEXT[e]}")
        for i, t in enumerate(m.crew):
            tc = self.tcards[i]
            e = t.overall_state()
            tc["frame"].configure(highlightbackground=COLOR[e][0] if e in (CAUTION, ALERT) else EDGE)
            tc["av"].configure(bg=COLOR[e][0] if e != OK else "#1E3E5C")
            tc["role"].configure(text=f"{t.role} · scenario: {t.scenario} · life support: "
                                     f"{STATE_TEXT[t.support_state()].lower()}")
            paint_chip(tc["chip"], e)
            for key, lv in tc["cells"].items():
                ei = t.indicator_state(key)
                lv.configure(text=format_value(key, t.values_[key]),
                             fg=TEXT if ei == OK else COLOR[ei][0])
            self._draw_spark(i)

        # team: total points and collective goal
        r = self.routines
        progress_value = r.team_progress()
        done_list = sum(len(p.done_today) for p in r.progress)
        total = len(ROUTINES) * len(r.progress)
        self.lbl_team.configure(text=f"{r.team_points()} crew pts  ·  day {r.day}")
        cvm = self.cv_goal
        cvm.delete("all")
        col = COLOR[OK][0] if r.goal_met() else "#5FD3F3"
        cvm.create_rectangle(1, 2, 259, 12, outline=EDGE)
        cvm.create_rectangle(1, 2, 1 + 258 * progress_value, 12, fill=col, outline="")
        mx = 1 + 258 * TEAM_GOAL
        cvm.create_line(mx, 0, mx, 14, fill=COLOR[CAUTION][0], width=2)
        self.lbl_goal.configure(text=f"Team goal: {int(TEAM_GOAL * 100)} % of routines today · "
                                     f"{done_list}/{total} ({int(progress_value * 100)} %)"
                                     + ("  ✓ met!" if r.goal_met() else ""))

        # time of the first response to the latest critical notice
        with_resp = [d for d in m.broadcasts if d.response_secs is not None]
        open_ones = m.broadcast_queue()
        if with_resp:
            d = with_resp[-1]
            self.lbl_response.configure(
                text=f"◆ Latest notice ({m.crew[d.affected].name_.split()[0]}, {d.indicator_name()}): "
                     f"first response from {m.crew[d.who_responded].name_.split()[0]} "
                     f"in {d.response_secs:.0f} s")
        elif open_ones:
            self.lbl_response.configure(text=f"◆ {len(open_ones)} critical notice(s) waiting for a response")
        else:
            self.lbl_response.configure(text="")

        self.txt_events.configure(state="normal")
        self.txt_events.delete("1.0", "end")
        if not m.events:
            self.txt_events.insert("end", "No events yet. Choose a scenario and press Play.")
        for ev in reversed(m.events):
            self.txt_events.insert("end", f"{ev.clock}  ", ())
            if ev.text.startswith(BROADCAST_PREFIX):
                self.txt_events.insert("end", f" {ev.name_} {ev.text} \n", ("broadcast",))
            else:
                self.txt_events.insert("end", f"{ICON[ev.status]} {ev.name_} {ev.text}\n", (ev.status,))
        self.txt_events.configure(state="disabled")

    # ---------------- export ----------------
    def export(self):
        """Exports the COMPLETE session from SQLite (not from memory), atomically."""
        path_ = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="astrohealth_session.csv",
            filetypes=[("CSV", "*.csv")], title="Export session")
        if not path_:
            return
        if not self._save(lambda: self.db.sync(self.mission, self.routines)):
            return
        try:
            rows = self.db.export_session(path_)
        except (OSError, SaveError) as e:
            messagebox.showerror("AstroHealth", f"Could not export (no half-written file was left behind):\n{e}")
            return
        text = "\n".join(f"{r}  ({n} rows)" for r, n in rows.items())
        messagebox.showinfo("AstroHealth", f"Session exported:\n{text}")

if __name__ == "__main__":
    AstroHealth().mainloop()
