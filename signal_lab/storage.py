"""SQLite persistence and raw-data exports.

Live capture appends rows to small in-memory buffers and writes them with
executemany(). This keeps per-report SQLite calls out of the measurement/UI
hot path while preserving every raw row.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_utc TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    app_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS controller_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp_ns INTEGER NOT NULL,
    source TEXT NOT NULL,
    lx REAL, ly REAL, rx REAL, ry REAL, lt REAL, rt REAL,
    raw_report_hex TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_controller_session_time ON controller_samples(session_id, timestamp_ns);
CREATE TABLE IF NOT EXISTS oscillator_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp_ns INTEGER NOT NULL,
    source TEXT NOT NULL,
    frequency_hz REAL,
    duty_cycle_percent REAL,
    period_s REAL,
    quality TEXT NOT NULL DEFAULT 'measured',
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_osc_session_time ON oscillator_samples(session_id, timestamp_ns);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp_ns INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""

_CONTROLLER_SQL = """INSERT INTO controller_samples(
    session_id,timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex,extra_json
) VALUES(?,?,?,?,?,?,?,?,?,?,?)"""
_OSCILLATOR_SQL = """INSERT INTO oscillator_samples(
    session_id,timestamp_ns,source,frequency_hz,duty_cycle_percent,period_s,quality
) VALUES(?,?,?,?,?,?,?)"""
_EVENT_SQL = "INSERT INTO events(session_id,timestamp_ns,event_type,payload_json) VALUES(?,?,?,?)"
_STANDARD_CONTROLLER_FIELDS = frozenset({"lx","ly","rx","ry","lt","rt"})


class LabDatabase:
    BUFFER_FLUSH_ROWS = 4096

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(controller_samples)")}
        if "extra_json" not in columns:
            self.conn.execute("ALTER TABLE controller_samples ADD COLUMN extra_json TEXT NOT NULL DEFAULT '{}'")
        self.conn.commit()
        self._controller_buffer: list[tuple] = []
        self._oscillator_buffer: list[tuple] = []
        self._event_buffer: list[tuple] = []

    @property
    def pending_row_count(self) -> int:
        return len(self._controller_buffer) + len(self._oscillator_buffer) + len(self._event_buffer)

    def _maybe_flush(self) -> None:
        if self.pending_row_count >= self.BUFFER_FLUSH_ROWS:
            self.flush()

    def create_session(self, name: str, mode: str, app_version: str, metadata: dict | None = None) -> str:
        self.flush()
        session_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions(id,created_utc,name,mode,app_version,metadata_json) VALUES(?,?,?,?,?,?)",
            (session_id, datetime.now(timezone.utc).isoformat(), name, mode, app_version, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return session_id

    def add_controller_sample(self, session_id: str, timestamp_ns: int, sample: dict, *, source: str, raw_report_hex: str | None = None) -> None:
        extra = {key: value for key, value in sample.items() if key not in _STANDARD_CONTROLLER_FIELDS}
        extra_json = "{}" if not extra else json.dumps(extra, separators=(",", ":"))
        self._controller_buffer.append((
            session_id, int(timestamp_ns), source,
            sample.get("lx"), sample.get("ly"), sample.get("rx"), sample.get("ry"),
            sample.get("lt"), sample.get("rt"), raw_report_hex, extra_json,
        ))
        self._maybe_flush()

    def add_oscillator_sample(self, session_id: str, timestamp_ns: int, frequency_hz: float, *, source: str, duty_cycle_percent: float | None = None, quality: str = "measured") -> None:
        frequency = float(frequency_hz)
        self._oscillator_buffer.append((
            session_id, int(timestamp_ns), source, frequency, duty_cycle_percent,
            (1.0 / frequency) if frequency else None, quality,
        ))
        self._maybe_flush()

    def add_event(self, session_id: str, timestamp_ns: int, event_type: str, payload: dict | None = None) -> None:
        self._event_buffer.append(
            (session_id, int(timestamp_ns), event_type, json.dumps(payload or {}, separators=(",", ":")))
        )
        self._maybe_flush()

    def flush(self) -> None:
        if not self.pending_row_count:
            return
        try:
            if self._controller_buffer:
                self.conn.executemany(_CONTROLLER_SQL, self._controller_buffer)
            if self._oscillator_buffer:
                self.conn.executemany(_OSCILLATOR_SQL, self._oscillator_buffer)
            if self._event_buffer:
                self.conn.executemany(_EVENT_SQL, self._event_buffer)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        else:
            self._controller_buffer.clear()
            self._oscillator_buffer.clear()
            self._event_buffer.clear()

    def session_summary(self, session_id: str) -> dict:
        self.flush()
        session = self.conn.execute("SELECT id,created_utc,name,mode,app_version,metadata_json FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise KeyError(session_id)
        controller_count = self.conn.execute("SELECT COUNT(*) FROM controller_samples WHERE session_id=?", (session_id,)).fetchone()[0]
        oscillator_count = self.conn.execute("SELECT COUNT(*) FROM oscillator_samples WHERE session_id=?", (session_id,)).fetchone()[0]
        event_count = self.conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)).fetchone()[0]
        return {
            "id": session[0], "created_utc": session[1], "name": session[2], "mode": session[3],
            "app_version": session[4], "metadata": json.loads(session[5]),
            "controller_samples": controller_count, "oscillator_samples": oscillator_count, "events": event_count,
        }

    def export_json(self, session_id: str, destination: str | Path) -> Path:
        self.flush()
        destination = Path(destination)
        payload = {"session": self.session_summary(session_id)}
        payload["controller_samples"] = []
        for row in self.conn.execute(
            "SELECT timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex,extra_json FROM controller_samples WHERE session_id=? ORDER BY timestamp_ns",
            (session_id,),
        ):
            item = dict(zip(["timestamp_ns","source","lx","ly","rx","ry","lt","rt","raw_report_hex","extra_json"], row))
            item["extra"] = json.loads(item.pop("extra_json") or "{}")
            payload["controller_samples"].append(item)
        payload["oscillator_samples"] = [
            dict(zip(["timestamp_ns","source","frequency_hz","duty_cycle_percent","period_s","quality"], row))
            for row in self.conn.execute(
                "SELECT timestamp_ns,source,frequency_hz,duty_cycle_percent,period_s,quality FROM oscillator_samples WHERE session_id=? ORDER BY timestamp_ns",
                (session_id,),
            )
        ]
        payload["events"] = [
            {"timestamp_ns": row[0], "event_type": row[1], "payload": json.loads(row[2])}
            for row in self.conn.execute(
                "SELECT timestamp_ns,event_type,payload_json FROM events WHERE session_id=? ORDER BY timestamp_ns",
                (session_id,),
            )
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    def export_controller_csv(self, session_id: str, destination: str | Path) -> Path:
        self.flush()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute(
            "SELECT timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex,extra_json FROM controller_samples WHERE session_id=? ORDER BY timestamp_ns",
            (session_id,),
        )
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_ns","source","lx","ly","rx","ry","lt","rt","raw_report_hex","extra_json"])
            writer.writerows(rows)
        return destination

    def session_series(self, session_id: str) -> dict:
        self.flush()
        session = self.session_summary(session_id)
        controller_timestamps = [
            int(row[0])
            for row in self.conn.execute(
                "SELECT timestamp_ns FROM controller_samples WHERE session_id=? ORDER BY timestamp_ns",
                (session_id,),
            )
        ]
        oscillator_rows = self.conn.execute(
            """SELECT frequency_hz,duty_cycle_percent
               FROM oscillator_samples
               WHERE session_id=? AND frequency_hz IS NOT NULL
               ORDER BY timestamp_ns""",
            (session_id,),
        ).fetchall()
        oscillator_frequencies = [float(row[0]) for row in oscillator_rows if row[0] is not None]
        duty_cycles = [float(row[1]) for row in oscillator_rows if row[1] is not None]
        return {
            "session": session,
            "controller_timestamps_ns": controller_timestamps,
            "oscillator_frequencies_hz": oscillator_frequencies,
            "duty_cycles_percent": duty_cycles,
        }

    def list_sessions(self, limit: int = 200) -> list[dict]:
        self.flush()
        rows = self.conn.execute(
            """SELECT s.id,s.created_utc,s.name,s.mode,s.app_version,
                      (SELECT COUNT(*) FROM controller_samples c WHERE c.session_id=s.id),
                      (SELECT COUNT(*) FROM oscillator_samples o WHERE o.session_id=s.id),
                      (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id)
               FROM sessions s
               ORDER BY s.created_utc DESC
               LIMIT ?""",
            (max(1, min(int(limit), 5000)),),
        ).fetchall()
        return [
            {
                "id": row[0], "created_utc": row[1], "name": row[2], "mode": row[3],
                "app_version": row[4], "controller_samples": row[5],
                "oscillator_samples": row[6], "events": row[7],
            }
            for row in rows
        ]

    def recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        self.flush()
        rows = self.conn.execute(
            """SELECT timestamp_ns,event_type,payload_json
               FROM events WHERE session_id=?
               ORDER BY id DESC LIMIT ?""",
            (session_id, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [
            {"timestamp_ns": row[0], "event_type": row[1], "payload": json.loads(row[2])}
            for row in reversed(rows)
        ]

    def list_events(self, session_id: str, limit: int = 250) -> list[dict]:
        return self.recent_events(session_id, limit)

    def close(self) -> None:
        self.flush()
        self.conn.close()
