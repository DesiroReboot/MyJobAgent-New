import argparse
import os
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from core.pusher.push_gate import compute_feishu_push_decision
except Exception:
    from pusher.push_gate import compute_feishu_push_decision

from cleaner.data_cleaner import DataCleaner
from config import AppConfig, resolve_api_key, resolve_config_path
from collectors.aw_collector import ActivityWatchCollector
from llm.llm_client import create_llm_client
from analysis.auditor import annotate_keywords
from visualization.wordcloud import WordCloudGenerator
from pusher.feishu_pusher import FeishuPusher
from storage.event_store import EventStore
from chat.ingest import load_chat_sessions_file, save_chat_sessions_jsonl
from chat.merge import merge_keyword_payloads
from chat.sources import collect_chat_sessions
from chat.obsidian_export import export_sessions_per_session


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


def ensure_output_path(path_str: str) -> str:
    path = Path(path_str)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


import time
import datetime
from typing import Optional


def run_analysis(config: AppConfig, days: int, chat_sessions_file: Optional[str] = None) -> int:
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
    chatbot_pool_seconds = int((compressed_data.get("chatbot", {}) or {}).get("pool_seconds", 0) or 0)
    try:
        chatbot_cfg = compressed_data.get("chatbot", {}) if isinstance(compressed_data, dict) else {}
        if not isinstance(chatbot_cfg, dict):
            chatbot_cfg = {}
        chatbot_cfg["token_weight"] = float(config.chatbot_token_weight(0.4))
        compressed_data["chatbot"] = chatbot_cfg
    except Exception:
        pass

    chat_sessions = []
    sessions_path = str(chat_sessions_file or "").strip()
    auto_sources = config.chatbot_sources() if config.chatbot_enabled(False) else []
    auto_out = config.chatbot_sessions_out("") if config.chatbot_enabled(False) else ""
    chat_days = min(days, config.chatbot_days(7)) if config.chatbot_enabled(False) else days
    chat_max_chars = config.chatbot_max_chars(6000)

    if not sessions_path and auto_sources and auto_out:
        p = Path(auto_out)
        if p.exists():
            sessions_path = auto_out
        else:
            try:
                sessions, results = collect_chat_sessions(sources=auto_sources, days=int(chat_days), max_chars=int(chat_max_chars))
                save_chat_sessions_jsonl(sessions, auto_out)
                chat_sessions = [s.to_dict() for s in sessions]
                compressed_data["chat_sessions"] = chat_sessions
                print(f"[Chat] Sessions collected: {len(chat_sessions)}")
                for r in results:
                    t = str((r.source or {}).get("type", "") or "")
                    d = str((r.source or {}).get("domain", "") or "")
                    print(f"[Chat] source={t} domain={d} sessions={len(r.sessions)} errors={len(r.errors)}")
                    for e in r.errors[:3]:
                        print(f"[Chat]   error: {e}")

                if config.obsidian_enabled(False) and config.obsidian_export_mode("per-session") == "per-session":
                    vault = config.obsidian_vault_path("")
                    if vault:
                        export_sessions_per_session(sessions, vault_path=vault, folder=config.obsidian_folder("CherryStudio"))
            except Exception as e:
                print(f"[Chat] Collect failed: {e}")
                chat_sessions = []

    if not chat_sessions and not sessions_path and config.chatbot_enabled(False):
        sessions_path = config.chatbot_sessions_file("")

    if not chat_sessions and sessions_path:
        try:
            chat_sessions = load_chat_sessions_file(sessions_path)
            compressed_data["chat_sessions"] = chat_sessions
            print(f"[Chat] Sessions loaded: {len(chat_sessions)}")
        except Exception as e:
            print(f"[Chat] Failed to load sessions: {e}")
            chat_sessions = []

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

    base_url = str(llm_cfg.get("base_url", "") or "").strip()
    base_url_host = ""
    if base_url:
        try:
            base_url_host = urlparse(base_url).netloc or base_url
        except Exception:
            base_url_host = base_url

    print(
        "[LLM] "
        + f"provider={provider} "
        + f"model={llm_cfg.get('model', '')} "
        + f"base_url={base_url_host or '(empty)'} "
        + f"env_key={env_key or '(none)'} "
        + f"key_set={'yes' if bool(api_key) else 'no'}"
    )
    if base_url_host:
        host_lower = base_url_host.lower()
        provider_lower = str(provider or "").lower()
        if "deepseek.com" in host_lower and provider_lower != "deepseek":
            print(f"[LLM][WARNING] base_url points to deepseek but provider={provider_lower}")
        if "dashscope.aliyuncs.com" in host_lower and provider_lower != "dashscope":
            print(f"[LLM][WARNING] base_url points to dashscope but provider={provider_lower}")
        if ("volces.com" in host_lower or "volcengine" in host_lower) and provider_lower != "doubao":
            print(f"[LLM][WARNING] base_url points to doubao but provider={provider_lower}")

    llm_client = create_llm_client(
        provider=provider,
        api_key=api_key,
        model=llm_cfg.get("model", "glm-4.7"),
        timeout=llm_cfg.get("timeout", 60),
        base_url=base_url,
    )

    min_k = int(llm_cfg.get("keyword_min", 5))
    # Adjust max_k based on days
    default_max = int(llm_cfg.get("keyword_max", 20))
    if days <= 1:
        max_k = 5
        skills_limit, tools_limit = config.llm_topn_limits(days=days, default_skills=5, default_tools=3)
    else:
        max_k = 10
        skills_limit, tools_limit = config.llm_topn_limits(days=days, default_skills=10, default_tools=5)
    
    # Override if config explicitly forces something else? 
    # User requested: 1-day -> 5, 7-day -> 10.
    # We will respect this rule over config.json's keyword_max for now, or use it as a cap.

    print(f"[Step1] TopN limits (days={days}): skills={skills_limit}, tools={tools_limit}, min_k={min_k}, max_k={max_k}")

    try:
        self_consistency_runs = int(llm_cfg.get("self_consistency_runs", 1) or 1)
        runs = []
        llm_meta = {"used_llm": False, "fallback_used": True, "http_status": None, "error": ""}
        if self_consistency_runs <= 1:
            keywords, llm_meta = llm_client.extract_keywords(
                compressed_data,
                min_k=min_k,
                max_k=max_k,
                skills_limit=skills_limit,
                tools_limit=tools_limit,
                return_meta=True,
            )
        else:
            keywords = None
            for _ in range(self_consistency_runs):
                run_keywords, run_meta = llm_client.extract_keywords(
                    compressed_data,
                    min_k=min_k,
                    max_k=max_k,
                    skills_limit=skills_limit,
                    tools_limit=tools_limit,
                    return_meta=True,
                )
                runs.append(run_keywords)
                llm_meta = run_meta
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

    chatbot_meta = None
    if chat_sessions:
        try:
            meta = compressed_data.get("meta", {}) if isinstance(compressed_data, dict) else {}
            active_total = int(meta.get("total_seconds", 0) or 0) - int(meta.get("afk_seconds", 0) or 0)
            max_active_total = max(0, int(active_total))
            if chatbot_pool_seconds > 0:
                effective_chatbot_pool_seconds = int(chatbot_pool_seconds)
            else:
                per_session_pool = int(config.chatbot_pool_seconds_per_session(300) or 0)
                session_count = int(len(chat_sessions) or 0)
                if max_active_total <= 0 or per_session_pool <= 0 or session_count <= 0:
                    effective_chatbot_pool_seconds = 0
                else:
                    if session_count > (max_active_total // per_session_pool + 1):
                        effective_chatbot_pool_seconds = max_active_total
                    else:
                        est_pool = per_session_pool * session_count
                        effective_chatbot_pool_seconds = min(int(est_pool), max_active_total)

            chatbot_keywords, chatbot_meta = llm_client.extract_chatbot_keywords(
                chat_sessions=chat_sessions,
                skills_limit=skills_limit,
                tools_limit=min(3, tools_limit),
                return_meta=True,
            )
            if isinstance(chatbot_keywords, dict) and not (chatbot_keywords.get("tools_platforms") or []):
                domains = (compressed_data.get("chatbot", {}) or {}).get("domains", {}) if isinstance(compressed_data, dict) else {}
                domain_items = []
                if isinstance(domains, dict) and domains:
                    pairs = []
                    for d, stats in domains.items():
                        try:
                            sec = int((stats.get("dur", {}) or {}).get("active_seconds", 0) or 0)
                        except Exception:
                            sec = 0
                        d = str(d or "").strip()
                        if d and sec > 0:
                            pairs.append((d, sec))
                    pairs.sort(key=lambda x: (-x[1], x[0]))
                    max_sec = max([s for _, s in pairs] or [1])
                    for d, sec in pairs[: min(5, len(pairs))]:
                        domain_items.append({"name": d, "weight": float(sec) / float(max_sec) if max_sec else 0.5})
                chatbot_keywords["tools_platforms"] = domain_items
            non_chat_total = max(0, int(active_total) - int(effective_chatbot_pool_seconds))

            base_payload = keywords
            if isinstance(base_payload, list):
                base_payload = {"skills_interests": base_payload, "tools_platforms": []}
            if not isinstance(base_payload, dict):
                base_payload = {"skills_interests": [], "tools_platforms": []}

            keywords = merge_keyword_payloads(
                base_payload=base_payload,
                chatbot_payload=chatbot_keywords,
                non_chat_total_seconds=non_chat_total,
                chatbot_pool_seconds=effective_chatbot_pool_seconds,
            )
            print(f"[Chat] Keywords merged: {_count_keywords(keywords)}")
        except Exception as e:
            print(f"[Chat] Merge failed: {e}")

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
    feishu_cfg = config.section("feishu")
    push_on_llm_fallback = bool(feishu_cfg.get("push_on_llm_fallback", False))

    keyword_count = _count_keywords(keywords)
    should_push, title_fallback_suffix, skip_reason = compute_feishu_push_decision(
        keyword_count=keyword_count,
        llm_meta=llm_meta,
        chatbot_meta=chatbot_meta,
        push_on_llm_fallback=push_on_llm_fallback,
    )
    if not should_push:
        print(f"[Step1] Feishu push skipped: {skip_reason}")
    
    # 优先使用配置的webhook
    if webhook_url and should_push:
        pusher = FeishuPusher(mode="bot", webhook_url=webhook_url)
        pusher.push_keywords(
            keywords,
            title_suffix=f" (Past {days} Days){title_fallback_suffix}",
            skills_limit=skills_limit,
            tools_limit=tools_limit,
        )
        if feishu_account:
            print(f"[Step1] Feishu push sent (account: {feishu_account})")
        else:
            print("[Step1] Feishu push sent via Webhook")
    else:
        # 尝试使用App模式
        app_id = config.get_env("FEISHU_APP_ID")
        if app_id and should_push:
            email = config.get_env("FEISHU_EMAIL")
            open_id = config.get_env("FEISHU_OPEN_ID")
            mobile = config.get_env("FEISHU_MOBILES")
            app_secret = config.get_env("FEISHU_APP_SECRET")
            
            if not email and not open_id and not mobile:
                 print('[Step1] FEISHU_APP_ID found but no target user (EMAIL/OPEN_ID/MOBILES). Skipping push.')
            else:
                try:
                    pusher = FeishuPusher(mode="app", app_id=app_id, app_secret=app_secret, email=email, user_id=open_id, mobile=mobile)
                    pusher.push_keywords(
                        keywords,
                        title_suffix=f" (Past {days} Days){title_fallback_suffix}",
                        skills_limit=skills_limit,
                        tools_limit=tools_limit,
                    )
                    print(f'[Step1] Feishu push sent via App (Target: {email or mobile or open_id})')
                except Exception as e:
                    print(f"[Step1] Feishu App push failed: {e}")
        else:
            if should_push:
                print("[Step1] Feishu not configured (neither Webhook nor App ID found), skipping push")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JobInsight Step 1 MVP")
    parser.add_argument("--days", type=int, default=None, help="Days of data to analyze")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    parser.add_argument("--chat-sessions", type=str, default="", help="Path to chatbot sessions (.jsonl/.json)")
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
        return run_analysis(config, days, chat_sessions_file=args.chat_sessions)
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
