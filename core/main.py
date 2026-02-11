import argparse
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from cleaner.data_cleaner import DataCleaner
from config import AppConfig, resolve_config_path
from collectors.aw_collector import ActivityWatchCollector
from llm.llm_client import create_llm_client
from analysis.auditor import annotate_keywords
from visualization.wordcloud import WordCloudGenerator
from pusher.feishu_pusher import FeishuPusher
from storage.event_store import EventStore


def load_env() -> None:
    if load_dotenv:
        load_dotenv()
        return

    # Fallback: minimal .env loader to avoid hardcoding keys
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def resolve_api_key(provider: str, configured_key: str) -> str:
    if configured_key:
        return configured_key

    provider = (provider or "").lower()
    if provider == "zhipu":
        return (
            os.environ.get("ZHIPU_API_KEY")
            or os.environ.get("GLM_API_KEY")
            or os.environ.get("BIGMODEL_API_KEY")
            or ""
        )
    if provider == "doubao":
        return os.environ.get("VOLCANO_API_KEY") or os.environ.get("ARK_API_KEY") or ""
    if provider in {"openai", "openai_compat"}:
        return os.environ.get("OPENAI_API_KEY") or ""
    if provider == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY") or ""
    if provider == "dashscope":
        return os.environ.get("DASHSCOPE_API_KEY") or ""
    return ""


def ensure_output_path(path_str: str) -> str:
    path = Path(path_str)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


import time
import datetime
from typing import Optional

def run_analysis(config: AppConfig, days: int) -> int:
    """Run the analysis pipeline once."""
    print(f"[JobInsight] Starting analysis for past {days} days...")
    
    source = config.collector_source("local")
    events = []
    if source == "activitywatch":
        aw_db_path = config.collector_aw_db_path("")
        collector = ActivityWatchCollector(db_path=aw_db_path or None)
        if not collector.db_path:
            print("[Step1] ActivityWatch DB not found. Install and run ActivityWatch first, then set collector.aw_db_path.")
            print("[Step1] Expected default locations:")
            for path in ActivityWatchCollector._default_db_candidates():
                if path:
                    print(f"[Step1]   {path}")
            return 1
        print(f"[Step1] Loading ActivityWatch events from: {collector.db_path}")
        try:
            events = collector.collect(days=days)
        except FileNotFoundError:
            print("[Step1] ActivityWatch DB not found. Set collector.aw_db_path in config.json.")
            return 1
    else:
        db_path = config.collector_db_path("local_events.db")
        print(f"[Step1] Loading local events from: {db_path}")
        store = EventStore(db_path)
        events = store.read_events(days=days)

    print(f"[Step1] Events loaded: {len(events)}")
    if not events:
        if source == "activitywatch":
            print("[Step1] No ActivityWatch events found. Ensure ActivityWatch is running and has recent data.")
        else:
            print("[Step1] No events found. Run collector_service.py to collect data first.")
        return 1

    compressed_data = DataCleaner.compress_data(events)
    web_domains = len(compressed_data.get("web", {}))
    print(f"[Step1] Domains after compression: {web_domains}")

    llm_cfg = config.llm_config()
    provider = llm_cfg.get("provider", "zhipu")
    
    # Updated: use config.get_env to support config.json fallback
    api_key_env = resolve_api_key(provider, llm_cfg.get("api_key", ""))
    # If resolve_api_key returns empty (not in env), try getting from config via get_env logic if applicable
    # Actually resolve_api_key checks specific env vars. We should update resolve_api_key or call get_env first.
    # Let's update resolve_api_key to use config.get_env if we can pass config to it, or just do it here.
    # For now, let's assume resolve_api_key logic handles standard envs. 
    # But user wants generic config.json fallback.
    # Let's fetch the key name based on provider and ask config.get_env
    
    key_map = {
        "zhipu": "ZHIPU_API_KEY",
        "doubao": "VOLCANO_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai_compat": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY"
    }
    env_key = key_map.get(provider, "")
    api_key = config.get_env(env_key) if env_key else ""
    if not api_key:
        api_key = resolve_api_key(provider, llm_cfg.get("api_key", "")) # Fallback to old logic or empty

    llm_client = create_llm_client(
        provider=provider,
        api_key=api_key,
        model=llm_cfg.get("model", "glm-4.7"),
        timeout=llm_cfg.get("timeout", 60),
        base_url=llm_cfg.get("base_url", ""),
    )

    min_k = int(llm_cfg.get("keyword_min", 5))
    # Adjust max_k based on days
    default_max = int(llm_cfg.get("keyword_max", 20))
    if days <= 1:
        max_k = 5
    else:
        max_k = 10
    
    # Override if config explicitly forces something else? 
    # User requested: 1-day -> 5, 7-day -> 10.
    # We will respect this rule over config.json's keyword_max for now, or use it as a cap.

    try:
        self_consistency_runs = int(llm_cfg.get("self_consistency_runs", 1) or 1)
        runs = []
        if self_consistency_runs <= 1:
            keywords = llm_client.extract_keywords(compressed_data, min_k=min_k, max_k=max_k)
        else:
            keywords = None
            for _ in range(self_consistency_runs):
                run_keywords = llm_client.extract_keywords(compressed_data, min_k=min_k, max_k=max_k)
                runs.append(run_keywords)
            # Use the last run as primary, but compute consistency across runs
            keywords = runs[-1] if runs else None
    except Exception as e:
        print(f"[Step1] LLM call failed: {e}")
        # In scheduler mode, we might not want to raise, just log and return
        return 1
    def _count_keywords(payload) -> int:
        if isinstance(payload, dict):
            total = 0
            for key in ("skills_interests", "tools_platforms"):
                total += len(payload.get(key, []) or [])
            return total
        if isinstance(payload, list):
            return len(payload)
        return 0

    print(f"[Step1] Keywords extracted: {_count_keywords(keywords)}")

    # Annotate keywords with evidence/scores/level (2A)
    consistency_runs = []
    if isinstance(runs, list) and runs:
        for item in runs:
            if isinstance(item, dict):
                names = []
                for key in ("skills_interests", "tools_platforms"):
                    for kw in item.get(key, []) or []:
                        name = str(kw.get("name", "")).strip()
                        if name:
                            names.append(name)
                consistency_runs.append(names)
            elif isinstance(item, list):
                names = []
                for kw in item:
                    name = str(kw.get("name", "")).strip()
                    if name:
                        names.append(name)
                consistency_runs.append(names)

    keywords = annotate_keywords(keywords, compressed_data, consistency_runs=consistency_runs)

    output_cfg = config.output_config()
    base_wc_file = output_cfg.get("wordcloud_file", "wordcloud.html")
    # Append days to filename to distinguish reports
    name_part, ext_part = os.path.splitext(base_wc_file)
    wordcloud_file = ensure_output_path(f"{name_part}_{days}d{ext_part}")

    wc_generator = WordCloudGenerator()
    wc_generator.generate(keywords, wordcloud_file)
    print(f"[Step1] Wordcloud generated: {wordcloud_file}")

    feishu_account, webhook_url = config.feishu_webhook()
    
    # 优先使用配置的webhook
    if webhook_url:
        pusher = FeishuPusher(mode="bot", webhook_url=webhook_url)
        pusher.push_keywords(keywords, title_suffix=f" (Past {days} Days)")
        if feishu_account:
            print(f"[Step1] Feishu push sent (account: {feishu_account})")
        else:
            print("[Step1] Feishu push sent via Webhook")
    else:
        # 尝试使用App模式
        app_id = config.get_env("FEISHU_APP_ID")
        if app_id:
            email = config.get_env("FEISHU_EMAIL")
            open_id = config.get_env("FEISHU_OPEN_ID")
            mobile = config.get_env("FEISHU_MOBILES")
            app_secret = config.get_env("FEISHU_APP_SECRET")
            
            if not email and not open_id and not mobile:
                 print('[Step1] FEISHU_APP_ID found but no target user (EMAIL/OPEN_ID/MOBILES). Skipping push.')
            else:
                try:
                    pusher = FeishuPusher(mode="app", app_id=app_id, app_secret=app_secret, email=email, user_id=open_id, mobile=mobile)
                    pusher.push_keywords(keywords, title_suffix=f" (Past {days} Days)")
                    print(f'[Step1] Feishu push sent via App (Target: {email or mobile or open_id})')
                except Exception as e:
                    print(f"[Step1] Feishu App push failed: {e}")
        else:
            print("[Step1] Feishu not configured (neither Webhook nor App ID found), skipping push")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JobInsight Step 1 MVP")
    parser.add_argument("--days", type=int, default=None, help="Days of data to analyze")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    parser.add_argument("-test", "--test", action="store_true", help="Run immediately (bypass scheduler)")
    args = parser.parse_args()

    load_env()

    config_path = resolve_config_path(args.config)
    config = AppConfig.from_file(str(config_path))
    
    schedule_cfg = config.schedule_config()
    enabled = schedule_cfg.get("enabled", False)
    
    # Determine mode
    if args.test or not enabled:
        # Run immediately
        days = args.days if args.days is not None else config.collector_days(7)
        return run_analysis(config, days)
    else:
        # Run scheduler
        target_time = schedule_cfg.get("time", "09:00")
        days = schedule_cfg.get("days_to_analyze", 1) 
        print(f"[JobInsight] Scheduler started. Will run daily at {target_time} (Dual Mode: 1-Day & 7-Day analysis).")
        print(f"[JobInsight] Current Time (Container): {datetime.datetime.now().strftime('%H:%M:%S')}")
        print("[JobInsight] Press Ctrl+C to exit.")
        
        while True:
            try:
                # Reload config to support hot-reloading without restart
                # (Re-parsing config.json every loop is cheap and useful)
                config = AppConfig.from_file(str(config_path))
                schedule_cfg = config.schedule_config()
                target_time = schedule_cfg.get("time", "09:00")
                days = schedule_cfg.get("days_to_analyze", 1)
                
                now = datetime.datetime.now()
                current_time = now.strftime("%H:%M")
                
                # Debug log every hour to show aliveness (optional)
                if now.minute == 0 and now.second < 30:
                     print(f"[JobInsight] Heartbeat: {current_time} (Target: {target_time})")

                if current_time == target_time:
                    print(f"[JobInsight] Triggering scheduled analysis at {current_time}...")
                    
                    # User Request: "give 1 day / 7 days push at the same time"
                    # Run 1-day analysis
                    print("[JobInsight] Running 1-day analysis...")
                    run_analysis(config, 1)
                    
                    # Run 7-day analysis
                    print("[JobInsight] Running 7-day analysis...")
                    run_analysis(config, 7)
                    
                    # Sleep for 61 seconds to avoid double triggering
                    time.sleep(61)
                else:
                    # Sleep for a bit
                    time.sleep(30)
            except KeyboardInterrupt:
                print("\n[JobInsight] Scheduler stopped.")
                break
            except Exception as e:
                print(f"[JobInsight] Scheduler error: {e}")
                time.sleep(60)
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
