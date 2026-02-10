import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional


@dataclass
class ActivityWatchRecord:
    event_type: str
    url: str
    title: str
    app: str
    duration: int
    timestamp: datetime
    status: str


class ActivityWatchCollector:
    def __init__(self, db_path: Optional[str] = None, buckets: Optional[List[str]] = None):
        self.db_path = db_path or self._default_db_path()
        self.buckets = buckets or ["web", "window", "audio", "afk"]

    @staticmethod
    def _default_db_candidates() -> List[str]:
        local_app = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        return [
            os.path.join(local_app, "ActivityWatch", "activitywatch.db"),
            os.path.join(appdata, "ActivityWatch", "activitywatch.db"),
            os.path.join(os.path.expanduser("~"), ".activitywatch", "activitywatch.db"),
            os.path.join(local_app, "activitywatch", "activitywatch", "aw-server", "peewee-sqlite.v2.db"),
            os.path.join(appdata, "activitywatch", "activitywatch", "aw-server", "peewee-sqlite.v2.db"),
        ]

    @staticmethod
    def _default_db_path() -> Optional[str]:
        candidates = ActivityWatchCollector._default_db_candidates()
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    @staticmethod
    def _parse_iso(ts: str) -> datetime:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)

    @staticmethod
    def _bucket_patterns(buckets: List[str]) -> List[str]:
        mapping = {
            "web": "aw-watcher-web%",
            "window": "aw-watcher-window%",
            "audio": "aw-watcher-audio%",
            "afk": "aw-watcher-afk%",
        }
        patterns = []
        for name in buckets:
            key = str(name).lower().strip()
            if key in mapping:
                patterns.append(mapping[key])
        return patterns

    def collect(self, days: int = 7) -> List[ActivityWatchRecord]:
        if not self.db_path or not os.path.exists(self.db_path):
            raise FileNotFoundError("ActivityWatch DB not found. Set collector.aw_db_path in config.json")

        patterns = self._bucket_patterns(self.buckets)
        if not patterns:
            raise ValueError("No ActivityWatch buckets enabled")

        threshold_dt = datetime.now(timezone.utc) - timedelta(days=days)
        records: List[ActivityWatchRecord] = []

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            def has_table(name: str) -> bool:
                cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                    (name,),
                )
                return cursor.fetchone() is not None

            if has_table("buckets") and has_table("events"):
                where_clause = " OR ".join(["b.bucket_id LIKE ?" for _ in patterns])
                query = (
                    "SELECT b.bucket_id, e.timestamp, e.duration, e.data "
                    "FROM events e JOIN buckets b ON e.bucket_id = b.id "
                    f"WHERE {where_clause}"
                )
                cursor.execute(query, patterns)
            elif has_table("bucketmodel") and has_table("eventmodel"):
                where_clause = " OR ".join(["b.id LIKE ?" for _ in patterns])
                query = (
                    "SELECT b.id, e.timestamp, e.duration, e.datastr "
                    "FROM eventmodel e JOIN bucketmodel b ON e.bucket_id = b.key "
                    f"WHERE {where_clause}"
                )
                cursor.execute(query, patterns)
            else:
                raise FileNotFoundError("Unsupported ActivityWatch DB schema")

            for bucket_id, ts, duration, data_json in cursor.fetchall():
                try:
                    dt = self._parse_iso(ts)
                except Exception:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                if dt < threshold_dt:
                    continue

                try:
                    data = json.loads(data_json)
                except Exception:
                    data = {}

                bucket_id = bucket_id or ""
                event_type = "unknown"
                if bucket_id.startswith("aw-watcher-web"):
                    event_type = "web"
                elif bucket_id.startswith("aw-watcher-window"):
                    event_type = "window"
                elif bucket_id.startswith("aw-watcher-audio"):
                    event_type = "audio"
                elif bucket_id.startswith("aw-watcher-afk"):
                    event_type = "afk"

                url = data.get("url") or ""
                title = data.get("title") or ""
                app = data.get("app") or data.get("app_name") or data.get("application") or ""
                status = data.get("status") or ""

                if event_type == "web" and not url:
                    continue
                if event_type in {"window", "audio"} and not (title or app):
                    continue

                records.append(
                    ActivityWatchRecord(
                        event_type=event_type,
                        url=url,
                        title=title,
                        app=app,
                        duration=int(float(duration or 0)),
                        timestamp=dt,
                        status=status,
                    )
                )
        finally:
            conn.close()

        return records
