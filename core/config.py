import os
import json
from pathlib import Path
from typing import Dict, List, Tuple


def resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if not path.is_absolute():
        if not path.exists():
            path = Path(__file__).parent / path
    return path


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

    def llm_topn_limits(self, days: int, default_skills: int, default_tools: int) -> Tuple[int, int]:
        llm = self.section("llm")
        mapping = llm.get("topn_by_days", {})
        if not isinstance(mapping, dict):
            return default_skills, default_tools

        cfg = mapping.get(str(days)) or mapping.get("default") or {}
        if not isinstance(cfg, dict):
            return default_skills, default_tools

        skills = cfg.get("skills", default_skills)
        tools = cfg.get("tools", default_tools)
        try:
            skills = int(skills)
        except Exception:
            skills = default_skills
        try:
            tools = int(tools)
        except Exception:
            tools = default_tools

        return skills, tools

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

    def chatbot_config(self) -> Dict:
        return self.section("chatbot")

    def feishu_inbox_config(self) -> Dict:
        return self.section("feishu_inbox")

    def feishu_inbox_enabled(self, default: bool = False) -> bool:
        val = self.feishu_inbox_config().get("enabled", default)
        return bool(val)

    def feishu_inbox_download_dir(self, default: str = "") -> str:
        path = str(self.feishu_inbox_config().get("download_dir", default) or "").strip()
        return self.resolve_path(path) if path else ""

    def feishu_inbox_sessions_out(self, default: str = "") -> str:
        path = str(self.feishu_inbox_config().get("sessions_out", default) or "").strip()
        return self.resolve_path(path) if path else ""

    def chatbot_enabled(self, default: bool = False) -> bool:
        val = self.chatbot_config().get("enabled", default)
        return bool(val)

    def chatbot_sessions_file(self, default: str = "") -> str:
        path = str(self.chatbot_config().get("sessions_file", default) or "").strip()
        if not path:
            return ""
        return self.resolve_path(path)

    def chatbot_max_chars(self, default: int = 6000) -> int:
        try:
            return int(self.chatbot_config().get("max_chars", default))
        except Exception:
            return default

    def chatbot_days(self, default: int = 7) -> int:
        try:
            return int(self.chatbot_config().get("days", default))
        except Exception:
            return default

    def chatbot_sessions_out(self, default: str = "") -> str:
        path = str(self.chatbot_config().get("sessions_out", default) or "").strip()
        if not path:
            return ""
        return self.resolve_path(path)

    def chatbot_pool_seconds_per_session(self, default: int = 300) -> int:
        try:
            return int(self.chatbot_config().get("pool_seconds_per_session", default))
        except Exception:
            return default

    def chatbot_token_weight(self, default: float = 0.4) -> float:
        try:
            return float(self.chatbot_config().get("token_weight", default))
        except Exception:
            return float(default)

    def chatbot_sources(self) -> List[Dict]:
        cfg = self.chatbot_config().get("sources", [])
        if not isinstance(cfg, list):
            return []
        out: List[Dict] = []
        for item in cfg:
            if not isinstance(item, dict):
                continue
            t = str(item.get("type", "") or "").strip().lower()
            if not t:
                continue
            norm = dict(item)
            norm["type"] = t
            if t == "filesystem":
                p = str(norm.get("path", "") or "").strip()
                norm["path"] = self.resolve_path(p) if p else ""
            if t == "cherrystudio":
                d = str(norm.get("data_dir", "") or "").strip()
                norm["data_dir"] = self.resolve_path(d) if d else ""
            out.append(norm)
        return out

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
