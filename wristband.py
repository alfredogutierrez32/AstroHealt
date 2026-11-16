"""
AstroHealth - Outdoor wristband (wrist display for each astronaut).

Designed to be used outside the ship, with gloves: only the essentials, large text,
touch areas of at least 44 px and scrolling (mouse wheel or ▲ ▼ buttons).

- FIXED (do not scroll): header (name, link with the ship, ◀ ⌂ ME ▶), tabs,
  discreet notification at the bottom and the EMERGENCY notice with blinking.
- HEALTH tab: overall traffic light, cards for HR, SpO₂, temperature, pressure, glucose and
  radiation, an action line (only if something is not green) and the crew row.
- BODY tab: silhouette with markers and animated ECG.
- ROUTINES tab: sleep, exercise, daily checklist, points, level, streak and team goal.
- Red alert or no signal for ANY crew member (including sleep and exercise, even though they
  do not appear in HEALTH) -> every wristband shows the notice: in 2nd person to the affected
  person (their screen BLINKS red) and in 3rd person to crewmates (a sound plays, no blinking),
  with ACKNOWLEDGED / VIEW THEIR DATA / I'M ON MY WAY. See actions.py for the full cycle.
- FIXED strip of active alarms (visible on every tab and even while scrolling).
- The pulse curve is an ILLUSTRATIVE ANIMATION, not a measured ECG.

Synthetic data · Illustrative rules · Do not use for real medical decisions.
"""
import math
import time
import tkinter as tk

from simulation import (
    INDICATORS, IND, OK, CAUTION, ALERT, NO_SIGNAL, INVALID, STATE_ORDER, ALERT_PRIORITY,
    SIGNAL, QUALITY_TEXT, Q_NO_DATA, PHASE_CRITICAL, SILENCE_S, format_value, protocol_for,
)
import actions
from routines import ROUTINES, TEAM_GOAL

W, H = 600, 720                 # initial size (the window can be resized)
FONT_FAMILY = "Bahnschrift"          # if it does not exist, Tk uses the default font

BG = "#06121F"
PANEL = "#0B1E33"
LINE = "#1E4466"
DIM = "#7FA3C0"
TEXT = "#E4F2FC"
CYAN = "#5FD3F3"
ORANGE = "#FF7A2F"
YELLOW = "#F2C230"
RED = "#FF3B3B"
GREEN = "#3DDC97"
GRAY = "#6C8299"
FLASH = "#5A0B0E"

STATE_COLOR = {OK: GREEN, CAUTION: YELLOW, ALERT: RED, NO_SIGNAL: GRAY, INVALID: GRAY}
WORD = {OK: "IN RANGE", CAUTION: "CAUTION", ALERT: "ALERT", NO_SIGNAL: "NO SIGNAL", INVALID: "INVALID"}
BIG_WORD = {OK: "IN RANGE", CAUTION: "CAUTION", ALERT: "RED ALERT", NO_SIGNAL: "NO SIGNAL",
                  INVALID: "INVALID DATA"}

BLINK_S = 3.0        # duration of the emergency blinking
TOAST_MS = 5000         # duration of the discreet notification
TOUCH = 44             # minimum size of a touch area (px)
HEADER_HEIGHT = 116
FOOTER_HEIGHT = 50
ALARMS_HEIGHT = 50

TITLES = {"hr": "PULSE", "spo2": "OXYGEN SpO₂", "temp": "TEMPERATURE", "sbp": "PRESSURE",
           "glu": "GLUCOSE", "sleep": "SLEEP (24 h)", "exercise": "EXERCISE TODAY", "rad": "RADIATION"}
HEALTH_CARDS = ["hr", "spo2", "temp", "sbp", "glu", "rad"]
# short action for the action line (2nd person / to help a crewmate)
ALERT_ACTION = "stop the activity and tell the medical officer"


def f(sz, weight_="normal"):
    return (FONT_FAMILY, sz, weight_)


def gauss(t, mu, sigma):
    return math.exp(-((t - mu) ** 2) / (2 * sigma ** 2))


def beat(t):
    """Shape of a heartbeat (P wave, QRS and T) for t between 0 and 1."""
    return (0.12 * gauss(t, .15, .03) - 0.15 * gauss(t, .30, .008) + 1.0 * gauss(t, .33, .01)
            - 0.30 * gauss(t, .36, .01) + 0.30 * gauss(t, .55, .04))


from actions import action_line  # noqa: E402  (the logic lives without Tk for the phone)


class Wristband(tk.Toplevel):
    WIDTH, HEIGHT = W, H
    NAME = "Wristband"
    RESIZABLE = True

    def __init__(self, master, mission, owner, db_=None, on_close=None, routines=None):
        super().__init__(master)
        self.ship = master              # console that saves actions to the database
        self.mission = mission
        self.owner = owner
        self.db = db_
        self.routines = routines
        self.on_close = on_close
        t = mission.crew[owner]
        self.title(f"{self.NAME} · {t.name_}")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.resizable(self.RESIZABLE, self.RESIZABLE)
        self.configure(bg=BG)

        self.viewed = owner
        self.view_name = "health"            # "health", "body" or "routines"
        self.prev = {i: x.overall_state() for i, x in enumerate(mission.crew)}
        self.seen_items = {}                # broadcast id -> repetitions already seen
        self.attention_view = {}        # broadcast id -> who is attending it (already notified)
        self.n_resolutions = len(mission.resolutions)
        self.n_on_the_way = len(mission.on_the_way)
        self.n_exits = len(mission.critical_exits)
        self.force_id = None           # notice reopened by tapping the red strip
        self.toast = None
        self._toast_after = None
        self.toast_history = []      # everything notified (including what never got shown)
        self.flash_until = 0.0
        self.flash_on = False
        self._flashing = False
        self.phase = 0.0
        self._alive = True
        self._width = self.WIDTH

        self._build_ui()
        self._bind_events()
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.draw()
        self._animate()

    # ------------------------------------------------------------------ structure
    def _build_ui(self):
        """Fixed header, scrollable area (Canvas + inner Frame), ▲ ▼ buttons and fixed footer."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.cv_top = tk.Canvas(self, height=HEADER_HEIGHT, bg=BG, highlightthickness=0)
        self.cv_top.grid(row=0, column=0, columnspan=3, sticky="ew")

        self.zone = tk.Frame(self, bg=BG)                 # central zone (where the scroll goes)
        self.zone.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.zone.columnconfigure(0, weight=1)
        self.zone.rowconfigure(0, weight=1)
        self.scroll = tk.Canvas(self.zone, bg=BG, highlightthickness=0, yscrollincrement=20)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.bar = tk.Scrollbar(self.zone, orient="vertical", command=self.scroll.yview, width=8,
                                  bg=LINE, troughcolor=BG, activebackground=CYAN, relief="flat", bd=0)
        self.bar.grid(row=0, column=1, sticky="ns")
        self.scroll.configure(yscrollcommand=self.bar.set)
        self.inner = tk.Frame(self.scroll, bg=BG)       # scrollable inner Frame
        self._win = self.scroll.create_window((0, 0), window=self.inner, anchor="nw")
        self.cv = tk.Canvas(self.inner, bg=BG, highlightthickness=0, height=400)
        self.cv.pack(fill="x")
        # large ▲ ▼ buttons (used with gloves)
        buttons = tk.Frame(self.zone, bg=BG)
        buttons.grid(row=0, column=2, sticky="ns", padx=(4, 6))
        for text, step in (("▲", -1), ("▼", 1)):
            b = tk.Label(buttons, text=text, bg=PANEL, fg=CYAN, font=f(18, "bold"), width=2,
                         highlightthickness=1, highlightbackground=CYAN, cursor="hand2")
            b.pack(side="top" if step < 0 else "bottom", pady=6, ipady=6)
            b.bind("<Button-1>", lambda e, p=step: self.scroll_by(p))
        # emergency notice: fixed above the scrollable area (never hidden)
        self.cv_alert = tk.Canvas(self.zone, bg="#16070A", highlightthickness=0)
        self._alert_visible = False

        # FIXED strip of active alarms: outside the scroll, visible on every tab
        self.cv_alarms = tk.Canvas(self, height=ALARMS_HEIGHT, bg=BG, highlightthickness=0)
        self.cv_alarms.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.cv_alarms.grid_remove()
        self._alarms_visible = False
        self.cv_footer = tk.Canvas(self, height=FOOTER_HEIGHT, bg=BG, highlightthickness=0)
        self.cv_footer.grid(row=3, column=0, columnspan=3, sticky="ew")

        # scrollregion and content width on resize
        self.inner.bind("<Configure>",
                           lambda e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>", self._on_resize)
        self.cv_top.bind("<Configure>", lambda e: self.draw())
        self.cv_alert.bind("<Configure>", lambda e: self._alert_visible and self.draw())
        # mouse wheel only with the pointer over the scrollable area
        self.zone.bind("<Enter>", lambda e: self._wheel(True))
        self.zone.bind("<Leave>", lambda e: self._wheel(False))
        self._canvases = [self.cv_top, self.cv, self.cv_alert, self.cv_alarms, self.cv_footer]

    def _on_resize(self, e):
        self.scroll.itemconfigure(self._win, width=e.width)
        if abs(e.width - self._width) > 2:
            self._width = e.width
            self.draw()

    def _wheel(self, enable):
        if enable:
            self.bind_all("<MouseWheel>", self._with_wheel)          # Windows and macOS
            self.bind_all("<Button-4>", lambda e: self.scroll_by(-1, 3))   # Linux
            self.bind_all("<Button-5>", lambda e: self.scroll_by(1, 3))
        else:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

    def _with_wheel(self, e):
        if e.delta == 0:
            return
        steps = -int(e.delta / 120) if abs(e.delta) >= 120 else (-1 if e.delta > 0 else 1)  # Windows / macOS
        self.scroll_by(1 if steps > 0 else -1, max(1, abs(steps)) * 3)

    def scroll_by(self, address, units=None):
        if units is None:      # ▲ ▼ buttons: almost half a screen per tap
            units = max(3, int(self.scroll.winfo_height() * 0.45 / 20))
        self.scroll.yview_scroll(address * units, "units")

    def _bind_events(self):
        n = len(self.mission.crew)
        actions = {"btn_me": lambda: self.view(self.owner), "btn_prev": lambda: self.cycle(-1),
                    "btn_next": lambda: self.cycle(1), "btn_ok": self.button_ack,
                    "btn_view": self.button_view, "btn_go": self.button_go,
                    "tab_health": lambda: self.set_view("health"),
                    "tab_body": lambda: self.set_view("body"),
                    "tab_routines": lambda: self.set_view("routines"),
                    "strip": self.reopen_notice,
                    "silence": self.silence}
        for i in range(n):
            actions[f"tile{i}"] = (lambda k=i: self.view(k))
            actions[f"alarm{i}"] = (lambda k=i: self.reopen_notice(k))
        for r in ROUTINES:
            if r.key != "mood":
                actions[f"rout_{r.key}"] = (lambda c=r.key: self.complete_routine(c))
        for v in range(1, 6):
            actions[f"mood_{v}"] = (lambda k=v: self.complete_routine("mood", k))
        for cv in dict.fromkeys(self._canvases):
            for tag, fn in actions.items():
                cv.tag_bind(tag, "<Button-1>", lambda e, fn=fn: fn())
            cv.tag_bind("touch", "<Enter>", lambda e, c=cv: c.configure(cursor="hand2"))
            cv.tag_bind("touch", "<Leave>", lambda e, c=cv: c.configure(cursor=""))

    # ------------------------------------------------------------------ actions
    def view(self, i, sync=True):
        """Shows person i. The wristband and the helmet of the SAME owner change together
        (so when switching member on the wristband, the helmet switches to their video)."""
        self.viewed = i
        self.draw()
        if sync:
            for other in getattr(self.ship, "wristbands", []):
                if other is not self and other.owner == self.owner and other.viewed != i:
                    other.view(i, sync=False)

    def cycle(self, d):
        self.view((self.viewed + d) % len(self.mission.crew))

    def set_view(self, view_name):
        self.view_name = view_name
        if hasattr(self, "scroll"):
            self.scroll.yview_moveto(0)
        self.draw()

    def complete_routine(self, key, mood=None):
        if self.routines is None:
            return
        if key == "mood" and mood is None:
            return                       # mood is marked with the 1-5 buttons
        self.routines.complete_routine(self.owner, key, mood=mood)
        self._notify_change()

    def close_window(self):
        self._alive = False
        try:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close(self)
        self.destroy()

    def who(self, i):
        return "YOU" if i == self.owner else self.mission.crew[i].name_.split()[0]

    def _notify_change(self):
        """Saves the action (response + reward in one transaction) and refreshes everything."""
        if hasattr(self.ship, "record_action"):
            self.ship.record_action()
        elif hasattr(self.ship, "refresh"):
            self.ship.refresh()
        else:
            self.update_view()

    def silence(self):
        """🔇 SILENCE: stops the voice and pauses sound and blinking of the current red alarms
        (on every wristband, helmet and phone). The alarms stay open and visible."""
        if not self.mission.silence(self.owner):
            return
        self.flash_until = 0
        mins = int(SILENCE_S // 60)
        self.notify(f"🔇 Alarms silenced {mins} min · still active; a new alarm will still sound", CYAN)
        self._notify_change()

    # ------------------------------------------------------------------ emergency notices
    def current_notice(self):
        """Alarm shown in large format (or None). See actions.notice_for."""
        d = actions.notice_for(self.mission, self.owner, self.force_id)
        if d is None or (self.force_id is not None and d.id != self.force_id):
            self.force_id = None
        return d

    def _after_response(self):
        self.force_id = None
        self.flash_until = 0.0
        self._notify_change()

    def button_ack(self):
        d = self.current_notice()
        if d is not None:
            actions.respond(self.mission, self.routines, self.owner, d, "ack")
            self._after_response()

    def button_view(self):
        d = self.current_notice()
        if d is not None:
            actions.respond(self.mission, self.routines, self.owner, d, "view")
            self.view_name = "health"
            self.view(d.affected)
            self._after_response()

    def button_go(self):
        d = self.current_notice()
        if d is not None and d.affected != self.owner:
            actions.respond(self.mission, self.routines, self.owner, d, "go")
            self._after_response()

    def reopen_notice(self, affected=None):
        """Reopens that person's alarm in large format (or the first one in the queue)."""
        queue_ = self.mission.broadcast_queue()
        if affected is not None:
            queue_ = [d for d in queue_ if d.affected == affected]
        if queue_:
            self.force_id = queue_[0].id
            self.draw()

    # ------------------------------------------------------------------ notifications
    def update_view(self):
        """Called by the ship every simulated minute, every second (repetitions) or when something changes."""
        if not self._alive:
            return
        m = self.mission
        for d in m.broadcast_queue():
            seen = self.seen_items.get(d.id)
            pending_ = self.owner not in d.cycle_acks and d.attended_by is None
            if m.is_silenced(d):                                # SILENCED: no sound or blinking
                self.seen_items[d.id] = d.repetitions
                pending_ = False
            is_mine = d.affected == self.owner
            # red blinking is only for the person at risk; crewmates hear a sound
            if seen is None:                                   # new notice
                self.seen_items[d.id] = d.repetitions
                if is_mine:
                    self.blink()
                elif pending_:
                    self.bell()
            elif d.repetitions > seen:                         # repetition
                self.seen_items[d.id] = d.repetitions
                # the person in red keeps sound and blinking even if someone is already on the way;
                # for the others it only sounds while nobody is attending
                if is_mine and self.owner not in d.cycle_acks and not m.is_silenced(d):
                    self.blink()
                elif pending_:
                    self.bell()
        # "X is on the way to help Y" (visual notification, no sound)
        for ow in m.on_the_way[self.n_on_the_way:]:
            if ow.helper == self.owner:
                text = f"You are on your way to help {self.who(ow.affected)}"
            elif ow.affected == self.owner:
                text = f"{self.who(ow.helper)} is on the way to help you"
            else:
                text = f"{self.who(ow.helper)} is on the way to help {self.who(ow.affected)}"
            self.notify(f"🏃  {text}", CYAN)
        self.n_on_the_way = len(m.on_the_way)
        # resolutions: "Mei back within range"
        for d in m.resolutions[self.n_resolutions:]:
            if d.affected == self.owner:
                self.notify(f"✓  You are back within range ({d.indicator_name()})", GREEN)
            else:
                self.notify(f"✓  {self.who(d.affected)} back within range ({d.indicator_name()})", GREEN)
        self.n_resolutions = len(m.resolutions)
        # left red but still yellow: this is NOT recovery
        for d in m.critical_exits[self.n_exits:]:
            who = "You" if d.affected == self.owner else f"{self.who(d.affected)}"
            self.notify(f"!  {who} left critical state ({d.indicator_name()}) · still in caution", YELLOW)
        self.n_exits = len(m.critical_exits)
        # caution: discreet notification only (not broadcast)
        for i, t in enumerate(m.crew):
            e, p = t.overall_state(), self.prev[i]
            if e != p:
                if e == CAUTION and p == OK:
                    bad_one = next((k for k in IND if t.indicator_state(k) == CAUTION), None)
                    detail = (f"{IND[bad_one].name_} {format_value(bad_one, t.values_[bad_one])} {IND[bad_one].unit}"
                               if bad_one else "values out of range")
                    self.notify(f"!  {self.who(i)}: {detail} · caution", YELLOW)
                elif e == OK and p == CAUTION:
                    self.notify(f"✓  {self.who(i)} back within range", GREEN)
                self.prev[i] = e
        # routine achievements
        if self.routines and self.owner in self.routines.last_achievement:
            self.notify(f"★  {self.routines.last_achievement.pop(self.owner)}", GREEN)
        self.draw()

    def notify(self, text, color):
        """Discreet notification. A current (amber/red) warning is NOT covered by an informative one."""
        self.toast_history.append((text, color))
        is_warning = color not in (GREEN, CYAN)
        if self.toast is not None and self.toast[1] not in (GREEN, CYAN) and not is_warning:
            return
        self.toast = (text, color)
        if self._toast_after:
            self.after_cancel(self._toast_after)
        self._toast_after = self.after(TOAST_MS, self._remove_toast)

    def _remove_toast(self):
        self.toast = None
        self._toast_after = None
        if self._alive:
            self.draw()

    def blink(self):
        self.flash_until = time.monotonic() + BLINK_S
        self.bell()
        if not self._flashing:
            self._flashing = True
            self._flash_step()

    def _flash_step(self):
        if not self._alive:
            return
        if time.monotonic() < self.flash_until:
            self.flash_on = not self.flash_on
            self.draw()
            self.after(250, self._flash_step)
        else:
            self.flash_on = False
            self._flashing = False
            self.draw()

    # ------------------------------------------------------------------ drawing helpers
    def box(self, x0, y0, x1, y1, title_=None, edge=LINE, fill_=PANEL, width_px=1, tags=(), cv=None):
        cv = cv or self.cv
        cv.create_rectangle(x0, y0, x1, y1, fill=fill_, outline=edge, width=width_px, tags=tags)
        if title_:
            cv.create_text(x0 + 10, y0 + 12, text=title_, anchor="w", fill=DIM,
                           font=f(9, "bold"), tags=tags)

    def touch_button(self, cv, x0, y0, x1, y1, text, tag, edge=CYAN, fill_="#123250", color=TEXT, sz=11):
        """Button with a minimum touch area of 44 px."""
        if y1 - y0 < TOUCH:
            y1 = y0 + TOUCH
        if x1 - x0 < TOUCH:
            x1 = x0 + TOUCH
        tags = (tag, "touch")
        cv.create_rectangle(x0, y0, x1, y1, fill=fill_, outline=edge, width=2, tags=tags)
        cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=text, fill=color, font=f(sz, "bold"), tags=tags)

    def _mini_trend(self, values_, key, x0, y0, x1, y1, color):
        data_ = [v for v in values_ if v is not None]
        if len(data_) < 2:
            return
        lo, hi = min(data_), max(data_)
        range_ = (hi - lo) or 1
        n = len(values_)
        for segment in actions.segments(values_):          # cut where data is missing
            pts = []
            for i, v in segment:
                pts += [x0 + i / max(n - 1, 1) * (x1 - x0), y1 - (v - lo) / range_ * (y1 - y0)]
            if len(pts) >= 4:
                self.cv.create_line(*pts, fill=color, width=1.5)
            elif len(pts) == 2:
                self.cv.create_oval(pts[0] - 1.5, pts[1] - 1.5, pts[0] + 1.5, pts[1] + 1.5, fill=color, outline="")

    # ------------------------------------------------------------------ main drawing
    def draw(self):
        if not self._alive:
            return
        background = FLASH if self.flash_on else BG
        for c in (self.cv_top, self.cv, self.cv_footer, self.cv_alarms, self.scroll, self.inner, self.zone):
            c.configure(bg=background)
        t = self.mission.crew[self.viewed]
        self._header_bar(t)

        cv = self.cv
        cv.delete("all")
        cw = max(self._width, 300)
        if self.view_name == "health":
            height_px = self._health_view(t, cw)
        elif self.view_name == "body":
            height_px = self._body_view(t, cw)
        else:
            height_px = self._routines_view(cw)
        cv.configure(height=height_px, width=cw)
        self._alarm_strip()
        self._footer()

        d = self.current_notice()
        if d is not None:
            if not self._alert_visible:
                self.cv_alert.place(in_=self.zone, x=0, y=0, relwidth=1, relheight=1)
                tk.Misc.tkraise(self.cv_alert)      # (Canvas.lift is for canvas items, not for the widget)
                self._alert_visible = True
            self._emergency(d)
        elif self._alert_visible:
            self.cv_alert.place_forget()
            self._alert_visible = False
        self._draw_ecg()

    def _header_bar(self, t):
        cv = self.cv_top
        cv.delete("all")
        w = max(cv.winfo_width(), 300)
        # row 1: name, link and navigation
        self.box(8, 6, w - 8, 62, cv=cv)
        cv.create_text(18, 24, text=f"EV{self.viewed + 1}", anchor="w", fill=DIM, font=f(12))
        parts = t.name_.split()
        cv.create_text(56, 24, text=f"{parts[-1].upper()}, {parts[0][0]}.", anchor="w",
                       fill=TEXT, font=f(17, "bold"))
        no_link = self.mission.crew[self.owner].overall_state() == NO_SIGNAL
        if no_link:
            link, col = "⇄ SHIP · NO LINK", RED
        else:
            clock = self.db.last_sent if self.db and self.db.last_sent else self.mission.clock()
            link, col = f"⇄ SHIP · SENT {clock}", GREEN
        who = "YOU" if self.viewed == self.owner else f"CREWMATE · {t.role.upper()}"
        cv.create_text(18, 47, text=who, anchor="w",
                       fill=CYAN if self.viewed == self.owner else YELLOW, font=f(8, "bold"))
        cv.create_text(18 + 9 * len(who) + 14, 47, text=link, anchor="w", fill=col, font=f(9, "bold"))
        x = w - 14
        for tag, text, width_px in (("btn_next", "▶", TOUCH), ("btn_me", "⌂ ME", 64), ("btn_prev", "◀", TOUCH)):
            self.touch_button(cv, x - width_px, 12, x, 56, text, tag,
                              edge=CYAN if tag == "btn_me" else LINE, fill_=PANEL, sz=12)
            x -= width_px + 6
        # row 2: tabs
        tabs = (("tab_health", "♥  HEALTH", "health"), ("tab_body", "◉  BODY", "body"),
                    ("tab_routines", "★  ROUTINES", "routines"))
        width_px = (w - 16 - 2 * 6) / 3
        for k, (tag, text, view_name) in enumerate(tabs):
            bx = 8 + k * (width_px + 6)
            enabled = self.view_name == view_name
            self.touch_button(cv, bx, 68, bx + width_px, 68 + TOUCH, text, tag,
                              edge=CYAN if enabled else LINE, fill_="#123250" if enabled else PANEL,
                              color=TEXT if enabled else DIM, sz=11)

    # ---------------- HEALTH ----------------
    def _health_view(self, t, cw):
        cv = self.cv
        is_you = self.viewed == self.owner
        x0, x1 = 10, cw - 10
        y = 10
        # a) large overall traffic light
        eg = t.overall_state()
        col = STATE_COLOR[eg]
        self.box(x0, y, x1, y + 92, edge=col, width_px=2)
        cv.create_oval(x0 + 18, y + 16, x0 + 78, y + 76, fill=col, outline="")
        cv.create_text(x0 + 48, y + 46, text={OK: "✓", CAUTION: "!", ALERT: "▲", NO_SIGNAL: "–"}[eg],
                       fill=BG, font=f(24, "bold"))
        cv.create_text(x0 + 96, y + 36, text=BIG_WORD[eg], anchor="w", fill=col, font=f(24, "bold"))
        cv.create_text(x0 + 96, y + 66, anchor="w", fill=DIM, font=f(10, "bold"),
                       text="OVERALL STATE · YOU" if is_you else f"OVERALL STATE · {t.name_.split()[0].upper()}")
        y += 104
        # b) compact cards
        cols = 3 if cw >= 540 else 2              # 3 and 3 (2 per row on a narrow screen)
        gap = 8
        width_px = (x1 - x0 - gap * (cols - 1)) / cols
        height_px = 112
        for k, key in enumerate(HEALTH_CARDS):
            row, column_ = divmod(k, cols)
            bx = x0 + column_ * (width_px + gap)
            by = y + row * (height_px + gap)
            self._card(t, key, bx, by, bx + width_px, by + height_px)
        rows = math.ceil(len(HEALTH_CARDS) / cols)
        y += rows * (height_px + gap) + 4
        # c) one action line, only if something is not green
        text, e = action_line(t, is_you)
        if text:
            item = cv.create_text(x0 + 46, y + 14, text=text, anchor="nw", fill=TEXT,
                                  font=f(13, "bold"), width=x1 - x0 - 60)
            bb = cv.bbox(item)
            line_height = max(52, (bb[3] - bb[1]) + 28 if bb else 52)
            background = cv.create_rectangle(x0, y, x1, y + line_height, fill="#1A1508" if e == CAUTION else
                                        ("#2A0A0D" if e == ALERT else PANEL), outline=STATE_COLOR[e], width=2)
            cv.tag_lower(background)
            cy = y + line_height / 2
            cv.create_oval(x0 + 10, cy - 13, x0 + 36, cy + 13, fill=STATE_COLOR[e], outline="")
            cv.create_text(x0 + 23, cy, text="!", fill=BG, font=f(14, "bold"))
            y += line_height + 12
        # d) compact crew
        y = self._crew_row(x0, y, x1)
        return y + 12

    def _card(self, t, key, x0, y0, x1, y1):
        """Compact card: title, large value, unit/range, state chip and mini trend."""
        cv = self.cv
        ind = IND[key]
        e = t.indicator_state(key)
        col = STATE_COLOR[e]
        self.box(x0, y0, x1, y1, TITLES[key], edge=col if e != OK else LINE, width_px=2 if e != OK else 1)
        value = format_value(key, t.values_[key])
        cv.create_text(x0 + 10, y0 + 50, text=value, anchor="w", fill=col if e != OK else TEXT,
                       font=f(28, "bold"))
        lo, hi = ind.range_
        d = ind.decimals
        range_ = f"up to {hi:.0f}" if ind.cumulative else f"{lo:.{d}f}–{hi:.{d}f}"
        cv.create_text(x0 + 10, y0 + 78, text=f"{ind.unit} · {range_}", anchor="w", fill=DIM, font=f(9))
        # state chip
        word = WORD[e]
        cw = 14 + len(word) * 7
        cv.create_rectangle(x0 + 10, y1 - 22, x0 + 10 + cw, y1 - 6, fill=col, outline="")
        cv.create_text(x0 + 10 + cw / 2, y1 - 14, text=word, fill=BG, font=f(8, "bold"))
        # mini trend (last 20 min) inside the card
        if x1 - x0 > 150:
            self._mini_trend(t.history[key][-21:], key, x1 - 70, y1 - 26, x1 - 10, y1 - 8,
                                 col if e != OK else CYAN)

    def _crew_row(self, x0, y, x1):
        cv = self.cv
        cv.create_text(x0, y + 8, text="CREW · tap to view their data", anchor="w", fill=DIM,
                       font=f(9, "bold"))
        y += 20
        others = [i for i in range(len(self.mission.crew)) if i != self.viewed]
        gap = 8
        width_px = (x1 - x0 - gap * (len(others) - 1)) / len(others)
        height_px = 96
        for k, i in enumerate(others):
            t = self.mission.crew[i]
            e = t.overall_state()
            col = STATE_COLOR[e]
            bx = x0 + k * (width_px + gap)
            tags = (f"tile{i}", "touch")
            alarm = e in (ALERT, NO_SIGNAL)
            cv.create_rectangle(bx, y, bx + width_px, y + height_px,
                                fill="#3A0A0D" if alarm and self.flash_on else PANEL,
                                outline=col if e != OK else LINE, width=3 if alarm else (2 if e != OK else 1),
                                dash=(5, 3) if e == NO_SIGNAL else None, tags=tags)
            hr = format_value("hr", t.values_["hr"])
            ehr = t.indicator_state("hr")
            hr_col = STATE_COLOR[ehr] if ehr != OK else TEXT
            cy = y + height_px / 2
            if width_px > 120:      # wide: initials on the left, name and pulse on the right
                cv.create_oval(bx + 10, cy - 24, bx + 58, cy + 24, fill=col, outline="", tags=tags)
                cv.create_text(bx + 34, cy, text=t.initials, fill=BG, font=f(13, "bold"), tags=tags)
                cv.create_text(bx + 70, cy - 14, text=self.who(i), anchor="w", fill=TEXT,
                               font=f(12, "bold"), tags=tags)
                cv.create_text(bx + 70, cy + 14, text=f"♥ {hr}", anchor="w", fill=hr_col,
                               font=f(17, "bold"), tags=tags)
            else:                # narrow: initials on top and pulse below, centered
                cx = bx + width_px / 2
                cv.create_oval(cx - 20, y + 10, cx + 20, y + 50, fill=col, outline="", tags=tags)
                cv.create_text(cx, y + 30, text=t.initials, fill=BG, font=f(11, "bold"), tags=tags)
                cv.create_text(cx, y + 74, text=f"♥ {hr}", fill=hr_col, font=f(15, "bold"), tags=tags)
        return y + height_px

    # ---------------- BODY ----------------
    def _body_view(self, t, cw):
        x0, x1 = 10, cw - 10
        self._silhouette(t, x0, 10, x1, 290)
        self._ecg_panel(t, x0, 300, x1, 430)
        # below the ECG: oxygen, temperature, pressure and glucose (they fill the free space)
        keys_ = ["spo2", "temp", "sbp", "glu"]
        cols = 4 if cw >= 760 else 2
        gap = 8
        width_px = (x1 - x0 - gap * (cols - 1)) / cols
        height_px = 112
        y = 440
        for k, key in enumerate(keys_):
            row, column_ = divmod(k, cols)
            bx = x0 + column_ * (width_px + gap)
            by = y + row * (height_px + gap)
            self._card(t, key, bx, by, bx + width_px, by + height_px)
        rows = math.ceil(len(keys_) / cols)
        return y + rows * (height_px + gap) + 6

    def _silhouette(self, t, x0, y0, x1, y1):
        cv = self.cv
        self.box(x0, y0, x1, y1, "BODY MONITORING")
        cx = (x0 + x1) / 2
        sil = "#22415E"
        cv.create_oval(cx - 15, y0 + 26, cx + 15, y0 + 60, fill=sil, outline="#3A6488")
        cv.create_polygon(cx - 38, y0 + 70, cx + 38, y0 + 70, cx + 30, y0 + 154, cx - 30, y0 + 154,
                          fill=sil, outline="#3A6488")
        for side_ in (-1, 1):
            cv.create_line(cx + 38 * side_, y0 + 74, cx + 54 * side_, y0 + 144, cx + 58 * side_, y0 + 186,
                           fill=sil, width=14, capstyle="round", joinstyle="round")
            cv.create_line(cx + 14 * side_, y0 + 154, cx + 18 * side_, y0 + 214, cx + 20 * side_, y0 + 266,
                           fill=sil, width=18, capstyle="round", joinstyle="round")
        no_signal = t.overall_state() == NO_SIGNAL
        points = {"temp": (cx, y0 + 43, -1), "hr": (cx - 13, y0 + 92, -1), "spo2": (cx + 15, y0 + 92, 1),
                  "glu": (cx, y0 + 130, -1), "sbp": (cx + 54, y0 + 144, 1)}
        short = {"hr": "HEART", "spo2": "LUNGS", "temp": "TEMP", "sbp": "PRESSURE", "glu": "GLUCOSE"}
        for key, (mx, my, side_) in points.items():
            e = t.indicator_state(key)
            col = STATE_COLOR[e] if e != OK else CYAN
            separation = min((x1 - x0) / 2 - 14, 260)    # labels near the body on a wide screen
            lx = cx - separation if side_ < 0 else cx + separation
            wd = "w" if side_ < 0 else "e"
            cv.create_line(mx, my, lx + (70 * -side_), my, fill=LINE)
            cv.create_text(lx, my - 8, text=short[key], anchor=wd, fill=DIM, font=f(8, "bold"))
            cv.create_text(lx, my + 8, text=f"{format_value(key, t.values_[key])} {IND[key].unit}",
                           anchor=wd, fill=STATE_COLOR[e] if e != OK else TEXT, font=f(11, "bold"))
            if e in (CAUTION, ALERT):
                cv.create_oval(mx - 13, my - 13, mx + 13, my + 13, outline=col, width=2)
                if e == ALERT:
                    cv.create_oval(mx - 19, my - 19, mx + 19, my + 19, outline=col, width=1)
            s = 7
            cv.create_polygon(mx, my - s, mx + s, my, mx, my + s, mx - s, my,
                              fill=col if not no_signal else GRAY, outline="white", width=1)
        if no_signal:
            cv.create_rectangle(cx - 60, y0 + 120, cx + 60, y0 + 150, fill=BG, outline=RED)
            cv.create_text(cx, y0 + 135, text="NO SIGNAL", fill=RED, font=f(13, "bold"))

    def _ecg_panel(self, t, x0, y0, x1, y1):
        cv = self.cv
        e = t.indicator_state("hr")
        col = ORANGE if e == OK else STATE_COLOR[e]
        self.box(x0, y0, x1, y1, "HEART RATE · illustrative curve (not a measured ECG)")
        cv.create_text(x0 + 16, y0 + 62, text=format_value("hr", t.values_["hr"]), anchor="w", fill=col,
                       font=f(34, "bold"))
        cv.create_text(x0 + 16, y0 + 100, text=f"bpm · 50–100 · {WORD[e]}", anchor="w",
                       fill=STATE_COLOR[e] if e != OK else DIM, font=f(9, "bold"))
        self.ecg_box = (x0 + 150, y0 + 24, x1 - 12, y1 - 12)
        self.ecg_color = col
        self.ecg_hr = t.values_["hr"]
        bx0, by0, bx1, by1 = self.ecg_box
        for k in range(1, 4):
            yy = by0 + (by1 - by0) * k / 4
            cv.create_line(bx0, yy, bx1, yy, fill="#102A44")

    # ---------------- ROUTINES ----------------
    def _routines_view(self, cw):
        cv = self.cv
        x0, x1 = 10, cw - 10
        t = self.mission.crew[self.owner]
        y = 10
        # sleep and exercise (they still broadcast alerts even though they are not in HEALTH)
        gap = 8
        width_px = (x1 - x0 - gap) / 2
        for k, key in enumerate(("sleep", "exercise")):
            bx = x0 + k * (width_px + gap)
            self._card(t, key, bx, y, bx + width_px, y + 112)
        y += 124
        if self.routines is None:
            cv.create_text(cw / 2, y + 20, text="Routines not available", fill=DIM, font=f(12))
            return y + 50
        r = self.routines
        p = r.progress[self.owner]
        # points, level and streak
        self.box(x0, y, x1, y + 74)
        cv.create_text(x0 + 12, y + 20, anchor="w", fill=CYAN, font=f(14, "bold"),
                       text=f"LEVEL {p.level}  ·  {p.points} PTS  ·  STREAK {p.streak} DAYS")
        cv.create_rectangle(x0 + 12, y + 38, x1 - 12, y + 50, outline=LINE)
        cv.create_rectangle(x0 + 12, y + 38, x0 + 12 + (x1 - x0 - 24) * p.points_in_level / 100, y + 50,
                            fill=CYAN, outline="")
        cv.create_text(x1 - 12, y + 63, anchor="e", fill=DIM, font=f(9),
                       text=f"{100 - p.points_in_level} pts to level {p.level + 1} · day {r.day}")
        y += 84
        # checklist
        cv.create_text(x0, y + 8, anchor="w", fill=DIM, font=f(9, "bold"), text="YOUR ROUTINES TODAY")
        y += 20
        for rout in ROUTINES:
            done = rout.key in p.done_today
            is_mood = rout.key == "mood"
            height_px = 112 if (is_mood and not done) else 66
            self.box(x0, y, x1, y + height_px, edge=GREEN if done else LINE)
            cv.create_text(x0 + 22, y + 33, text="✓" if done else "○", fill=GREEN if done else DIM,
                           font=f(18, "bold"))
            cv.create_text(x0 + 44, y + 22, text=rout.name_, anchor="w", fill=TEXT, font=f(13, "bold"))
            cv.create_text(x0 + 44, y + 44, text=rout.description, anchor="w", fill=DIM, font=f(9),
                           width=x1 - x0 - 200)
            if done:
                extra = f" · mood {t.reported_mood}/5" if is_mood and t.reported_mood else ""
                cv.create_text(x1 - 12, y + 33, anchor="e", fill=GREEN, font=f(10, "bold"),
                               text=f"DONE {p.done_today[rout.key]}{extra}")
            elif is_mood:
                cv.create_text(x1 - 12, y + 22, anchor="e", fill=DIM, font=f(9, "bold"),
                               text=f"+{rout.points} pts")
                bw = min(64, (x1 - x0 - 44 - 4 * 8) / 5)
                for v in range(1, 6):
                    bx = x0 + 44 + (v - 1) * (bw + 8)
                    self.touch_button(cv, bx, y + 60, bx + bw, y + 60 + TOUCH, str(v), f"mood_{v}", sz=14)
            else:
                self.touch_button(cv, x1 - 150, y + 11, x1 - 12, y + 11 + TOUCH,
                                  f"✓ COMPLETE +{rout.points}", f"rout_{rout.key}", sz=11)
            y += height_px + 8
        # badges
        cv.create_text(x0, y + 10, anchor="w", text="BADGES", fill=DIM, font=f(9, "bold"))
        y += 24
        if p.badges:
            ix = x0
            for name_ in p.badges:
                width_px = 22 + len(name_) * 7.5
                if ix + width_px > x1:
                    ix = x0
                    y += 30
                cv.create_rectangle(ix, y, ix + width_px, y + 24, outline=YELLOW, fill="#2A2410")
                cv.create_text(ix + width_px / 2, y + 12, text=f"★ {name_}", fill=YELLOW, font=f(9, "bold"))
                ix += width_px + 8
            y += 34
        else:
            cv.create_text(x0, y + 8, anchor="w", fill=GRAY, font=f(9),
                           text="Complete routines and help your crewmates to earn them.")
            y += 26
        # team goal (no individual ranking)
        progress_value = r.team_progress()
        done_list = sum(len(x.done_today) for x in r.progress)
        total = len(ROUTINES) * len(r.progress)
        col = GREEN if r.goal_met() else CYAN
        cv.create_text(x0, y + 8, anchor="w", fill=DIM, font=f(9, "bold"),
                       text=f"TEAM GOAL · {int(TEAM_GOAL * 100)} % OF ROUTINES TODAY")
        cv.create_text(x0, y + 30, anchor="w", fill=col, font=f(11, "bold"),
                       text=f"{done_list}/{total} routines · {r.team_points()} team pts"
                            + ("  ✓ GOAL MET!" if r.goal_met() else ""))
        cv.create_rectangle(x0, y + 46, x1, y + 60, outline=LINE)
        cv.create_rectangle(x0, y + 46, x0 + (x1 - x0) * progress_value, y + 60, fill=col, outline="")
        mx = x0 + (x1 - x0) * TEAM_GOAL
        cv.create_line(mx, y + 40, mx, y + 66, fill=YELLOW, width=2)
        return y + 80

    # ---------------- fixed strip of active alarms ----------------
    def _alarm_strip(self):
        items = actions.alarm_summary(self.mission, self.owner)
        cv = self.cv_alarms
        if not items:
            if self._alarms_visible:
                cv.grid_remove()
                self._alarms_visible = False
            return
        if not self._alarms_visible:
            cv.grid()
            self._alarms_visible = True
        cv.delete("all")
        w = max(cv.winfo_width(), 300)
        cv.create_text(12, ALARMS_HEIGHT / 2, text="▲ ALARMS", anchor="w", fill=RED, font=f(10, "bold"))
        # SILENCE button (on the right): only if there are red alarms still sounding
        m = self.mission
        critical = [d for d in m.broadcast_queue() if d.phase == PHASE_CRITICAL]
        button = 150
        bx = w - button - 8
        if any(not m.is_silenced(d) for d in critical):
            cv.create_rectangle(bx, 4, w - 8, ALARMS_HEIGHT - 4, fill="#3A0A0D", outline=RED, width=2,
                                tags=("silence", "touch"))
            cv.create_text(bx + button / 2, ALARMS_HEIGHT / 2, text="🔇 SILENCE", fill="#FFE0E0",
                           font=f(10, "bold"), tags=("silence", "touch"))
        elif critical:
            left = int(m.silence_left())
            cv.create_rectangle(bx, 4, w - 8, ALARMS_HEIGHT - 4, fill=PANEL, outline=GRAY, width=1)
            cv.create_text(bx + button / 2, ALARMS_HEIGHT / 2, text=f"🔇 SILENCED {left // 60}:{left % 60:02d}",
                           fill=GRAY, font=f(9, "bold"))
        else:
            button = -8
        x = 100
        width_px = max(150, (w - x - 10 - button - 8) / len(items) - 8)
        for it in items:
            col = RED if it["critical"] else YELLOW
            tags = (f"alarm{it['affected']}", "touch")
            cv.create_rectangle(x, 4, x + width_px, ALARMS_HEIGHT - 4, fill="#3A0A0D" if it["critical"] else "#2A2108",
                                outline=col, width=2, tags=tags)
            cv.create_text(x + 8, ALARMS_HEIGHT / 2, text=it["text"], anchor="w", fill="#FFE0E0",
                           font=f(9, "bold"), width=width_px - 14, tags=tags)
            x += width_px + 8

    # ---------------- footer (discreet notification, fixed) ----------------
    def _footer(self):
        cv = self.cv_footer
        cv.delete("all")
        w = max(cv.winfo_width(), 300)
        if self.toast:
            text, color = self.toast
            cv.create_rectangle(8, 4, w - 8, FOOTER_HEIGHT - 6, fill=PANEL, outline=color)
            cv.create_text(20, FOOTER_HEIGHT / 2 - 1, text=text, anchor="w", fill=color, font=f(11, "bold"),
                           width=w - 40)
        else:
            cv.create_text(12, FOOTER_HEIGHT / 2, anchor="w", fill=GRAY, font=f(8), width=w - 24,
                           text=f"Mode: {self.mission.mode} · mission clock {self.mission.clock()} UTC · "
                                + ("synthetic demo data · " if self.mission.mode == "SIMULATION" else "")
                                + "demo rules, do not use for real medical decisions")

    # ---------------- emergency notice (fixed, above the scroll) ----------------
    def _emergency(self, d):
        cv = self.cv_alert
        cv.delete("all")
        cv.configure(bg=FLASH if self.flash_on else "#16070A")
        w = max(cv.winfo_width(), 300)
        h = max(cv.winfo_height(), 300)
        t = self.mission.crew[d.affected]
        is_you = d.affected == self.owner
        title_, problem, steps = protocol_for(t, is_you=is_you, key=d.key, value=d.value)
        if d.key != SIGNAL:   # privacy: only the critical indicator and its value WHEN TRIGGERED
            problem = problem.rstrip(".") + " (value when triggered)."
        x0, y0, x1, y1 = 10, 8, w - 10, h - 8
        cv.create_rectangle(x0, y0, x1, y1, fill="#16070A", outline=RED, width=4)
        header = (f"⚠  EMERGENCY · {t.name_.upper()} (YOU)" if is_you
                    else f"⚠  EMERGENCY · {t.name_.upper()} ({t.role.upper()})")
        y = y0 + 14
        it = cv.create_text(x0 + 16, y, text=header, anchor="nw", fill=RED, font=f(15, "bold"),
                            width=x1 - x0 - 32)
        y = cv.bbox(it)[3] + 8 if cv.bbox(it) else y + 30
        it = cv.create_text(x0 + 16, y, text=title_, anchor="nw", fill=TEXT, font=f(14, "bold"),
                            width=x1 - x0 - 32)
        y = cv.bbox(it)[3] + 4 if cv.bbox(it) else y + 26
        cv.create_text(x0 + 16, y, text=problem, anchor="nw", fill="#FFB0B0", font=f(12, "bold"),
                       width=x1 - x0 - 32)
        y += 26
        if d.attended_by is not None and is_you:
            cv.create_text(x0 + 16, y, anchor="nw", fill=CYAN, font=f(11, "bold"),
                           text=f"{self.who(d.attended_by)} is on the way to help you")
            y += 22
        cv.create_text(x0 + 16, y + 6, text="WHAT TO DO:" if is_you else "HOW TO HELP:", anchor="nw",
                       fill=DIM, font=f(9, "bold"))
        y += 28
        # buttons (at the bottom, always visible; on narrow screens they wrap into two rows)
        if is_you:
            buttons = [("btn_ok", "ACKNOWLEDGED", RED, RED, "white")]
        else:
            buttons = [("btn_go", "I'M ON MY WAY", GREEN, "#0F3324", GREEN),
                       ("btn_view", "VIEW THEIR DATA", CYAN, PANEL, CYAN),
                       ("btn_ok", "ACKNOWLEDGED", RED, RED, "white")]
        widths = [24 + len(b[1]) * 10 for b in buttons]
        rows = [buttons] if sum(widths) + 8 * len(buttons) <= x1 - x0 - 24 else [buttons[:1], buttons[1:]]
        buttons_height = len(rows) * (TOUCH + 8)
        yb = y1 - 14 - buttons_height
        queue_ = self.mission.broadcast_queue()
        same = [x for x in queue_ if x.affected == d.affected and x.id != d.id]
        others = len({x.affected for x in queue_ if x.affected != d.affected})
        note = []
        if same:
            note.append("Also critical: " + ", ".join(x.indicator_name().split(" (")[0] for x in same))
        if others:
            note.append(f"+{others} more crew member(s) in queue (by severity)")
        steps_space = yb - y - (24 if note else 6)
        step_height = max(30, min(44, steps_space / max(len(steps), 1)))
        for k, step in enumerate(steps):
            yy = y + k * step_height + step_height / 2
            cv.create_oval(x0 + 16, yy - 12, x0 + 40, yy + 12, outline=RED, width=2)
            cv.create_text(x0 + 28, yy, text=str(k + 1), fill=RED, font=f(10, "bold"))
            cv.create_text(x0 + 52, yy, text=step, anchor="w", fill=TEXT, font=f(11), width=x1 - x0 - 72)
        if note:
            cv.create_text(x0 + 16, yb - 16, text="  ·  ".join(note), anchor="w", fill=YELLOW,
                           font=f(9, "bold"), width=x1 - x0 - 32)
        for row in rows:
            x = x1 - 14
            for tag, text, edge, fill_, color in reversed(row):
                width_px = 24 + len(text) * 10
                self.touch_button(cv, x - width_px, yb, x, yb + TOUCH, text, tag, edge=edge,
                                  fill_=fill_, color=color, sz=11)
                x -= width_px + 8
            yb += TOUCH + 8

    # ------------------------------------------------------------------ animated ECG
    def _animate(self):
        if not self._alive:
            return
        self.phase += 0.06
        self._draw_ecg()
        self.after(60, self._animate)

    def _draw_ecg(self):
        cv = self.cv
        cv.delete("ecg")
        if not hasattr(self, "ecg_box"):
            return
        if type(self) is Wristband and (self.view_name != "body" or self.current_notice() is not None):
            return
        x0, y0, x1, y1 = self.ecg_box
        middle = (y0 + y1) / 2 + 12
        amp = (y1 - y0) * 0.42
        if self.ecg_hr is None:
            cv.create_line(x0, middle, x1, middle, fill=GRAY, width=2, tags="ecg")
            cv.create_text((x0 + x1) / 2, y0 + 16, text="NO SIGNAL", fill=GRAY, font=f(10, "bold"), tags="ecg")
            return
        window_ = 3.0                  # visible seconds
        n = 140
        lps = self.ecg_hr / 60.0       # beats per second
        pts = []
        for k in range(n):
            secs = self.phase + k / n * window_
            beat_phase = (secs * lps) % 1.0
            pts += [x0 + k / (n - 1) * (x1 - x0), middle - beat(beat_phase) * amp]
        cv.create_line(*pts, fill=self.ecg_color, width=2, tags="ecg")
