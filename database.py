"""
AstroHealth - ship database (SQLite, standard library).

Guarantees of this version:
- VERSIONED migrations (PRAGMA user_version). Before migrating an existing database a backup
  COPY of the file is made (astrohealth.db.backup-YYYYMMDD-HHMMSS). Data is never deleted.
- Each session has a unique identifier (UUID) and is recorded in the `sessions` table.
- Each record stores: REAL date/time with zone (UTC, ISO 8601), simulated minute, simulated
  mission date and, when applicable, the incident (alarm) identifier.
- Readings, events, notices and routines of one step are saved in ONE transaction. Cursors and
  counters only advance AFTER the commit: if something fails there is a rollback and the next attempt
  saves what is pending without duplicating or skipping anything.
- Minute zero: when the session starts the ship saves the initial readings with minute = 0.
  The export includes that minute 0 and ALL saved minutes of the session (not only the
  latest ones in memory).
- Atomic export: temporary files are written and the final ones are only replaced if everything went well.
- Schema v3 uses English table and column names; older Spanish-named databases (v1/v2) are
  backed up and renamed in place (no data is lost).

Synthetic demo data. Do not use for real medical decisions.
"""
import csv
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone

from simulation import INDICATORS, STATE_TEXT

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "astrohealth.db")
SCHEMA_VERSION = 4          # v3: English table and column names · v4: pls column renamed to plss

# Databases created by the Spanish version (schema v1/v2) are renamed in place when opened
# (after the file backup). No data is deleted.
LEGACY_TABLES = {"sesiones": "sessions", "lecturas": "readings", "eventos": "events", "avisos": "notices",
                 "rutinas": "routines", "mediciones": "measurements", "cambios_config": "config_changes"}
LEGACY_COLUMNS = {
    "sesion": "session", "inicio_real": "real_start", "modo": "mode", "version_app": "app_version",
    "minuto": "minute", "hora_utc": "utc_time", "fecha_sim": "sim_time", "fecha_real": "real_time",
    "tripulante": "crew_member", "seudonimo": "pseudonym", "rol": "role", "escenario": "scenario",
    "fc": "hr", "pas": "sbp", "sueno": "sleep", "ejer": "exercise", "calidades": "qualities",
    "origenes": "origins", "animo": "mood", "animo_informado": "reported_mood", "estado": "status",
    "estado_soporte": "support_status", "recibido": "received", "detalle": "detail", "incidente": "incident",
    "categoria": "category", "afectado": "affected", "indicador": "indicator", "receptor": "receiver",
    "accion": "action", "rutina": "routine", "puntos": "points", "valor": "value", "persona": "person",
    "fuente": "source", "dispositivo": "device", "unidad": "unit", "valor_original": "original_value",
    "unidad_original": "original_unit", "t_medicion": "t_measured", "t_recepcion": "t_received",
    "calidad": "quality", "id_externo": "external_id", "cambio": "change",
    "pls": "plss",                    # v4: the main tank is now called PLSS
}
APP_VERSION = "2.0"

IND_COLS = [i.key for i in INDICATORS]
TABLES = {
    "sessions": [("uuid", "TEXT"), ("real_start", "TEXT"), ("mode", "TEXT"), ("app_version", "TEXT")],
    "readings": [("session", "TEXT"), ("minute", "INTEGER"), ("utc_time", "TEXT"), ("sim_time", "TEXT"),
                 ("real_time", "TEXT"), ("crew_member", "TEXT"), ("pseudonym", "TEXT"), ("role", "TEXT"),
                 ("scenario", "TEXT"), ("mode", "TEXT")]
                + [(c, "REAL") for c in IND_COLS]
                + [("qualities", "TEXT"), ("origins", "TEXT"), ("mood", "INTEGER"),
                   ("reported_mood", "INTEGER"), ("plss", "REAL"), ("ops", "REAL"),
                   ("status", "TEXT"), ("support_status", "TEXT"), ("received", "TEXT")],
    "events": [("session", "TEXT"), ("utc_time", "TEXT"), ("crew_member", "TEXT"), ("status", "TEXT"),
                ("detail", "TEXT"), ("minute", "INTEGER"), ("sim_time", "TEXT"), ("real_time", "TEXT"),
                ("incident", "TEXT"), ("category", "TEXT")],
    "notices": [("session", "TEXT"), ("utc_time", "TEXT"), ("affected", "TEXT"), ("indicator", "TEXT"),
               ("receiver", "TEXT"), ("action", "TEXT"), ("minute", "INTEGER"), ("sim_time", "TEXT"),
               ("real_time", "TEXT"), ("incident", "TEXT")],
    "routines": [("session", "TEXT"), ("crew_member", "TEXT"), ("routine", "TEXT"), ("points", "INTEGER"),
                ("utc_time", "TEXT"), ("minute", "INTEGER"), ("sim_time", "TEXT"), ("real_time", "TEXT"),
                ("value", "TEXT")],
    # real measurements (REPLAY / LIVE modes), with original value and unit
    "measurements": [("session", "TEXT"), ("person", "TEXT"), ("source", "TEXT"), ("device", "TEXT"),
                   ("indicator", "TEXT"), ("value", "REAL"), ("unit", "TEXT"), ("original_value", "TEXT"),
                   ("original_unit", "TEXT"), ("conversion", "TEXT"), ("t_measured", "TEXT"),
                   ("t_received", "TEXT"), ("quality", "TEXT"), ("mode", "TEXT"), ("external_id", "TEXT")],
    "config_changes": [("session", "TEXT"), ("real_time", "TEXT"), ("actor", "TEXT"), ("change", "TEXT")],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SaveError(Exception):
    """Could not save; the data stays in memory and will be retried."""


class Database:
    def __init__(self, path_=DB_PATH, mode="SIMULATION"):
        self.path_ = path_
        existed = path_ != ":memory:" and os.path.exists(path_) and os.path.getsize(path_) > 0
        self.conn = sqlite3.connect(path_)
        self.backup = None
        self._migrate(existed)
        self.session = uuid.uuid4().hex                     # UNIQUE session identifier
        self.real_start = now_iso()
        with self.conn:
            self.conn.execute("INSERT INTO sessions (uuid, real_start, mode, app_version) VALUES (?,?,?,?)",
                             (self.session, self.real_start, mode, APP_VERSION))
        self._cur = {"events": 0, "notices": 0, "routines": 0}
        self.last_sent = None
        self.session_readings = 0
        self.last_error = None

    # ---------------- structure and migration ----------------
    def _columns(self, table):
        return [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")]

    def _rename_legacy(self):
        """Spanish schema (v1/v2) -> English names, keeping every row."""
        for old, new in LEGACY_TABLES.items():
            if self._columns(old) and not self._columns(new):
                self.conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
        for table in TABLES:
            currents = self._columns(table)
            for c in currents:
                new = LEGACY_COLUMNS.get(c)
                if new and new not in currents:
                    self.conn.execute(f"ALTER TABLE {table} RENAME COLUMN {c} TO {new}")

    def _migrate(self, existed):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if existed:
            # backup BEFORE touching the schema of an existing database
            mark = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.backup = f"{self.path_}.backup-{mark}"
            self.conn.commit()
            shutil.copy2(self.path_, self.backup)
        try:
            with self.conn:                                  # a single transaction
                self._rename_legacy()
                for table, cols in TABLES.items():
                    currents = self._columns(table)
                    if not currents:
                        defs = ", ".join(f"{c} {t}" for c, t in cols)
                        self.conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {defs})")
                    else:
                        for c, t in cols:
                            if c not in currents:
                                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.DatabaseError as e:
            raise SaveError(f"Could not migrate the database ({e}). "
                                f"The original file was not deleted; backup: {self.backup}") from e

    # ---------------- save ----------------
    def _reading_rows(self, mission):
        rows = []
        for t in mission.crew:
            rows.append((self.session, mission.minutes_, mission.clock(), mission.sim_time(), now_iso(),
                          t.name_, t.pseudonym, t.role, t.scenario, mission.mode,
                          *[t.values_[c] for c in IND_COLS],
                          json.dumps(t.quality, ensure_ascii=False), json.dumps(t.origin, ensure_ascii=False),
                          t.mood, t.reported_mood, t.o2_tank, t.o2_reserve,
                          STATE_TEXT[t.overall_state()], STATE_TEXT[t.support_state()],
                          datetime.now(timezone.utc).strftime("%H:%M:%S")))
        return rows

    def receive_transmission(self, mission, routines=None):
        """Saves the current minute's readings + pending events/notices/routines in ONE transaction."""
        rows = self._reading_rows(mission)
        self._transaction(mission, routines, rows)
        self.last_sent = mission.clock()
        self.session_readings += len(rows)               # only after the commit

    def sync(self, mission, routines=None):
        """Saves pending events, notices and routines (no readings). Can be called while paused."""
        self._transaction(mission, routines, [])

    def _transaction(self, mission, routines, reading_rows):
        ev = mission.total_events[self._cur["events"]:]
        av = mission.total_notices[self._cur["notices"]:]
        ru = routines.records[self._cur["routines"]:] if routines is not None else []
        try:
            with self.conn:                               # commit on exit; rollback on exception
                if reading_rows:
                    cols = [c for c, _ in TABLES["readings"]]
                    self.conn.executemany(
                        f"INSERT INTO readings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        reading_rows)
                self.conn.executemany(
                    "INSERT INTO events (session, utc_time, crew_member, status, detail, minute, sim_time, "
                    "real_time, incident, category) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(self.session, e.clock, e.name_, STATE_TEXT[e.status], e.text, e.minute, e.sim_time,
                      e.real_time, e.incident, e.category) for e in ev])
                self.conn.executemany(
                    "INSERT INTO notices (session, utc_time, affected, indicator, receiver, action, minute, "
                    "sim_time, real_time, incident) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(self.session, a.clock, a.affected, a.indicator, a.receiver, a.action, a.minute,
                      a.sim_time, a.real_time, a.incident) for a in av])
                self.conn.executemany(
                    "INSERT INTO routines (session, crew_member, routine, points, utc_time, minute, sim_time, "
                    "real_time, value) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(self.session, r.crew_member, r.reason, r.points, r.clock, r.minute, r.sim_time,
                      r.real_time, r.value) for r in ru])
        except sqlite3.Error as e:
            self.last_error = str(e)
            raise SaveError(f"Could not save (it will be retried): {e}") from e
        # only after the commit
        self._cur["events"] += len(ev)
        self._cur["notices"] += len(av)
        self._cur["routines"] += len(ru)
        self.last_error = None

    def pending_items(self, mission, routines=None):
        return (len(mission.total_events) - self._cur["events"]
                + len(mission.total_notices) - self._cur["notices"]
                + (len(routines.records) - self._cur["routines"] if routines is not None else 0))

    def save_measurements(self, measurements):
        """Saves real measurements (see sources.Measurement) in one transaction."""
        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO measurements (session, person, source, device, indicator, value, unit, "
                    "original_value, original_unit, conversion, t_measured, t_received, quality, mode, "
                    "external_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(self.session, m.person, m.source, m.device, m.indicator, m.value, m.unit,
                      None if m.original_value is None else str(m.original_value), m.original_unit,
                      m.conversion, m.t_measured, m.t_received, m.quality, m.mode, m.external_id)
                     for m in measurements])
        except sqlite3.Error as e:
            raise SaveError(f"Could not save measurements: {e}") from e

    def record_change(self, actor, change):
        try:
            with self.conn:
                self.conn.execute("INSERT INTO config_changes (session, real_time, actor, change) VALUES (?,?,?,?)",
                                 (self.session, now_iso(), actor, change))
        except sqlite3.Error as e:
            raise SaveError(f"could not record the configuration change: {e}") from e

    # ---------------- export (from SQLite, atomic) ----------------
    def export_session(self, readings_path, session=None):
        """Exports the COMPLETE session from SQLite: readings (including minute 0), routines and notices.
        Writes temporary files and only replaces the final files if all three were written correctly.
        Returns {path: rows}."""
        session = session or self.session
        base = readings_path[:-4] if readings_path.lower().endswith(".csv") else readings_path
        targets = {
            base + ".csv": ("readings", [c for c, _ in TABLES["readings"]]),
            base + "_routines.csv": ("routines", [c for c, _ in TABLES["routines"]]),
            base + "_notices.csv": ("notices", [c for c, _ in TABLES["notices"]]),
        }
        temp_files, count_ = {}, {}
        try:
            for target_, (table, cols) in targets.items():
                tmp = f"{target_}.tmp-{uuid.uuid4().hex[:8]}"
                temp_files[target_] = tmp
                rows = self.conn.execute(
                    f"SELECT {', '.join(cols)} FROM {table} WHERE session = ? ORDER BY id", (session,)).fetchall()
                with open(tmp, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(cols)
                    w.writerows(rows)
                count_[target_] = len(rows)
            for target_, tmp in temp_files.items():
                os.replace(tmp, target_)
        except (OSError, sqlite3.Error):
            for tmp in temp_files.values():
                if os.path.exists(tmp):
                    os.remove(tmp)
            raise
        return count_

    # ---------------- queries ----------------
    def total_readings(self, session=None):
        if session:
            return self.conn.execute("SELECT COUNT(*) FROM readings WHERE session = ?", (session,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def new_session(self, mission, routines=None, mode="SIMULATION"):
        """RESTART SIMULATION: saves what is pending in the current session and opens a NEW session.
        Nothing is deleted: the previous session stays complete in the database. Returns the previous uuid."""
        self.sync(mission, routines)
        previous = self.session
        session = uuid.uuid4().hex
        start = now_iso()
        try:
            with self.conn:
                self.conn.execute("INSERT INTO sessions (uuid, real_start, mode, app_version) VALUES (?,?,?,?)",
                                  (session, start, mode, APP_VERSION))
        except sqlite3.Error as e:
            raise SaveError(f"Could not create the new session: {e}") from e
        self.session, self.real_start = session, start
        self._cur = {"events": 0, "notices": 0, "routines": 0}
        self.last_sent = None
        self.session_readings = 0
        return previous

    def close_window(self, mission=None, routines=None):
        """Syncs what is pending and then closes. If it cannot save, it does NOT close silently."""
        if mission is not None:
            self.sync(mission, routines)
        self.conn.close()
