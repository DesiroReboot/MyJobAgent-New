import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

try:
    import psutil
except Exception:
    psutil = None

try:
    from pycaw.pycaw import AudioUtilities
except Exception:
    AudioUtilities = None

import ctypes
from ctypes import wintypes

from storage.event_store import EventStore, LocalEvent


@dataclass
class WindowSample:
    title: str
    app: str


def _get_foreground_window_info() -> WindowSample:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return WindowSample(title="", app="")

    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    app_name = ""
    if psutil:
        try:
            app_name = psutil.Process(pid.value).name()
        except Exception:
            app_name = ""
    else:
        process_handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        if process_handle:
            buf = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleBaseNameW(process_handle, None, buf, 260):
                app_name = buf.value
            kernel32.CloseHandle(process_handle)

    return WindowSample(title=title.value, app=app_name)


def _get_idle_seconds() -> int:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    tick_count = kernel32.GetTickCount()
    elapsed = tick_count - lii.dwTime
    return int(elapsed / 1000)


def _get_audio_samples() -> List[WindowSample]:
    if not AudioUtilities:
        return []
    samples: List[WindowSample] = []
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return samples

    for session in sessions:
        try:
            if not session.Process:
                continue
            if session.State != 1:
                continue
            app = session.Process.name()
            title = session.DisplayName or session.Process.name()
            samples.append(WindowSample(title=title or "", app=app or ""))
        except Exception:
            continue

    return samples


def _default_browser_paths() -> Dict[str, List[str]]:
    local_app = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")

    chrome_root = os.path.join(local_app, "Google", "Chrome", "User Data")
    edge_root = os.path.join(local_app, "Microsoft", "Edge", "User Data")

    return {
        "chrome": _discover_chromium_profiles(chrome_root, "History"),
        "edge": _discover_chromium_profiles(edge_root, "History"),
        "firefox": _discover_firefox_profiles(appdata),
    }


def list_browser_paths() -> Dict[str, List[str]]:
    return _default_browser_paths()


def _discover_chromium_profiles(root: str, db_name: str) -> List[str]:
    if not root or not os.path.exists(root):
        return []
    local_state = os.path.join(root, "Local State")
    profiles = []
    if os.path.exists(local_state):
        try:
            with open(local_state, "r", encoding="utf-8") as f:
                data = json.load(f)
            info = data.get("profile", {}).get("info_cache", {})
            for profile in info.keys():
                path = os.path.join(root, profile, db_name)
                if os.path.exists(path):
                    profiles.append(path)
        except Exception:
            pass

    if not profiles:
        defaults = ["Default", "Profile 1", "Profile 2", "Profile 3"]
        for name in defaults:
            path = os.path.join(root, name, db_name)
            if os.path.exists(path):
                profiles.append(path)

    return profiles


def _discover_firefox_profiles(appdata: str) -> List[str]:
    profiles = []
    profiles_ini = os.path.join(appdata, "Mozilla", "Firefox", "profiles.ini")
    if not os.path.exists(profiles_ini):
        return profiles

    current = None
    with open(profiles_ini, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[Profile"):
                current = {}
            elif line.startswith("Path=") and current is not None:
                current["path"] = line.split("=", 1)[1].strip()
            elif line.startswith("Default=") and current is not None:
                current["default"] = line.split("=", 1)[1].strip()
            elif line == "" and current and current.get("path"):
                profiles.append(current)
                current = None
        if current and current.get("path"):
            profiles.append(current)

    base = os.path.join(appdata, "Mozilla", "Firefox")
    dbs = []
    for prof in profiles:
        path = prof["path"]
        if not path:
            continue
        full = path if os.path.isabs(path) else os.path.join(base, path)
        db = os.path.join(full, "places.sqlite")
        if os.path.exists(db):
            dbs.append(db)

    return dbs


def _copy_db(src: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(src, tmp.name)
    return tmp.name


def _read_chromium_history(db_path: str, since: datetime) -> Iterable[LocalEvent]:
    tmp_db = _copy_db(db_path)
    try:
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        threshold = int((since - chrome_epoch).total_seconds() * 1_000_000)
        cursor.execute(
            """
            SELECT url, title, last_visit_time
            FROM urls
            WHERE last_visit_time >= ? AND url IS NOT NULL AND url != ''
            """,
            (threshold,),
        )
        for url, title, last_visit_time in cursor.fetchall():
            ts = chrome_epoch + timedelta(microseconds=int(last_visit_time or 0))
            yield LocalEvent(
                event_type="web",
                url=url or "",
                title=title or "",
                app="",
                status="",
                duration=0,
                timestamp=ts,
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            os.remove(tmp_db)
        except Exception:
            pass


def _read_firefox_history(db_path: str, since: datetime) -> Iterable[LocalEvent]:
    tmp_db = _copy_db(db_path)
    try:
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        threshold = int(since.timestamp() * 1_000_000)
        cursor.execute(
            """
            SELECT p.url, p.title, v.visit_date
            FROM moz_places p
            JOIN moz_historyvisits v ON v.place_id = p.id
            WHERE v.visit_date >= ? AND p.url IS NOT NULL AND p.url != ''
            """,
            (threshold,),
        )
        for url, title, visit_date in cursor.fetchall():
            ts = datetime.fromtimestamp(int(visit_date) / 1_000_000, tz=timezone.utc)
            yield LocalEvent(
                event_type="web",
                url=url or "",
                title=title or "",
                app="",
                status="",
                duration=0,
                timestamp=ts,
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            os.remove(tmp_db)
        except Exception:
            pass


def _meta_key(browser: str, db_path: str) -> str:
    return f"browser::{browser}::{db_path}"


def ingest_browser_history(store: EventStore, days: int, browsers: List[str]) -> int:
    base_since = datetime.now(timezone.utc) - timedelta(days=days)
    paths = _default_browser_paths()
    inserted = 0

    for browser in browsers:
        key = browser.lower().strip()
        dbs = paths.get(key, [])
        for db_path in dbs:
            last_ts = store.get_meta(_meta_key(key, db_path))
            if last_ts:
                try:
                    since = datetime.fromtimestamp(int(last_ts), tz=timezone.utc)
                    if since < base_since:
                        since = base_since
                except Exception:
                    since = base_since
            else:
                since = base_since

            if key in {"chrome", "edge"}:
                inserted += store.insert_events(_read_chromium_history(db_path, since))
            elif key == "firefox":
                inserted += store.insert_events(_read_firefox_history(db_path, since))

            store.set_meta(_meta_key(key, db_path), str(int(datetime.now(timezone.utc).timestamp())))

    return inserted


def sample_and_store(store: EventStore, sample_interval: int, idle_threshold: int) -> int:
    now = datetime.now(timezone.utc)
    samples: List[LocalEvent] = []

    idle_seconds = _get_idle_seconds()
    status = "afk" if idle_seconds >= idle_threshold else "active"

    samples.append(
        LocalEvent(
            event_type="afk",
            url="",
            title="",
            app="",
            status=status,
            duration=sample_interval,
            timestamp=now,
        )
    )

    win = _get_foreground_window_info()
    if win.title or win.app:
        samples.append(
            LocalEvent(
                event_type="window",
                url="",
                title=win.title,
                app=win.app,
                status="",
                duration=sample_interval,
                timestamp=now,
            )
        )

    for audio in _get_audio_samples():
        if audio.title or audio.app:
            samples.append(
                LocalEvent(
                    event_type="audio",
                    url="",
                    title=audio.title,
                    app=audio.app,
                    status="",
                    duration=sample_interval,
                    timestamp=now,
                )
            )

    return store.insert_events(samples)
