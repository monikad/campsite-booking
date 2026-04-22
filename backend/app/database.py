"""
SQLite database for alerts persistence.
Simple and portable — no Postgres needed for solo dev.
"""
import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "campsite.db"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            phone TEXT,
            facility_id TEXT,
            facility_name TEXT,
            reservation_url TEXT,
            location_query TEXT,
            latitude REAL,
            longitude REAL,
            max_drive_hours REAL DEFAULT 2.0,
            checkin TEXT NOT NULL,
            checkout TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_checked_at TEXT,
            last_notified_at TEXT,
            times_notified INTEGER DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS alert_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            facility_id TEXT NOT NULL,
            facility_name TEXT,
            available_sites INTEGER,
            sent_at TEXT DEFAULT (datetime('now')),
            channel TEXT DEFAULT 'email',
            FOREIGN KEY (alert_id) REFERENCES alerts(id)
        );
    """)
    conn.commit()
    conn.close()


# --- Alert CRUD ---

def create_alert(
    email: str,
    checkin: str,
    checkout: str,
    phone: Optional[str] = None,
    facility_id: Optional[str] = None,
    facility_name: Optional[str] = None,
    reservation_url: Optional[str] = None,
    location_query: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    max_drive_hours: float = 2.0,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO alerts 
           (email, phone, facility_id, facility_name, reservation_url, location_query, latitude, longitude, max_drive_hours, checkin, checkout)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (email, phone, facility_id, facility_name, reservation_url, location_query, latitude, longitude, max_drive_hours, checkin, checkout),
    )
    conn.commit()
    alert_id = cur.lastrowid
    conn.close()
    return alert_id


def get_active_alerts() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM alerts WHERE active = 1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_alerts_by_email(email: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM alerts WHERE email = ? ORDER BY created_at DESC", (email,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def deactivate_alert(alert_id: int):
    conn = get_db()
    conn.execute("UPDATE alerts SET active = 0 WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()


def mark_alert_checked(alert_id: int):
    conn = get_db()
    conn.execute("UPDATE alerts SET last_checked_at = datetime('now') WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()


def record_notification(alert_id: int, facility_id: str, facility_name: str, available_sites: int, channel: str = "email"):
    conn = get_db()
    conn.execute(
        """INSERT INTO alert_notifications (alert_id, facility_id, facility_name, available_sites, channel)
           VALUES (?, ?, ?, ?, ?)""",
        (alert_id, facility_id, facility_name, available_sites, channel),
    )
    conn.execute(
        "UPDATE alerts SET last_notified_at = datetime('now'), times_notified = times_notified + 1 WHERE id = ?",
        (alert_id,),
    )
    conn.commit()
    conn.close()
