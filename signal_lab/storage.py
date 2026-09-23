"""SQLite persistence and raw-data exports."""
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


class LabDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def create_session(self, name: str, mode: str, app_version: str, metadata: dict | None = None) -> str:
        session_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions(id,created_utc,name,mode,app_version,metadata_json) VALUES(?,?,?,?,?,?)",
            (session_id, datetime.now(timezone.utc).isoformat(), name, mode, app_version, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return session_id

    def add_controller_sample(self, session_id: str, timestamp_ns: int, sample: dict, *, source: str, raw_report_hex: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO controller_samples(session_id,timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (session_id, int(timestamp_ns), source, sample.get("lx"), sample.get("ly"), sample.get("rx"), sample.get("ry"), sample.get("lt"), sample.get("rt"), raw_report_hex),
        )

    def add_oscillator_sample(self, session_id: str, timestamp_ns: int, frequency_hz: float, *, source: str, duty_cycle_percent: float | None = None, quality: str = "measured") -> None:
        self.conn.execute(
            """INSERT INTO oscillator_samples(session_id,timestamp_ns,source,frequency_hz,duty_cycle_percent,period_s,quality)
               VALUES(?,?,?,?,?,?,?)""",
            (session_id, int(timestamp_ns), source, float(frequency_hz), duty_cycle_percent, (1.0 / float(frequency_hz)) if frequency_hz else None, quality),
        )

    def add_event(self, session_id: str, timestamp_ns: int, event_type: str, payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(session_id,timestamp_ns,event_type,payload_json) VALUES(?,?,?,?)",
            (session_id, int(timestamp_ns), event_type, json.dumps(payload or {})),
        )

    def flush(self) -> None:
        self.conn.commit()

    def session_summary(self, session_id: str) -> dict:
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
        destination = Path(destination)
        payload = {"session": self.session_summary(session_id)}
        payload["controller_samples"] = [
            dict(zip(["timestamp_ns","source","lx","ly","rx","ry","lt","rt","raw_report_hex"], row))
            for row in self.conn.execute("SELECT timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex FROM controller_samples WHERE session_id=? ORDER BY timestamp_ns", (session_id,))
        ]
        payload["oscillator_samples"] = [
            dict(zip(["timestamp_ns","source","frequency_hz","duty_cycle_percent","period_s","quality"], row))
            for row in self.conn.execute("SELECT timestamp_ns,source,frequency_hz,duty_cycle_percent,period_s,quality FROM oscillator_samples WHERE session_id=? ORDER BY timestamp_ns", (session_id,))
        ]
        payload["events"] = [
            {"timestamp_ns": row[0], "event_type": row[1], "payload": json.loads(row[2])}
            for row in self.conn.execute("SELECT timestamp_ns,event_type,payload_json FROM events WHERE session_id=? ORDER BY timestamp_ns", (session_id,))
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    def export_controller_csv(self, session_id: str, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute("SELECT timestamp_ns,source,lx,ly,rx,ry,lt,rt,raw_report_hex FROM controller_samples WHERE session_id=? ORDER BY timestamp_ns", (session_id,))
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_ns","source","lx","ly","rx","ry","lt","rt","raw_report_hex"])
            writer.writerows(rows)
        return destination

    def recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """SELECT timestamp_ns,event_type,payload_json
               FROM events WHERE session_id=?
               ORDER BY id DESC LIMIT ?""",
            (session_id, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [
            {
                "timestamp_ns": row[0],
                "event_type": row[1],
                "payload": json.loads(row[2]),
            }
            for row in reversed(rows)
        ]

    def list_events(self, session_id: str, limit: int = 250) -> list[dict]:
        return self.recent_events(session_id, limit)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
