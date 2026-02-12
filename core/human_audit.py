import sys
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse

# Add core to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import AppConfig, resolve_api_key, resolve_config_path
from core.storage.event_store import EventStore
from core.collectors.aw_collector import ActivityWatchCollector
from core.cleaner.data_cleaner import DataCleaner
from core.llm.llm_client import create_llm_client
from core.analysis.auditor import annotate_keywords
from core.analysis.baseline import build_baseline_keywords, STOPWORDS


# === CODEX slicing defaults ===
MAX_EVENT_SEGMENT_LINES = 600
MAX_EVENT_SEGMENT_EVENTS = 20
EVENT_SEGMENT_MAX_SECONDS = 30 * 60
PACK_GAP_SEC = 30

MAX_SLICE_LINES = 120
MIN_SLICE_LINES = 20
TIME_GAP_SPLIT_SEC = 120

TOP_SLICE_KEYWORDS = 10

SEGMENT_LLM_MIN_K = 5
SEGMENT_LLM_MAX_K = 10
SLICE_LLM_MIN_K = 3
SLICE_LLM_MAX_K = 8


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    if line.lower().startswith("export "):
                        line = line[7:].strip()
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if " #" in v:
                        v = v.split(" #", 1)[0].strip()
                    v = v.strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v


def load_local_events(days: int = 7) -> List[Any]:
    try:
        config_path = resolve_config_path("config.json")
        config = AppConfig.from_file(str(config_path))

        aw_db = os.environ.get("AW_DB_PATH")
        if not aw_db:
            candidates = [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "activitywatch", "activitywatch", "aw-server", "peewee-sqlite.v2.db"),
                os.path.join(os.environ.get("APPDATA", ""), "activitywatch", "activitywatch", "aw-server", "peewee-sqlite.v2.db"),
            ]
            for c in candidates:
                if os.path.exists(c):
                    aw_db = c
                    break

        if aw_db and os.path.exists(aw_db):
            print(f"[Init] Using ActivityWatch DB: {aw_db}")
            collector = ActivityWatchCollector(db_path=aw_db)
            return collector.collect(days=days)

        db_path = config.collector_db_path("local_events.db")
        print(f"[Init] Using Config DB: {db_path}")
        store = EventStore(db_path)
        return store.read_events(days=days)
    except Exception as e:
        print(f"[Error] Failed to load events: {e}")
        return []


def _event_end(ev: Any) -> datetime:
    return ev.timestamp + timedelta(seconds=max(0, int(ev.duration)))


def _clean_title(text: str) -> str:
    return DataCleaner.clean_title(text or "")


def _clean_url(url: str) -> str:
    return DataCleaner.clean_url(url or "")


def _event_signature(ev: Any) -> Tuple[str, str, str, str]:
    event_type = str(ev.event_type or "")
    app = _clean_title(ev.app or "")
    url = _clean_url(ev.url or "")
    title = _clean_title(ev.title or "")
    return (event_type, app, url, title)


def _event_to_dict(ev: Any) -> Dict[str, Any]:
    return {
        "event_type": ev.event_type,
        "url": ev.url,
        "title": ev.title,
        "app": ev.app,
        "status": getattr(ev, "status", ""),
        "duration": int(ev.duration),
        "timestamp": ev.timestamp.isoformat(),
    }


def _format_event_line(ev: Any) -> str:
    parts = [
        ev.timestamp.isoformat(),
        f"type={ev.event_type}",
        f"dur={int(ev.duration)}s",
    ]
    if ev.app:
        parts.append(f"app={_clean_title(ev.app)}")
    if ev.url:
        parts.append(f"url={_clean_url(ev.url)}")
    if ev.title:
        parts.append(f"title={_clean_title(ev.title)}")
    if getattr(ev, "status", ""):
        parts.append(f"status={getattr(ev, 'status', '')}")
    return " | ".join(parts)


def _build_event_packs(events: List[Any], gap_sec: int = PACK_GAP_SEC) -> List[Dict[str, Any]]:
    packs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    pack_idx = 1

    for ev in events:
        sig = _event_signature(ev)
        ev_start = ev.timestamp
        ev_end = _event_end(ev)

        if current:
            gap = (ev_start - current["end"]).total_seconds()
            if sig == current["signature"] and gap <= gap_sec:
                current["events"].append(ev)
                current["end"] = max(current["end"], ev_end)
                current["line_count"] += 1
                continue

        if current:
            packs.append(current)

        current = {
            "pack_id": f"PACK-{pack_idx:04d}",
            "signature": sig,
            "events": [ev],
            "start": ev_start,
            "end": ev_end,
            "line_count": 1,
        }
        pack_idx += 1

    if current:
        packs.append(current)

    return packs


def _build_event_segments(
    packs: List[Dict[str, Any]],
    max_lines: int = MAX_EVENT_SEGMENT_LINES,
    max_events: int = MAX_EVENT_SEGMENT_EVENTS,
    max_seconds: int = EVENT_SEGMENT_MAX_SECONDS,
) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    seg_idx = 1

    for pack in packs:
        if not current:
            current = {
                "segment_id": f"SEG-{seg_idx:03d}",
                "packs": [pack],
                "events": list(pack["events"]),
                "start": pack["start"],
                "end": pack["end"],
                "line_count": pack["line_count"],
            }
            continue

        new_lines = current["line_count"] + pack["line_count"]
        new_packs = len(current["packs"]) + 1
        new_duration = (max(current["end"], pack["end"]) - current["start"]).total_seconds()

        if new_lines > max_lines or new_packs > max_events or new_duration > max_seconds:
            segments.append(current)
            seg_idx += 1
            current = {
                "segment_id": f"SEG-{seg_idx:03d}",
                "packs": [pack],
                "events": list(pack["events"]),
                "start": pack["start"],
                "end": pack["end"],
                "line_count": pack["line_count"],
            }
            continue

        current["packs"].append(pack)
        current["events"].extend(pack["events"])
        current["end"] = max(current["end"], pack["end"])
        current["line_count"] = new_lines

    if current:
        segments.append(current)

    return segments


def _slice_key(ev: Any) -> Tuple[str, str, str]:
    source = str(ev.event_type or "")
    channel = ""
    tag = _clean_title(ev.title or "")
    if ev.event_type == "web":
        try:
            channel = urlparse(_clean_url(ev.url or "")).netloc.lower()
        except Exception:
            channel = "unknown"
    else:
        channel = _clean_title(ev.app or "") or "unknown"
    return (source, channel, tag)


def _build_slices(
    events: List[Any],
    max_lines: int = MAX_SLICE_LINES,
    min_lines: int = MIN_SLICE_LINES,
    gap_sec: int = TIME_GAP_SPLIT_SEC,
) -> List[Dict[str, Any]]:
    slices: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    slice_idx = 1

    for ev in events:
        key = _slice_key(ev)
        ev_start = ev.timestamp
        ev_end = _event_end(ev)

        if current:
            gap = (ev_start - current["end"]).total_seconds()
            if key == current["key"] and gap <= gap_sec and current["line_count"] < max_lines:
                current["events"].append(ev)
                current["end"] = max(current["end"], ev_end)
                current["line_count"] += 1
                continue

        if current:
            slices.append(current)

        current = {
            "slice_id": f"S-{slice_idx:03d}",
            "key": key,
            "source": key[0],
            "channel": key[1],
            "tag": key[2],
            "events": [ev],
            "start": ev_start,
            "end": ev_end,
            "line_count": 1,
        }
        slice_idx += 1

    if current:
        slices.append(current)

    if not slices:
        return slices

    merged: List[Dict[str, Any]] = []
    i = 0
    while i < len(slices):
        sl = slices[i]
        if sl["line_count"] >= min_lines:
            merged.append(sl)
            i += 1
            continue

        if merged:
            prev = merged[-1]
            prev["events"].extend(sl["events"])
            prev["end"] = max(prev["end"], sl["end"])
            prev["line_count"] += sl["line_count"]
            i += 1
            continue

        # First slice is too small: merge forward if possible
        if i + 1 < len(slices):
            nxt = slices[i + 1]
            sl["events"].extend(nxt["events"])
            sl["end"] = max(sl["end"], nxt["end"])
            sl["line_count"] += nxt["line_count"]
            slices[i + 1] = sl
            i += 1
            continue

        merged.append(sl)
        i += 1

    return merged


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    cur = []
    for ch in text:
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur).lower())
                cur = []
    if cur:
        out.append("".join(cur).lower())
    return [t for t in out if t and t not in STOPWORDS and len(t) > 1]


def _summarize_segment(compressed_data: Dict[str, Any]) -> Dict[str, Any]:
    top_apps: List[str] = []
    for app in (compressed_data.get("non_web_samples", {}) or {}).get("window", [])[:3]:
        name = app.get("app", "")
        dur = int(app.get("duration", 0))
        top_apps.append(f"{name} ({max(1, dur // 60)}m)")

    web_stats = compressed_data.get("web", {}) or {}
    web_items = []
    for domain, stats in web_stats.items():
        dur = int(stats.get("dur", {}).get("active_seconds", 0))
        web_items.append((domain, dur))
    web_items.sort(key=lambda x: (-x[1], x[0]))
    top_web = [f"{d} ({max(1, s // 60)}m)" for d, s in web_items[:3]]

    token_counts: Dict[str, int] = {}
    for stats in web_stats.values():
        for title, count in (stats.get("title_freq", {}) or {}).items():
            for t in _tokenize(title):
                token_counts[t] = token_counts.get(t, 0) + int(count or 1)

    for app in (compressed_data.get("non_web_samples", {}) or {}).get("window", []) or []:
        for title in app.get("titles", []) or []:
            for t in _tokenize(title):
                token_counts[t] = token_counts.get(t, 0) + 1

    tags = [k for k, _ in sorted(token_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]

    return {
        "top_apps": top_apps,
        "top_web": top_web,
        "tags": tags,
    }


def _flatten_llm_keywords(extracted: Any) -> List[Dict[str, Any]]:
    if isinstance(extracted, dict):
        items: List[Dict[str, Any]] = []
        for k in extracted.get("skills_interests", []) or []:
            k = dict(k)
            k["type"] = k.get("type") or "Skill (LLM)"
            items.append(k)
        for k in extracted.get("tools_platforms", []) or []:
            k = dict(k)
            k["type"] = k.get("type") or "Tool (LLM)"
            items.append(k)
        return items
    if isinstance(extracted, list):
        return [dict(x) for x in extracted]
    return []


def _extract_llm_items(
    llm_client: Any,
    compressed_data: Dict[str, Any],
    *,
    min_k: int,
    max_k: int,
    label: str,
) -> List[Dict[str, Any]]:
    if not llm_client:
        return []
    try:
        extracted = llm_client.extract_keywords(compressed_data, min_k=min_k, max_k=max_k)
        return _flatten_llm_keywords(extracted)
    except Exception as e:
        print(f"[LLM] Failed for {label}: {e}")
        return []


def _select_weight(item: Dict[str, Any]) -> float:
    scores = item.get("scores") or {}
    if isinstance(scores, dict) and "evidence" in scores:
        return float(scores.get("evidence") or 0.0)
    return float(item.get("weight") or 0.0)


def _trim_keywords(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    ranked = sorted(items, key=lambda x: (-_select_weight(x), str(x.get("name", "")).lower()))
    return ranked[:limit]


def _build_slice_keywords(
    llm_items: List[Dict[str, Any]],
    slice_compressed: Dict[str, Any],
    nlp_limit: int = TOP_SLICE_KEYWORDS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    llm_slice: List[Dict[str, Any]] = []
    if llm_items:
        llm_slice = annotate_keywords(llm_items, slice_compressed)
        llm_slice = _trim_keywords(llm_slice, nlp_limit)

    nlp_set = build_baseline_keywords(slice_compressed, limit=nlp_limit)
    nlp_items = [{"name": kw, "type": "NLP (Slice)"} for kw in nlp_set]
    nlp_slice = annotate_keywords(nlp_items, slice_compressed) if nlp_items else []
    nlp_slice = _trim_keywords(nlp_slice, nlp_limit)
    return llm_slice, nlp_slice


def _slice_llm_mode() -> str:
    v = str(os.environ.get("AUDIT_SLICE_LLM", "")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return "off"
    if v in ("1", "true", "yes", "on"):
        return "on"
    return "review"


def _get_score(prompt: str) -> str:
    while True:
        choice = input(f"{prompt} (0/1/2/s=skip/q=quit): ").strip().lower()
        if choice in ("0", "1", "2", "s", "q"):
            return choice


def run_audit_session():
    print("=== JobInsight Human-in-the-Loop Audit Session (CODEX) ===")
    load_env()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slice_llm_mode = _slice_llm_mode()
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(root_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    try:
        print("\n[Phase 1] Loading Events...")
        events = load_local_events(days=7)
        if not events:
            print("No events found. Exiting.")
            return

        events = sorted(events, key=lambda x: x.timestamp)
        packs = _build_event_packs(events, gap_sec=PACK_GAP_SEC)
        segments = _build_event_segments(
            packs,
            max_lines=MAX_EVENT_SEGMENT_LINES,
            max_events=MAX_EVENT_SEGMENT_EVENTS,
            max_seconds=EVENT_SEGMENT_MAX_SECONDS,
        )

        if not segments:
            print("No segments created. Exiting.")
            return

        print(f"Loaded {len(events)} events -> {len(packs)} packs -> {len(segments)} segments")

        print("\n[Phase 2] Preparing LLM Client...")
        llm_client = None
        try:
            config_path = resolve_config_path("config.json")
            config = AppConfig.from_file(str(config_path))
            llm_cfg = config.llm_config()

            provider = llm_cfg.get("provider", "zhipu")
            api_key = resolve_api_key(provider, llm_cfg.get("api_key", ""))

            llm_client = create_llm_client(
                provider,
                api_key,
                model=llm_cfg.get("model", "glm-4.7"),
                base_url=llm_cfg.get("base_url", ""),
            )
        except Exception as e:
            print(f"[Warning] LLM client unavailable: {e}")

        segments_out: List[Dict[str, Any]] = []
        segment_scores: List[Dict[str, Any]] = []
        slice_scores: List[Dict[str, Any]] = []

        print("\n[Phase 3] Segment Review & ABTest")
        for seg in segments:
            seg_id = seg["segment_id"]
            seg_events = seg["events"]
            compressed = DataCleaner.compress_data(seg_events)
            summary = _summarize_segment(compressed)

            print("\n" + "=" * 72)
            print(
                f"=== Segment: {seg['start'].isoformat()} - {seg['end'].isoformat()} "
                f"(Events: {len(seg_events)}, Lines: {seg['line_count']}) ==="
            )
            print(f"[Top Apps] {', '.join(summary['top_apps']) or 'N/A'}")
            print(f"[Top Web]  {', '.join(summary['top_web']) or 'N/A'}")
            print(f"[Tags]     {', '.join(summary['tags']) or 'N/A'}")

            llm_items: List[Dict[str, Any]] = []
            llm_items = _extract_llm_items(
                llm_client,
                compressed,
                min_k=SEGMENT_LLM_MIN_K,
                max_k=SEGMENT_LLM_MAX_K,
                label=seg_id,
            )

            nlp_set = build_baseline_keywords(compressed, limit=max(10, len(llm_items) + 5))
            nlp_items = [{"name": kw, "type": "NLP (Baseline)", "weight": 0.0} for kw in nlp_set]

            if llm_items:
                print("\n[LLM-only Output]")
                for item in _trim_keywords(llm_items, 10):
                    print(f"  - {item.get('name')} (w={item.get('weight', 0)})")
            else:
                print("\n[LLM-only Output] N/A")

            if nlp_items:
                print("\n[NLP-only Output]")
                for item in _trim_keywords(nlp_items, 10):
                    print(f"  - {item.get('name')} (w={item.get('weight', 0)})")
            else:
                print("\n[NLP-only Output] N/A")

            llm_score = _get_score("Segment score for LLM")
            if llm_score == "q":
                break
            nlp_score = _get_score("Segment score for NLP")
            if nlp_score == "q":
                break

            segment_scores.append(
                {
                    "segment_id": seg_id,
                    "llm_score": llm_score,
                    "nlp_score": nlp_score,
                }
            )

            slices = _build_slices(seg_events, max_lines=MAX_SLICE_LINES, min_lines=MIN_SLICE_LINES, gap_sec=TIME_GAP_SPLIT_SEC)
            slice_out: List[Dict[str, Any]] = []

            review_slices = input("Review slices for this segment? (y/n): ").strip().lower() == "y"
            slice_llm_enabled = bool(llm_client) and (slice_llm_mode == "on" or (slice_llm_mode == "review" and review_slices))

            for sl in slices:
                slice_events = sl["events"]
                slice_compressed = DataCleaner.compress_data(slice_events)
                slice_llm_items: List[Dict[str, Any]] = []
                if slice_llm_enabled:
                    slice_llm_items = _extract_llm_items(
                        llm_client,
                        slice_compressed,
                        min_k=SLICE_LLM_MIN_K,
                        max_k=SLICE_LLM_MAX_K,
                        label=f"{seg_id}/{sl['slice_id']}",
                    )
                llm_slice, nlp_slice = _build_slice_keywords(slice_llm_items, slice_compressed)

                raw_lines = [_format_event_line(ev) for ev in slice_events]
                slice_payload = {
                    "slice_id": sl["slice_id"],
                    "segment_id": seg_id,
                    "source": sl["source"],
                    "channel": sl["channel"],
                    "tag": sl["tag"],
                    "start": sl["start"].isoformat(),
                    "end": sl["end"].isoformat(),
                    "line_count": sl["line_count"],
                    "raw_lines": raw_lines,
                    "slice_llm_keywords": llm_slice,
                    "slice_nlp_keywords": nlp_slice,
                    "slice_keywords_llm": llm_slice,
                    "slice_keywords_nlp": nlp_slice,
                }
                slice_out.append(slice_payload)

                if review_slices:
                    print("\n-- Slice {0} (source={1}, {2}-{3}, {4} lines) --".format(
                        sl["slice_id"],
                        sl["source"],
                        sl["start"].isoformat(),
                        sl["end"].isoformat(),
                        sl["line_count"],
                    ))
                    for line in raw_lines:
                        print(f"  {line}")
                    print("[LLM Slice Keywords]")
                    for item in llm_slice:
                        print(f"  - {item.get('name')} (w={_select_weight(item):.3f})")
                    print("[NLP Slice Keywords]")
                    for item in nlp_slice:
                        print(f"  - {item.get('name')} (w={_select_weight(item):.3f})")

                    llm_s = _get_score("Slice score for LLM")
                    if llm_s == "q":
                        review_slices = False
                        break
                    nlp_s = _get_score("Slice score for NLP")
                    if nlp_s == "q":
                        review_slices = False
                        break
                    note = input("Slice note (empty to skip): ").strip()
                    slice_scores.append(
                        {
                            "segment_id": seg_id,
                            "slice_id": sl["slice_id"],
                            "llm_score": llm_s,
                            "nlp_score": nlp_s,
                            "note": note,
                        }
                    )

            segments_out.append(
                {
                    "segment_id": seg_id,
                    "start": seg["start"].isoformat(),
                    "end": seg["end"].isoformat(),
                    "event_pack_count": len(seg["packs"]),
                    "line_count": seg["line_count"],
                    "summary": summary,
                    "events": [_event_to_dict(ev) for ev in seg_events],
                    "slices": slice_out,
                    "slice_reviewed": review_slices,
                    "slice_llm_enabled": slice_llm_enabled,
                    "segment_llm_keywords": llm_items,
                    "segment_nlp_keywords": nlp_items,
                    "llm_output": llm_items,
                    "nlp_output": nlp_items,
                }
            )

        segment_path = os.path.join(log_dir, f"audit_segment_{timestamp}.json")
        with open(segment_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "meta": {
                        "timestamp": timestamp,
                        "segment_config": {
                            "max_event_segment_lines": MAX_EVENT_SEGMENT_LINES,
                            "max_event_segment_events": MAX_EVENT_SEGMENT_EVENTS,
                            "max_event_segment_seconds": EVENT_SEGMENT_MAX_SECONDS,
                            "pack_gap_sec": PACK_GAP_SEC,
                            "max_slice_lines": MAX_SLICE_LINES,
                            "min_slice_lines": MIN_SLICE_LINES,
                            "time_gap_split_sec": TIME_GAP_SPLIT_SEC,
                            "segment_llm_min_k": SEGMENT_LLM_MIN_K,
                            "segment_llm_max_k": SEGMENT_LLM_MAX_K,
                            "slice_llm_mode": slice_llm_mode,
                            "slice_llm_min_k": SLICE_LLM_MIN_K,
                            "slice_llm_max_k": SLICE_LLM_MAX_K,
                        },
                    },
                    "segments": segments_out,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        scores_path = os.path.join(log_dir, f"audit_scores_{timestamp}.json")
        with open(scores_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "meta": {
                        "timestamp": timestamp,
                        "segments_file": segment_path,
                    },
                    "segment_scores": segment_scores,
                    "slice_scores": slice_scores,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        print("\n[Done]")
        print(f"Segments saved to: {segment_path}")
        print(f"Scores saved to:   {scores_path}")

    except KeyboardInterrupt:
        print("\n[Audit] Session interrupted by user.")
        return
    except Exception as e:
        print(f"\n[Error] Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_audit_session()
