"""Reads alerts aloud, in the style of a suit assistant (no internet).

Style: male English voice if the computer has one (British preferred), slightly deeper pitch,
calm pace and complete, formal phrases ("Sir, red alert. Mei's oxygen saturation is at
85 percent."). It is NOT the real voice of any character or actor: it uses the voices already
installed on the computer.

Fluency: on Windows ONE voice process is opened once and keeps listening, so each alert starts
playing immediately (previously a new process was opened for every alert).

Rules for what is read and when:
- Only important alerts: RED health alerts (not sleep, exercise/routines or mood, which only
  appear on screen) and suit oxygen notices (PLSS at 50, 30, 10, 5 and 0 %, start of the OPS
  emergency reserve and every 10 % of the OPS).
- An alert waits HALF A SECOND before being read: if it went back to green by then, it is not read.
- At most ONE alert every 2 seconds, and never on top of the one being read.
- Order: the order in which they happened. If a person gets a new oxygen notice before the
  previous one is read, only the most recent one is read.

Engines, in this order: Windows (Windows voices through SAPI, including "OneCore" voices such as
George or David if installed), macOS (`say`), Linux (`espeak-ng` / `espeak`), and pyttsx3 if
installed. If there is none, it does not fail: it leaves the text in `history`.
"""
import base64
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- settings
MIN_WAIT_S = 0.5      # an alert shorter than this (that goes back to green) is not read
INTERVAL_S = 2.0       # at most one alert every 2 s
VALIDITY_S = 20.0       # a low-priority notice that waited longer than this is no longer read

SALUTATION = "Sir"   # how the assistant addresses the listener ("" = no salutation)
PREFERRED_VOICE = ""      # part of the name of an installed voice, for example "George", "Ryan" or "Daniel"
PITCH = -4               # -10 (deeper) … 10 (higher). Assistant: slightly deep
SPEED = 1           # -10 (slow) … 10 (fast). 0-2 sounds fluent and calm

# NOT read: sleep, exercise (they are routines) or mood. They still appear on screen.
IGNORE_IN_VOICE = {"sleep", "exercise", "mood"}

PRIORITY_HIGH = 0      # red health and oxygen alerts
PRIORITY_NORMAL = 1

# name of each indicator inside a phrase ("Mei's oxygen saturation")
INDICATOR_PHRASE = {
    "hr": "heart rate", "spo2": "oxygen saturation", "temp": "temperature",
    "sbp": "systolic pressure", "glu": "glucose", "rad": "accumulated radiation",
    "sleep": "sleep", "exercise": "exercise",
}

_UNITS = [
    (r"(\d)\.(\d)", r"\1 point \2"),
    (r"≈", " about "),
    (r"SpO₂", "oxygen saturation"),
    (r"O₂", "oxygen"),
    (r"(\d)\s*%", r"\1 percent"),
    (r"\bbpm\b", "beats per minute"),
    (r"°C", " degrees"),
    (r"\bmmHg\b", "millimeters of mercury"),
    (r"\bmg/dL\b", "milligrams per deciliter"),
    (r"\bmSv\b", "millisieverts"),
    (r"\bmin/day\b", "minutes per day"),
    (r"\b1 h (\d+) min", r"1 hour \1 minutes"),
    (r"(\d+) h (\d+) min", r"\1 hours \2 minutes"),
    (r"\b1 h\b", "1 hour"),
    (r"(\d)\s*h\b", r"\1 hours"),
    (r"\b0(\d) minutes", r"\1 minutes"),
    (r"\bmin\b", "minutes"),
    (r"(?<![\d.])1 minutes", "1 minute"),
    (r"\s*·\s*", ". "),
    (r"\s*:\s*", ", "),
]


def for_voice(text):
    """Turns symbols and units into words so the voice reads them smoothly."""
    for pattern, change in _UNITS:
        text = re.sub(pattern, change, text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.])", r"\1", text)


def _with_salutation(phrase):
    """'Sir, red alert…'. The first letter is lowered only for ordinary words, never for a
    crew member's name ("Sir, Alex's PLSS…")."""
    if not SALUTATION:
        return phrase
    first = phrase.split()[0]
    if "'" in first or first in ("PLSS", "OPS"):
        return f"{SALUTATION}, {phrase}"
    return f"{SALUTATION}, {phrase[0].lower()}{phrase[1:]}"


# ---------------------------------------------------------------- voice engines
_SCRIPT_WINDOWS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$v = New-Object -ComObject SAPI.SpVoice
$tokens = @()
foreach ($cat in @('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices',
                   'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices')) {
  $c = New-Object -ComObject SAPI.SpObjectTokenCategory
  $c.SetId($cat, $false)
  foreach ($t in $c.EnumerateTokens()) { $tokens += $t }
}
function LangId($t) {
  $l = $t.GetAttribute('Language'); if (-not $l) { return 0 }
  try { return [Convert]::ToInt32(($l -split ';')[0], 16) } catch { return 0 }
}
function IsEnglish($t) { return ((LangId $t) -band 0xFF) -eq 0x09 }
function IsBritish($t) { return (LangId $t) -eq 0x809 }
$pref = '__PREFERRED__'
$chosen = $null
if ($pref) { $chosen = $tokens | Where-Object { $_.GetDescription() -like "*$pref*" } | Select-Object -First 1 }
if (-not $chosen) { $chosen = $tokens | Where-Object { (IsBritish $_) -and $_.GetAttribute('Gender') -eq 'Male' } | Select-Object -First 1 }
if (-not $chosen) { $chosen = $tokens | Where-Object { (IsEnglish $_) -and $_.GetAttribute('Gender') -eq 'Male' } | Select-Object -First 1 }
if (-not $chosen) { $chosen = $tokens | Where-Object { IsEnglish $_ } | Select-Object -First 1 }
if ($chosen) { $v.Voice = $chosen }
[Console]::Out.WriteLine('VOICE ' + $v.Voice.GetDescription())
[Console]::Out.Flush()
while ($true) {
  $l = [Console]::In.ReadLine()
  if ($l -eq $null) { break }
  $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($l))
  [void]$v.Speak($text, 8)
  [Console]::Out.WriteLine('OK')
  [Console]::Out.Flush()
}
"""


class _WindowsEngine:
    """A single PowerShell process with SAPI that stays open (no delay between alerts)."""

    def __init__(self):
        self.name_ = "Windows (SAPI)"
        self.voice = None
        self._proc = None

    def _start(self):
        script = _SCRIPT_WINDOWS.replace("__PREFERRED__", re.sub(r"[^\w ]", "", PREFERRED_VOICE))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        # the script goes on the command line; standard input stays free for the phrases
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = self._proc.stdout.readline().decode("utf-8", "replace").strip()
        if line.startswith("VOICE "):
            self.voice = line[len("VOICE "):]
            print("[Voice] Using voice:", self.voice)

    def say_(self, text):
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        xml = (f'<pitch absmiddle="{PITCH}"><rate absspeed="{SPEED}">'
               f"{escape(text)}</rate></pitch>")
        self._proc.stdin.write(base64.b64encode(xml.encode("utf-8")) + b"\n")
        self._proc.stdin.flush()
        self._proc.stdout.readline()          # wait for "OK": it finished speaking

    def stop(self):
        """Cuts the current phrase (closes PowerShell; it reopens with the next alert)."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()


class _CommandEngine:
    def __init__(self, name_, build):
        self.name_ = name_
        self._build = build

        self._proc = None

    def say_(self, text):
        self._proc = subprocess.Popen(self._build(text), stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()

    def stop(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def _say_mac(text):
    voice = PREFERRED_VOICE or "Daniel"             # male English voices on macOS: Daniel (British), Alex, Fred
    return ["say", "-v", voice, "-r", str(175 + SPEED * 10), text]


def _espeak(prog):
    return lambda t: [prog, "-v", "en-gb+m3", "-p", str(max(0, 50 + PITCH * 5)), "-s", str(160 + SPEED * 10), t]


class _Pyttsx3Engine:
    name_ = "pyttsx3"

    def __init__(self):
        import pyttsx3
        self._pyttsx3 = pyttsx3

    def say_(self, text):
        if not hasattr(self, "_engine"):
            self._engine = self._pyttsx3.init()
            best = None
            for v in self._engine.getProperty("voices"):
                data_ = f"{v.id} {v.name} {getattr(v, 'languages', '')}".lower()
                is_ = "english" in data_ or "british" in data_ or re.search(r"\ben[-_]", data_)
                if PREFERRED_VOICE and PREFERRED_VOICE.lower() in data_:
                    best = v
                    break
                if is_ and (best is None or "male" == str(getattr(v, "gender", "")).lower()):
                    best = v
            if best is not None:
                self._engine.setProperty("voice", best.id)
            self._engine.setProperty("rate", 175 + SPEED * 10)
        self._engine.say(text)
        self._engine.runAndWait()

    def stop(self):
        if hasattr(self, "_engine"):
            self._engine.stop()


def detect_engine():
    """Returns the voice engine available on this computer, or None."""
    if sys.platform.startswith("win") and shutil.which("powershell"):
        return _WindowsEngine()
    if sys.platform == "darwin" and shutil.which("say"):
        return _CommandEngine("macOS say", _say_mac)
    for prog in ("espeak-ng", "espeak"):
        if shutil.which(prog):
            return _CommandEngine(prog, _espeak(prog))
    try:
        return _Pyttsx3Engine()
    except Exception:
        return None


# ---------------------------------------------------------------- queue with rules
class Voice:
    """Queue of spoken alerts. `tick()` is called often (for example every 100 ms from Tk)."""

    def __init__(self, engine="auto", clock_=time.monotonic, enabled=True):
        self.clock_ = clock_
        self.enabled = enabled
        self.engine = detect_engine() if engine == "auto" else engine
        self.history = []                 # (instant, text) of everything that was read
        self._pending = []
        self._order = 0
        self._last = None
        self._busy = threading.Event()
        self._queue = queue.Queue()
        if self.engine is None:
            print("[Voice] No voice engine found on this computer; alerts will not be read aloud.")
        else:
            threading.Thread(target=self._thread, daemon=True).start()

    def announce(self, text, priority=PRIORITY_HIGH, key=None, current=None):
        """Adds an alert. `key` avoids repeating the same notice (the new one replaces the old one);
        `current()` is checked right before reading it: if it no longer applies (back to green), it is dropped."""
        if key is not None:
            self._pending = [p for p in self._pending if p["key"] != key]
        self._order += 1
        self._pending.append({"text": text, "priority": priority, "key": key,
                                 "current": current or (lambda: True), "t": self.clock_(),
                                 "order": self._order})

    def tick(self):
        """Reads at most one alert, if it is time. Returns the text read or None."""
        now_ = self.clock_()
        alive = []
        for p in self._pending:
            if not p["current"]():
                continue                                   # back to green: not read
            if p["priority"] != PRIORITY_HIGH and now_ - p["t"] > VALIDITY_S:
                continue
            alive.append(p)
        self._pending = alive
        # even if the COMPUTER's voice is off, the rules still apply and the text
        # stays in `history`, because the phone reads it too
        if self._busy.is_set():
            return None
        if self._last is not None and now_ - self._last < INTERVAL_S:
            return None
        ready = [p for p in self._pending if now_ - p["t"] >= MIN_WAIT_S]
        if not ready:
            return None
        p = min(ready, key=lambda x: (x["priority"], x["order"]))
        self._pending.remove(p)
        self._last = now_
        text = for_voice(p["text"])
        self.history.append((now_, text))
        if self.engine is not None and self.enabled:
            self._busy.set()
            self._queue.put(text)
        return text

    def pending_items(self):
        return [p["text"] for p in self._pending]

    def silence(self):
        """SILENCE: drops what is pending and cuts the phrase being read."""
        self._pending = []
        stop = getattr(self.engine, "stop", None)
        if stop is not None and self._busy.is_set():
            try:
                stop()
            except Exception as e:
                print("[Voice] Could not cut the phrase:", e)

    def _thread(self):
        while True:
            text = self._queue.get()
            try:
                self.engine.say_(text)
            except Exception as e:                         # the voice must never crash the app
                print("[Voice] Could not read the alert:", e)
            finally:
                self._busy.clear()


# ---------------------------------------------------------------- assistant phrases
def red_alert_phrase(name_, d):
    from simulation import SIGNAL
    if d.key == SIGNAL:
        return _with_salutation(f"Red alert. We have lost {name_}'s signal.")
    indicator = INDICATOR_PHRASE.get(d.key, d.indicator_name().lower())
    return _with_salutation(f"Red alert. {name_}'s {indicator} is at {d.value_text()}.")


def oxygen_phrase(name_, av):
    u, value = av.threshold, av.value
    time_ = ""
    if av.time_ and "not available" not in av.time_ and "no data" not in av.time_:
        time_ = f" {av.time_[0].upper()}{av.time_[1:]}."
    if u is None or value is None:                       # notice without structured data
        return _with_salutation(f"{name_}: {av.text}.")
    if u == "plss0":
        return _with_salutation(f"{name_}'s PLSS is depleted. Switching to the emergency reserve.{time_}")
    if u.startswith("plss"):
        return _with_salutation(f"{name_}'s PLSS is at {value:.0f} %.{time_}")
    from simulation import ALERT
    if value <= 0:
        return _with_salutation(f"Alert. {name_}'s emergency reserve is depleted. "
                                "Return to the ship immediately.")
    if av.level == ALERT:
        return _with_salutation(f"Alert. {name_}'s emergency reserve is at {value:.0f} %."
                                f"{time_} I recommend returning to the ship immediately.")
    return _with_salutation(f"{name_}'s emergency reserve is at {value:.0f} %.{time_}")


class VoiceWatcher:
    """Watches the mission and sends the important alerts to the voice."""

    def __init__(self, mission, voice):
        self.mission = mission
        self.voice = voice
        self._support_cur = len(mission.support_notices)
        self._phase = {d.id: d.phase for d in mission.broadcasts}
        self._silences = getattr(mission, "silences", 0)
        self._in_silence = set()            # alarms silenced while the silence lasts

    def check(self):
        from simulation import PHASE_CRITICAL, OPS
        m = self.mission
        # SILENCE (from a wristband or the phone): stop what is being read right now
        if m.silences != self._silences:
            self._silences = m.silences
            self.voice.silence()
        if m.silence_left() > 0:
            self._in_silence = set(m.silenced)
        elif self._in_silence:
            # the silence ended and the alarm is still red: it is read once more
            for d in m.broadcast_queue():
                if d.id in self._in_silence and d.phase == PHASE_CRITICAL:
                    self._phase[d.id] = None
            self._in_silence = set()
        for d in m.broadcasts:
            before = self._phase.get(d.id)
            self._phase[d.id] = d.phase
            if d.resolved or d.phase != PHASE_CRITICAL or before == PHASE_CRITICAL:
                continue
            if m.is_silenced(d):
                continue
            if d.key == OPS:          # the critical reserve is already announced as an oxygen notice
                continue
            if d.key in IGNORE_IN_VOICE:   # sleep, exercise, mood: not important alerts
                continue
            name_ = m.crew[d.affected].name_.split()[0]
            self.voice.announce(red_alert_phrase(name_, d), PRIORITY_HIGH, key=("red", d.id),
                              # if someone is already on the way before it is read, it is not read (less noise)
                              current=lambda d=d: (not d.resolved and d.phase == PHASE_CRITICAL
                                                   and not m.is_silenced(d) and d.attended_by is None))
        for av in m.support_notices[self._support_cur:]:
            name_ = m.crew[av.affected].name_.split()[0]
            # if a new one arrives for the same person before the previous one is read, the most recent is read
            self.voice.announce(oxygen_phrase(name_, av), PRIORITY_HIGH, key=("oxygen", av.affected))
        self._support_cur = len(m.support_notices)
