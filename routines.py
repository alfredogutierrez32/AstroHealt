"""
AstroHealth - daily routines and light gamification.

Design rules:
- Points are given ONLY for behaviors: completing a routine, handling your alert,
  responding to a notice on time and helping a crewmate.
- Points are NEVER given for "being healthy" and are NEVER taken from someone in alert.
- There is no individual ranking: team progress is shown (collective goal).
- Records are saved in the database together with the alarms, in the same transaction
  (Database.sync / receive_transmission).

Badges:
- "First routine": the first ROUTINE completed (other earlier behaviors do not prevent it).
- "Always connected 24 h": 24 CONTINUOUS hours with signal; a disconnection resets the count.

Synthetic demo data. Do not use for real medical decisions.
"""
import csv
from collections import namedtuple
from dataclasses import dataclass, field

from simulation import NO_SIGNAL

Record = namedtuple("Record", "clock crew_member reason points minute sim_time real_time value kind")


@dataclass(frozen=True)
class Routine:
    key: str
    name_: str
    points: int
    description: str


ROUTINES = [
    Routine("workout", "Daily exercise", 20, "Treadmill, bike or weights: at least 30 min."),
    Routine("sleep", "Sleep 7 h", 15, "Log that you slept on schedule."),
    Routine("hydration", "Hydration", 10, "Drink 2 L of water throughout the day."),
    Routine("mood", "Mood check-in", 10, "How do you feel today? (1 to 5)"),
]
ROUT = {r.key: r for r in ROUTINES}

TEAM_GOAL = 0.80
MIN_ROUTINES_STREAK = 3
MIN_CONNECTED = 24 * 60            # 24 continuous hours

POINTS_OWN_ALERT = 10
POINTS_ACK_ON_TIME = 5
POINTS_HELP_CREWMATE = 20

BADGES = {
    "streak3": "3-day streak",
    "hydrated": "Hydrated",
    "good_crewmate": "Good crewmate",
    "connected": "Always connected 24 h",
    "first": "First routine",
}


@dataclass
class Progress:
    points: int = 0
    streak: int = 0
    done_today: dict = field(default_factory=dict)
    badges: list = field(default_factory=list)
    hydrated_days: int = 0
    crewmates_helped: int = 0
    min_with_signal: int = 0             # CONTINUOUS minutes with signal
    total_routines: int = 0

    @property
    def level(self):
        return self.points // 100

    @property
    def points_in_level(self):
        return self.points % 100


class Routines:
    def __init__(self, mission):
        self.mission = mission
        self.progress = [Progress() for _ in mission.crew]
        self.day = 1
        self.records = []        # Record(...) -> saved to the database
        self.last_achievement = {}

    def _give(self, i, reason, points, value=None, kind="behavior"):
        p = self.progress[i]
        level_before = p.level
        p.points += points
        t = self.mission.crew[i]
        m = self.mission
        self.records.append(Record(m.clock(), t.name_, reason, points, m.minutes_, m.sim_time(),
                                       m.utc_clock().isoformat(), value, kind))
        text = f"+{points} pts · {reason}"
        if p.level > level_before:
            text += f" · Level {p.level}!"
        self.last_achievement[i] = text
        return points

    def _badge(self, i, key):
        p = self.progress[i]
        name_ = BADGES[key]
        if name_ not in p.badges:
            p.badges.append(name_)
            self.last_achievement[i] = f"New badge: {name_}"

    def complete_routine(self, i, key, mood=None):
        """Crew member i completed a routine. Returns the points earned (0 if they already had it)."""
        p = self.progress[i]
        if key in p.done_today:
            return 0
        if key == "mood":
            if mood is None:
                return 0
            self.mission.crew[i].record_mood(mood, self.mission.minutes_)
        p.done_today[key] = self.mission.clock()
        r = ROUT[key]
        value = str(int(mood)) if key == "mood" else None
        pts = self._give(i, r.name_, r.points, value=value, kind="routine")
        p.total_routines += 1
        if p.total_routines == 1:
            self._badge(i, "first")
        return pts

    def handled_own_alert(self, i):
        return self._give(i, "Handled your alert", POINTS_OWN_ALERT)

    def ack_on_time(self, i):
        return self._give(i, "Responded to a notice on time", POINTS_ACK_ON_TIME)

    def helped_crewmate(self, i):
        self.progress[i].crewmates_helped += 1
        self._badge(i, "good_crewmate")
        return self._give(i, "Helped a crewmate", POINTS_HELP_CREWMATE)

    def step(self):
        rng = self.mission.rng
        for i, t in enumerate(self.mission.crew):
            p = self.progress[i]
            if t.overall_state() != NO_SIGNAL:
                p.min_with_signal += 1
                if p.min_with_signal >= MIN_CONNECTED:
                    self._badge(i, "connected")
            else:
                p.min_with_signal = 0            # 24 CONTINUOUS hours: a disconnection resets the count
            if i == self.mission.me or self.mission.mode != "SIMULATION":
                continue                       # in real-data modes nobody completes routines "by themselves"
            pending_items = [r.key for r in ROUTINES if r.key not in p.done_today]
            if pending_items and rng.random() < 0.04:
                key = rng.choice(pending_items)
                self.complete_routine(i, key, mood=t.mood if key == "mood" else None)

    def close_day(self):
        for i, p in enumerate(self.progress):
            if len(p.done_today) >= MIN_ROUTINES_STREAK:
                p.streak += 1
            else:
                p.streak = 0
            if "hydration" in p.done_today:
                p.hydrated_days += 1
                if p.hydrated_days >= 3:
                    self._badge(i, "hydrated")
            if p.streak >= 3:
                self._badge(i, "streak3")
            p.done_today = {}
        self.day += 1

    def team_progress(self):
        total = len(ROUTINES) * len(self.progress)
        done_list = sum(len(p.done_today) for p in self.progress)
        return done_list / total if total else 0.0

    def goal_met(self):
        return self.team_progress() >= TEAM_GOAL

    def team_points(self):
        return sum(p.points for p in self.progress)

    def export_csv(self, path_):
        with open(path_, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(Record._fields)
            w.writerows(self.records)
