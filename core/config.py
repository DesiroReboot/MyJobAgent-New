import os
import json
from pathlib import Path
from typing import Dict, Tuple


def resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if not path.is_absolute():
        if not path.exists():
            path = Path(__file__).parent / path
    return path


class AppConfig:
    def __init__(self, data: Dict, base_dir: Path) -> None:
        self._data = data
        self._base_dir = base_dir

    @classmethod
    def from_file(cls, config_path: str) -> "AppConfig":
        path = resolve_config_path(config_path)
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return cls(data, path.parent)

    def section(self, name: str) -> Dict:
        section = self._data.get(name, {})
        return section if isinstance(section, dict) else {}

    def resolve_path(self, path_str: str) -> str:
        if not path_str:
            return path_str
        path = Path(path_str)
        if not path.is_absolute():
            path = self._base_dir / path
        return str(path)

    def collector_days(self, default: int = 7) -> int:
        return int(self.section("collector").get("days", default))

    def collector_source(self, default: str = "local") -> str:
        return str(self.section("collector").get("source", default)).strip().lower()

    def collector_db_path(self, default: str = "local_events.db") -> str:
        return self.resolve_path(self.section("collector").get("db_path", default))

    def collector_aw_db_path(self, default: str = "") -> str:
        # Priority: Env Var > Config
        env_path = os.environ.get("COLLECTOR_AW_DB_PATH")
        if env_path:
            return env_path
            
        path = self.section("collector").get("aw_db_path", default)
        if not path:
            return ""
        return self.resolve_path(path)

    def collector_sample_interval(self, default: int = 30) -> int:
        return int(self.section("collector").get("sample_interval_seconds", default))

    def collector_idle_threshold(self, default: int = 300) -> int:
        return int(self.section("collector").get("idle_threshold_seconds", default))

    def collector_browsers(self, default: list[str] | None = None) -> list[str]:
        if default is None:
            default = ["chrome", "edge", "firefox"]
        return self.section("collector").get("browsers", default)

    def collector_log_file(self, daemon: bool, default: str = "collector.log") -> str | None:
        if not daemon:
            return None
        return self.resolve_path(self.section("collector").get("log_file", default))

    def llm_config(self) -> Dict:
        llm = self.section("llm")
        base_model = llm.get("base_model", "dashscope")
        base_url_examples = llm.get("base_url_examples", {})
        
        # Resolve base_url from base_model if not explicitly set (or if we strictly use base_model)
        # User requested: base_url -> base_model logic
        if base_model in base_url_examples:
            llm["base_url"] = base_url_examples[base_model]
        
        return llm

    def schedule_config(self) -> Dict:
        return self.section("schedule")

    def env_vars(self) -> Dict:
        return self.section("env_vars")

    def get_env(self, key: str, default: str = "") -> str:
        # Priority: .env (os.environ) > config.json (env_vars section)
        # OR as user requested: "choose one from config.json and .env"
        # We'll prioritize os.environ (which comes from .env or system), 
        # then fall back to config.json
        
        # Check os.environ first
        val = os.environ.get(key)
        if val:
            return val
            
        # Check config.json env_vars section
        env_vars = self.env_vars()
        val = env_vars.get(key)
        if val:
            return str(val)
            
        return default

    def output_config(self) -> Dict:
        return self.section("output")

    def feishu_webhook(self) -> Tuple[str, str]:
        cfg = self.section("feishu")
        account = str(cfg.get("account", "")).strip()

        accounts = cfg.get("accounts", {})
        if isinstance(accounts, dict) and account:
            account_cfg = accounts.get(account, {})
            if isinstance(account_cfg, dict):
                url = account_cfg.get("webhook_url", "")
            else:
                url = account_cfg
            if url:
                return account, str(url).strip()

        webhook_by_account = cfg.get("webhook_by_account", {})
        if isinstance(webhook_by_account, dict) and account:
            url = webhook_by_account.get(account, "")
            if url:
                return account, str(url).strip()

        if isinstance(accounts, dict):
            default_cfg = accounts.get("default", {})
            if isinstance(default_cfg, dict):
                url = default_cfg.get("webhook_url", "")
            else:
                url = default_cfg
            if url:
                return "default", str(url).strip()

        url = cfg.get("webhook_url", "")
        return account, str(url).strip()
