"""
BMW Dashboard — Datenbankschicht (SQLite)
Speichert alle abgerufenen Fahrzeugdaten lokal.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path("bmw_data.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            key       TEXT NOT NULL,
            value     TEXT,
            unit      TEXT,
            timestamp TEXT,
            fetched   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_telemetry_key ON telemetry(key);
        CREATE INDEX IF NOT EXISTS idx_telemetry_ts  ON telemetry(timestamp);

        CREATE TABLE IF NOT EXISTS charging_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time      INTEGER,
            end_time        INTEGER,
            soc_start       INTEGER,
            soc_end         INTEGER,
            energy_kwh      REAL,
            duration_sec    INTEGER,
            max_power_kw    REAL,
            address         TEXT,
            municipality    TEXT,
            lat             REAL,
            lon             REAL,
            mileage         INTEGER,
            fetched         TEXT DEFAULT (datetime('now')),
            UNIQUE(start_time, end_time)
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            data      TEXT NOT NULL,
            fetched   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tokens (
            id            INTEGER PRIMARY KEY,
            access_token  TEXT,
            refresh_token TEXT,
            id_token      TEXT,
            expires_in    INTEGER,
            saved_at      REAL
        );
    """)
    conn.commit()
    conn.close()


def save_telemetry(key: str, value: str, unit: str, timestamp: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO telemetry (key, value, unit, timestamp) VALUES (?,?,?,?)",
        (key, value, unit, timestamp)
    )
    conn.commit()
    conn.close()


def get_latest_telemetry(key: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM telemetry WHERE key=? ORDER BY fetched DESC LIMIT 1", (key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_telemetry_history(key: str, limit: int = 200) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM telemetry WHERE key=? ORDER BY fetched DESC LIMIT ?", (key, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_charging_sessions(sessions: list):
    conn = get_db()
    inserted = 0
    for s in sessions:
        blocks = s.get("chargingBlocks", [])
        max_kw = max((b.get("averagePowerGridKw", 0) for b in blocks), default=0)
        loc = s.get("chargingLocation", {})
        try:
            conn.execute("""
                INSERT OR IGNORE INTO charging_sessions
                (start_time, end_time, soc_start, soc_end, energy_kwh,
                 duration_sec, max_power_kw, address, municipality, lat, lon, mileage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s.get("startTime"), s.get("endTime"),
                s.get("displayedStartSoc"), s.get("displayedSoc"),
                s.get("energyConsumedFromPowerGridKwh", 0),
                s.get("totalChargingDurationSec", 0),
                round(max_kw, 2),
                loc.get("formattedAddress", ""),
                loc.get("municipality", ""),
                loc.get("mapMatchedLatitude"),
                loc.get("mapMatchedLongitude"),
                s.get("mileage", 0),
            ))
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            pass
    conn.commit()
    conn.close()
    return inserted


def get_charging_sessions(limit: int = 100, offset: int = 0) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM charging_sessions ORDER BY start_time DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_charging_stats() -> dict:
    conn = get_db()
    stats = conn.execute("""
        SELECT
            COUNT(*)            as total_sessions,
            SUM(energy_kwh)     as total_kwh,
            AVG(energy_kwh)     as avg_kwh,
            AVG(duration_sec)/60 as avg_duration_min,
            AVG(soc_start)      as avg_soc_start,
            MAX(mileage)        as max_mileage,
            MIN(mileage)        as min_mileage
        FROM charging_sessions WHERE energy_kwh > 0
    """).fetchone()
    monthly = conn.execute("""
        SELECT
            strftime('%Y-%m', datetime(start_time, 'unixepoch')) as month,
            COUNT(*) as sessions,
            ROUND(SUM(energy_kwh),1) as kwh
        FROM charging_sessions WHERE energy_kwh > 0
        GROUP BY month ORDER BY month DESC LIMIT 12
    """).fetchall()
    conn.close()
    return {
        "totals": dict(stats) if stats else {},
        "monthly": [dict(r) for r in monthly]
    }


def save_snapshot(data: dict):
    conn = get_db()
    conn.execute("INSERT INTO snapshots (data) VALUES (?)", (json.dumps(data),))
    conn.commit()
    conn.close()


def get_latest_snapshot() -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM snapshots ORDER BY fetched DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d
    return None


def save_tokens_db(tokens: dict):
    conn = get_db()
    conn.execute("DELETE FROM tokens")
    conn.execute("""
        INSERT INTO tokens (id, access_token, refresh_token, id_token, expires_in, saved_at)
        VALUES (1,?,?,?,?,?)
    """, (tokens.get("access_token"), tokens.get("refresh_token"),
          tokens.get("id_token"), tokens.get("expires_in", 3600),
          tokens.get("saved_at", 0)))
    conn.commit()
    conn.close()


def load_tokens_db() -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM tokens WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None
