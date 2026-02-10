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


def main() -> int:
    parser = argparse.ArgumentParser(description="JobInsight Step 1 MVP")
    parser.add_argument("--days", type=int, default=None, help="Days of data to analyze")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    args = parser.parse_args()

    load_env()

    config_path = resolve_config_path(args.config)
    config = AppConfig.from_file(str(config_path))

    days = args.days if args.days is not None else config.collector_days(7)
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
    api_key = resolve_api_key(provider, llm_cfg.get("api_key", ""))
    llm_client = create_llm_client(
        provider=provider,
        api_key=api_key,
        model=llm_cfg.get("model", "glm-4.7"),
        timeout=llm_cfg.get("timeout", 60),
        base_url=llm_cfg.get("base_url", ""),
    )

    min_k = int(llm_cfg.get("keyword_min", 5))
    max_k = int(llm_cfg.get("keyword_max", 20))

    try:
        keywords = llm_client.extract_keywords(compressed_data, min_k=min_k, max_k=max_k)
    except Exception as e:
        print(f"[Step1] LLM call failed: {e}")
        raise
    print(f"[Step1] Keywords extracted: {len(keywords)}")

    output_cfg = config.output_config()
    wordcloud_file = ensure_output_path(output_cfg.get("wordcloud_file", "wordcloud.html"))

    wc_generator = WordCloudGenerator()
    wc_generator.generate(keywords, wordcloud_file)
    print(f"[Step1] Wordcloud generated: {wordcloud_file}")

    feishu_account, webhook_url = config.feishu_webhook()
    
    # 优先使用配置的webhook
    if webhook_url:
        pusher = FeishuPusher(mode="bot", webhook_url=webhook_url)
        pusher.push_keywords(keywords)
        if feishu_account:
            print(f"[Step1] Feishu push sent (account: {feishu_account})")
        else:
            print("[Step1] Feishu push sent via Webhook")
    else:
        # 尝试使用App模式
        app_id = os.getenv("FEISHU_APP_ID")
        if app_id:
            email = os.getenv("FEISHU_EMAIL")
            open_id = os.getenv("FEISHU_OPEN_ID")
            mobile = os.getenv("FEISHU_MOBILES")
            
            if not email and not open_id and not mobile:
                 print('[Step1] FEISHU_APP_ID found but no target user (EMAIL/OPEN_ID/MOBILES). Skipping push.')
            else:
                try:
                    pusher = FeishuPusher(mode="app", app_id=app_id, email=email, user_id=open_id, mobile=mobile)
                    pusher.push_keywords(keywords)
                    print(f'[Step1] Feishu push sent via App (Target: {email or mobile or open_id})')
                except Exception as e:
                    print(f"[Step1] Feishu App push failed: {e}")
        else:
            print("[Step1] Feishu not configured (neither Webhook nor App ID found), skipping push")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
