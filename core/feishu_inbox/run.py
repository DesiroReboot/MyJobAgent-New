import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

_CORE_DIR = Path(__file__).resolve().parents[1]
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

from config import AppConfig, resolve_config_path
from pusher.feishu_pusher import FeishuPusher
from feishu_inbox.inbox import FeishuInboxService


def load_env() -> None:
    if load_dotenv:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)


def main(argv: list[str] | None = None) -> int:
    load_env()
    if argv is None:
        argv = sys.argv[1:]

    config_path = "config.json"
    if argv:
        config_path = argv[0]

    cfg = AppConfig.from_file(str(resolve_config_path(config_path)))
    inbox_cfg = cfg.feishu_inbox_config()
    if not cfg.feishu_inbox_enabled(False):
        print("[FeishuInbox] disabled. Set feishu_inbox.enabled=true to start.")
        return 0

    pusher = FeishuPusher(mode="app")
    service = FeishuInboxService(
        app_id=os.getenv("FEISHU_APP_ID", ""),
        app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        config=cfg,
        pusher=pusher,
        inbox_cfg=inbox_cfg,
    )
    service.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
