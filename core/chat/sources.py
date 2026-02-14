from __future__ import annotations

from dataclasses import dataclass
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cherrystudio_api import CherryApiConfig, CherryStudioApiError, extract_sessions_via_api
from .cherrystudio import extract_sessions as extract_cherrystudio_sessions
from .ingest import ChatSession, ingest_chat_sessions, select_recent_session_files


@dataclass
class ChatSourceResult:
    source: Dict[str, Any]
    sessions: List[ChatSession]
    errors: List[str]
    debug: Optional[Dict[str, Any]] = None


def _dedupe_sessions(sessions: List[ChatSession]) -> List[ChatSession]:
    seen = set()
    out: List[ChatSession] = []
    for s in sessions:
        try:
            sid = s.to_dict().get("session_id", "")
        except Exception:
            sid = ""
        if not sid:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        out.append(s)
    return out


def _pick_latest_zip(directory: str) -> Optional[str]:
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return None
    zips = [p for p in root.rglob("*.zip") if p.is_file()]
    if not zips:
        return None
    zips.sort(key=lambda p: (p.stat().st_mtime, p.stat().st_size, str(p)))
    return str(zips[-1])


def _inspect_zip(path: str) -> Dict[str, Any]:
    info = {"db_files": 0, "json_files": 0, "md_files": 0, "txt_files": 0, "indexeddb_files": 0, "names_sample": []}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = [i.filename for i in zf.infolist() if not i.is_dir()]
            info["names_sample"] = names[:30]
            for n in names:
                lower = n.lower()
                if lower.endswith((".db", ".sqlite", ".sqlite3")):
                    info["db_files"] += 1
                elif lower.endswith(".json"):
                    info["json_files"] += 1
                elif lower.endswith(".md"):
                    info["md_files"] += 1
                elif lower.endswith(".txt"):
                    info["txt_files"] += 1
                if "indexeddb" in lower and lower.endswith((".ldb", ".log")):
                    info["indexeddb_files"] += 1
    except Exception as e:
        info["error"] = str(e)
    return info


def _extract_first_db_from_zip(zip_path: str, tmp_dir: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                lower = name.lower()
                if not lower.endswith((".db", ".sqlite", ".sqlite3")):
                    continue
                out = Path(tmp_dir) / Path(name).name
                out.write_bytes(zf.read(info))
                return str(out)
    except Exception:
        return None
    return None


def collect_chat_sessions(
    sources: List[Dict[str, Any]],
    days: int = 7,
    max_chars: int = 6000,
    debug: bool = False,
) -> Tuple[List[ChatSession], List[ChatSourceResult]]:
    all_sessions: List[ChatSession] = []
    results: List[ChatSourceResult] = []

    for src in sources or []:
        if not isinstance(src, dict):
            continue
        t = str(src.get("type", "") or "").strip().lower()
        domain = str(src.get("domain", "") or "").strip()
        errs: List[str] = []
        sessions: List[ChatSession] = []
        dbg: Optional[Dict[str, Any]] = None

        if t == "filesystem":
            p = str(src.get("path", "") or "").strip()
            if not p:
                errs.append("missing path")
            else:
                files = select_recent_session_files(p, days=int(days))
                if debug:
                    dbg = {"files": len(files), "path": p}
                for f in files:
                    d = domain or "chatshare.biz"
                    sessions.extend(ingest_chat_sessions(str(f), domain=d, max_chars=int(max_chars)))

        elif t == "obsidian":
            p = str(src.get("path", "") or "").strip()
            if not p:
                errs.append("missing path")
            else:
                files = select_recent_session_files(p, days=int(days))
                if debug:
                    dbg = {"files": len(files), "path": p}
                for f in files:
                    d = domain or "cherrystudio"
                    sessions.extend(ingest_chat_sessions(str(f), domain=d, max_chars=int(max_chars)))

        elif t == "cherrystudio":
            data_dir = str(src.get("data_dir", "") or "").strip()
            d = domain or "cherrystudio"
            mode = str(src.get("mode", "") or "auto").strip().lower()
            db_path = str(src.get("db_path", "") or "").strip()
            try:
                meta_errors: List[str] = []
                if mode in {"auto", "api"}:
                    try:
                        api_cfg = CherryApiConfig.from_env()
                        api_sessions, api_meta = extract_sessions_via_api(
                            cfg=api_cfg,
                            domain=d,
                            days=int(days),
                            max_chars=int(max_chars),
                            debug=debug,
                        )
                        sessions = api_sessions
                        if debug:
                            dbg = {"api": api_meta}
                    except Exception as e:
                        if debug:
                            dbg = dbg or {}
                            dbg["api_error"] = str(e)
                        meta_errors.append(f"api: {e}")
                if not sessions and mode in {"auto", "sqlite"}:
                    try:
                        sessions = extract_cherrystudio_sessions(
                            days=int(days),
                            domain=d,
                            data_dir=data_dir,
                            db_path=db_path,
                            max_chars=int(max_chars),
                        )
                        if debug:
                            dbg = dbg or {}
                            dbg["sqlite"] = {"data_dir": data_dir, "db_path": db_path}
                    except Exception as e:
                        meta_errors.append(f"sqlite: {e}")
                for me in meta_errors:
                    if debug:
                        errs.append(me)
            except Exception as e:
                errs.append(str(e))

        elif t == "cherrystudio_backup":
            p = str(src.get("path", "") or "").strip()
            if not p:
                errs.append("missing path")
            else:
                chosen = p
                if Path(p).is_dir():
                    chosen = _pick_latest_zip(p) or ""
                    if debug:
                        dbg = {"backup_dir": p, "chosen_zip": chosen}
                if not chosen:
                    errs.append("no backup zip found")
                else:
                    d = domain or "cherrystudio"
                    sessions = ingest_chat_sessions(chosen, domain=d, max_chars=int(max_chars))
                    if not sessions:
                        zinfo = _inspect_zip(chosen)
                        if debug:
                            dbg = dbg or {}
                            dbg["zip"] = zinfo
                        if zinfo.get("db_files", 0):
                            with tempfile.TemporaryDirectory(prefix="cherry_backup_") as td:
                                dbp = _extract_first_db_from_zip(chosen, td)
                                if dbp:
                                    try:
                                        sessions = extract_cherrystudio_sessions(
                                            days=int(days),
                                            domain=d,
                                            db_path=dbp,
                                            data_dir="",
                                            max_chars=int(max_chars),
                                        )
                                    except Exception as e:
                                        errs.append(f"sqlite: {e}")
                        elif zinfo.get("indexeddb_files", 0):
                            errs.append("backup contains IndexedDB leveldb files; not supported for parsing")
                        else:
                            errs.append("backup zip has no readable json/md/txt payloads")

        else:
            errs.append(f"unsupported type: {t or '(empty)'}")

        sessions = _dedupe_sessions(sessions)
        all_sessions.extend(sessions)
        results.append(ChatSourceResult(source=src, sessions=sessions, errors=errs, debug=dbg))

    all_sessions = _dedupe_sessions(all_sessions)
    return all_sessions, results
