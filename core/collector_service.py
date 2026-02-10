import argparse
import time
from datetime import datetime

from collectors.local_collector import ingest_browser_history, sample_and_store, list_browser_paths
from config import AppConfig, resolve_config_path
from storage.event_store import EventStore


def _log(msg: str, log_file: str | None, quiet: bool) -> None:
    if not quiet:
        print(msg)
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def run_collector(
    db_path: str,
    days: int,
    sample_interval: int,
    idle_threshold: int,
    browsers: list[str],
    log_file: str | None,
    quiet: bool,
) -> None:
    store = EventStore(db_path)
    _log(f"[Collector] DB: {db_path}", log_file, quiet)

    purged = store.purge_older_than(days)
    if purged:
        _log(f"[Collector] Purged {purged} old events", log_file, quiet)

    browser_paths = list_browser_paths()
    for name, paths in browser_paths.items():
        _log(f"[Collector] {name} history DBs: {len(paths)}", log_file, quiet)
        for p in paths[:5]:
            _log(f"[Collector]   {p}", log_file, quiet)
        if len(paths) > 5:
            _log(f"[Collector]   ... ({len(paths) - 5} more)", log_file, quiet)

    _log("[Collector] Ingesting browser history...", log_file, quiet)
    inserted = ingest_browser_history(store, days, browsers)
    _log(f"[Collector] Browser history inserted: {inserted}", log_file, quiet)

    _log(f"[Collector] Sampling every {sample_interval}s (Ctrl+C to stop)", log_file, quiet)
    try:
        while True:
            count = sample_and_store(store, sample_interval, idle_threshold)
            now = datetime.now().strftime("%H:%M:%S")
            _log(f"[{now}] samples inserted: {count}", log_file, quiet)
            time.sleep(sample_interval)
    except KeyboardInterrupt:
        _log("[Collector] Stopped", log_file, quiet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JobInsight local collector service")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    parser.add_argument("--daemon", action="store_true", help="Run quietly and log to file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    config = AppConfig.from_file(str(config_path))

    db_path = config.collector_db_path("local_events.db")
    days = config.collector_days(7)
    sample_interval = config.collector_sample_interval(30)
    idle_threshold = config.collector_idle_threshold(300)
    browsers = config.collector_browsers()
    log_file = config.collector_log_file(args.daemon, "collector.log")

    run_collector(db_path, days, sample_interval, idle_threshold, browsers, log_file, args.daemon)
