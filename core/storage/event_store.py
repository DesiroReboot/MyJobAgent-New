import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class LocalEvent:
    event_type: str
    url: str
    title: str
    app: str
    status: str
    duration: int
    timestamp: datetime


class EventStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    url TEXT,
                    title TEXT,
                    app TEXT,
                    status TEXT,
                    duration INTEGER NOT NULL,
                    ts_start INTEGER NOT NULL,
                    ts_end INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, ts_start)")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _fingerprint(event_type: str, url: str, title: str, app: str, ts_start: int) -> str:
        raw = f"{event_type}|{url}|{title}|{app}|{ts_start}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def insert_events(self, events: Iterable[LocalEvent]) -> int:
        conn = sqlite3.connect(self.db_path)
        inserted = 0
        try:
            cursor = conn.cursor()
            for ev in events:
                ts_start = int(ev.timestamp.replace(tzinfo=timezone.utc).timestamp())
                ts_end = ts_start + max(0, int(ev.duration))
                fp = self._fingerprint(ev.event_type, ev.url, ev.title, ev.app, ts_start)
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO events
                        (event_type, url, title, app, status, duration, ts_start, ts_end, fingerprint)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ev.event_type,
                            ev.url,
                            ev.title,
                            ev.app,
                            ev.status,
                            int(ev.duration),
                            ts_start,
                            ts_end,
                            fp,
                        ),
                    )
                    if cursor.rowcount == 1:
                        inserted += 1
                except sqlite3.IntegrityError:
                    continue
            conn.commit()
        finally:
            conn.close()
        return inserted

    def purge_older_than(self, days: int) -> int:
        cutoff = int(datetime.now(timezone.utc).timestamp()) - days * 86400
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events WHERE ts_start < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def read_events(self, days: int) -> list[LocalEvent]:
        cutoff = int(datetime.now(timezone.utc).timestamp()) - days * 86400
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_type, url, title, app, status, duration, ts_start
                FROM events WHERE ts_start >= ?
                ORDER BY ts_start ASC
                """,
                (cutoff,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        events: list[LocalEvent] = []
        for event_type, url, title, app, status, duration, ts_start in rows:
            dt = datetime.fromtimestamp(int(ts_start), tz=timezone.utc)
            events.append(
                LocalEvent(
                    event_type=event_type,
                    url=url or "",
                    title=title or "",
                    app=app or "",
                    status=status or "",
                    duration=int(duration or 0),
                    timestamp=dt,
                )
            )
        return events

    def get_meta(self, key: str) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def set_meta(self, key: str, value: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
