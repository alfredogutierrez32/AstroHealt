"""
AstroHealth - real-data acquisition (separate from validation, storage, alerts and interface).

    Source (adapter)  ->  normalize() / validate  ->  MeasurementProcessor  ->  Mission / Database  ->  interface
    (separate thread)      (Measurement contract)     (duplicates, order,      (alert rules)           (Tk, via queue)
                                                       staleness)

Rules:
- No missing indicator is filled with simulated data: it stays "not available".
- The measurement instant is never invented: if the source does not provide it, t_measured = None.
- The ORIGINAL value and unit are kept; every conversion is recorded.
- Acceptable staleness is CONFIGURED per source and indicator. Without configuration nothing is
  marked "stale" (no limit is invented).
- "Connected" is only reported after the source confirms the connection.
- Adding real measurements does NOT clinically validate the software or its demo thresholds.

INTEGRATION STATUS: there is no real source defined yet (device, format, frequency).
LiveSource is only an interface; no working connection is claimed to exist.
"""
import csv
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from simulation import (IND, validate, Q_OK, Q_INVALID, Q_STALE,
                        MODE_REPLAY, MODE_LIVE)

Q_DUPLICATE = "duplicate"
Q_OUT_OF_ORDER = "out_of_order"
Q_UNIT = "unsupported_unit"
Q_UNKNOWN = "unknown_indicator"

# Life support (not health indicators): tank level in %
SUPPORT = {"plss": "%", "ops": "%"}

# Unit synonyms accepted WITHOUT conversion
SYNONYMS = {"bpm": {"bpm", "bpm", "beats/min"}, "%": {"%"}, "°C": {"°C", "C", "degC"},
             "mmHg": {"mmHg", "mmhg"}, "mg/dL": {"mg/dL", "mg/dl"}, "h": {"h", "hours"},
             "min/day": {"min/day", "min/dia", "min"}, "mSv": {"mSv", "msv"}}
# Explicit, documented conversions (any other unit is rejected; nothing is guessed)
CONVERSIONS = {
    ("temp", "°F"): (lambda v: (v - 32.0) * 5.0 / 9.0, "°F→°C: (v−32)×5/9"),
    ("glu", "mmol/L"): (lambda v: v * 18.0, "mmol/L→mg/dL: ×18.0 (usual factor for glucose)"),
    ("rad", "µSv"): (lambda v: v / 1000.0, "µSv→mSv: ÷1000"),
    ("rad", "uSv"): (lambda v: v / 1000.0, "µSv→mSv: ÷1000"),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Measurement:
    """Measurement contract."""
    person: str              # PSEUDONYMIZED identifier (never a real name)
    source: str               # adapter / origin
    device: str          # device model or id (if the source provides it)
    indicator: str
    value: object             # normalized value (float) or None if not valid
    unit: str               # canonical unit
    original_value: object
    original_unit: object
    conversion: object        # text of the conversion applied, or None
    t_measured: object        # ISO 8601 with zone, or None if the source does not provide it
    t_received: str          # ISO 8601 (real clock of the receiving computer)
    quality: str
    mode: str                 # REPLAY or LIVE
    external_id: object = None # message id in the source (to detect duplicates)


def normalize(person, source, device, indicator, value, unit, t_measured, mode,
               external_id=None, t_received=None):
    """Converts a raw record into a Measurement. Never turns bad data into valid data."""
    t_rec = t_received or now_iso()
    base = dict(person=person, source=source, device=device, indicator=indicator,
                original_value=value, original_unit=unit, t_measured=t_measured or None,
                t_received=t_rec, mode=mode, external_id=external_id)
    if indicator in SUPPORT:
        canon = SUPPORT[indicator]
    elif indicator in IND:
        canon = IND[indicator].unit
    else:
        return Measurement(value=None, unit=str(unit), conversion=None, quality=Q_UNKNOWN, **base)
    v = value
    if isinstance(v, str):                       # CSV: text -> number (if not possible, invalid)
        try:
            v = float(v.replace(",", ".")) if v.strip() else None
        except ValueError:
            return Measurement(value=None, unit=canon, conversion=None, quality=Q_INVALID, **base)
    conversion = None
    if unit not in SYNONYMS.get(canon, {canon}):
        conv = CONVERSIONS.get((indicator, unit))
        if conv is None:
            return Measurement(value=None, unit=canon, conversion=None, quality=Q_UNIT, **base)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = conv[0](float(v))
        conversion = conv[1]
    if indicator in SUPPORT:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= float(v) <= 100
        return Measurement(value=float(v) if ok else None, unit=canon, conversion=conversion,
                        quality=Q_OK if ok else Q_INVALID, **base)
    v, qual = validate(indicator, v)
    return Measurement(value=v, unit=canon, conversion=conversion, quality=qual, **base)


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------
class Source:
    """Adapter interface. connect_() must fail (exception) if there is no real connection."""
    name_ = "source"
    device = "unknown"
    mode = MODE_LIVE

    def connect_(self):
        raise NotImplementedError

    def read_(self):
        """Generator of raw dicts: person, indicator, value, unit, t_measured (optional), id (optional)."""
        raise NotImplementedError

    def close_window(self):
        pass


class HistoricalCSVSource(Source):
    """Replay of a historical CSV file with columns:
        person, indicator, value, unit, t_measured, id, device
    (t_measured, id and device may be empty). Rows are delivered in file order;
    no times are invented and indicators missing from the file are not filled in."""
    mode = MODE_REPLAY
    COLUMNS = ("person", "indicator", "value", "unit")

    def __init__(self, path_, name_="Historical CSV"):
        self.path_ = path_
        self.name_ = name_
        self._f = None

    def connect_(self):
        self._f = open(self.path_, newline="", encoding="utf-8")
        reader = csv.DictReader(self._f)
        remaining = [c for c in self.COLUMNS if c not in (reader.fieldnames or [])]
        if remaining:
            self._f.close()
            raise ValueError(f"CSV is missing columns: {', '.join(remaining)}")
        self._reader = reader

    def read_(self):
        for row in self._reader:
            yield {"person": row.get("person", "").strip(), "indicator": row.get("indicator", "").strip(),
                   "value": row.get("value"), "unit": (row.get("unit") or "").strip(),
                   "t_measured": (row.get("t_measured") or "").strip() or None,
                   "id": (row.get("id") or "").strip() or None,
                   "device": (row.get("device") or "").strip() or "not specified"}

    def close_window(self):
        if self._f:
            self._f.close()


class LiveSource(Source):
    """PENDING: no device/protocol has been defined. It does not connect to anything.
    To implement it you need: device model, transport (serial/Bluetooth/API),
    documented message format, indicators, units, frequency and whether it has its own timestamp."""
    name_ = "live (not defined)"

    def connect_(self):
        raise NotImplementedError("Live source not defined yet: device, protocol and format are missing.")


# --------------------------------------------------------------------------
# Processing: duplicates, order, staleness and applying to the engine
# --------------------------------------------------------------------------
def _seconds(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


class MeasurementProcessor:
    def __init__(self, mission, staleness_limits=None):
        """staleness_limits: {(source, indicator): seconds} CONFIGURED for the real source.
        Without an entry for a pair, the staleness of that indicator is not judged."""
        self.mission = mission
        self.limits = dict(staleness_limits or {})
        self._seen = set()                 # (source, external_id) or fingerprint
        self._last_t = {}                  # (person, indicator) -> seconds of t_measured
        self._last_rec = {}                # (person, indicator) -> (source, reception seconds)
        self.rejected = []

    def _fingerprint(self, m):
        if m.external_id:
            return (m.source, m.external_id)
        return (m.source, m.person, m.indicator, m.t_measured, str(m.original_value))

    def process(self, m):
        """Returns the final quality. Measurements are applied to the engine only if new and in order."""
        h = self._fingerprint(m)
        if h in self._seen:
            m.quality = Q_DUPLICATE
            self.rejected.append(m)
            return m.quality
        self._seen.add(h)
        key = (m.person, m.indicator)
        t_meas = _seconds(m.t_measured) if m.t_measured else None
        if t_meas is not None and key in self._last_t and t_meas < self._last_t[key]:
            m.quality = Q_OUT_OF_ORDER       # it is stored, but does not replace more recent data
            self.rejected.append(m)
            return m.quality
        if t_meas is not None:
            self._last_t[key] = t_meas
        self._last_rec[key] = (m.source, _seconds(m.t_received))
        self.mission.apply_measurement(m)
        return m.quality

    def check_staleness(self, now_s=None):
        """Marks as 'stale' whatever exceeded the CONFIGURED limit for its source and indicator."""
        now_s = time.time() if now_s is None else now_s
        marked = []
        for (person, indicator), (source, t_rec) in self._last_rec.items():
            limit = self.limits.get((source, indicator))
            if limit is None or t_rec is None:
                continue
            if now_s - t_rec > limit:
                if self.mission.mark_stale(person, indicator):
                    marked.append((person, indicator))
        return marked


# --------------------------------------------------------------------------
# Acquisition in a thread (does not block Tk). The interface reads the queue with after().
# --------------------------------------------------------------------------
class Acquisition:
    def __init__(self, source, retries=3, wait_s=2.0):
        self.source = source
        self.retries = retries
        self.wait_s = wait_s
        self.queue_ = queue.Queue()
        self.status = "disconnected"        # only becomes "connected" after connect_() without error
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        attempts = 0
        while not self._stop.is_set():
            try:
                self.source.connect_()
                self.status = "connected"
                self.queue_.put(("status", "connected", None))
                attempts = 0
                for raw in self.source.read_():
                    if self._stop.is_set():
                        break
                    m = normalize(raw.get("person"), self.source.name_, raw.get("device", "not specified"),
                                   raw.get("indicator"), raw.get("value"), raw.get("unit"),
                                   raw.get("t_measured"), self.source.mode, raw.get("id"))
                    self.queue_.put(("measurement", m, None))
                self.source.close_window()
                self.status = "end of data" if self.source.mode == MODE_REPLAY else "disconnected"
                self.queue_.put(("status", self.status, None))
                if self.source.mode == MODE_REPLAY:
                    return                          # a replay ends; it does not repeat by itself
            except Exception as e:                  # disconnection / adapter error
                self.status = "disconnected"
                self.queue_.put(("status", "error", f"{type(e).__name__}: {e}"))
                try:
                    self.source.close_window()
                except Exception:
                    pass
                attempts += 1
                if attempts > self.retries or isinstance(e, NotImplementedError):
                    self.queue_.put(("status", "no connection", "retries exhausted"))
                    return
                self._stop.wait(self.wait_s)

    def drain(self, maximum=500):
        """Returns pending messages (to call it from Tk with after)."""
        output = []
        while len(output) < maximum:
            try:
                output.append(self.queue_.get_nowait())
            except queue.Empty:
                break
        return output
