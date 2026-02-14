import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .compress import compress_chat_text
from .ingest import ChatSession
from .sanitize import redact_sensitive


@dataclass
class CherryApiConfig:
    base_url: str
    header_name: str
    api_key: str
    prefix: str = ""

    @classmethod
    def from_env(cls) -> "CherryApiConfig":
        base_url = (os.environ.get("CHERRY_API_BASE") or "").strip()
        if not base_url:
            host = (os.environ.get("CHERRY_API_HOST") or "").strip()
            port = (os.environ.get("CHERRY_API_PORT") or "").strip()
            if host in {"0.0.0.0", "::", "[::]"}:
                host = "127.0.0.1"
            if host and "://" not in host:
                host = "http://" + host
            if host and port and host.rstrip("/").endswith((":" + port)):
                base_url = host
            elif host and port:
                base_url = host.rstrip("/") + ":" + port
            elif host:
                base_url = host
        if not base_url:
            base_url = "http://127.0.0.1:23333"

        header_raw = (os.environ.get("CHERRY_API_HEADER") or os.environ.get("HEADER") or "").strip()
        api_key = (os.environ.get("CHERRY_API_KEY") or os.environ.get("API_KEY") or "").strip()
        header_name = header_raw
        if ":" in header_raw:
            left, right = header_raw.split(":", 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                header_name = left
                api_key = right
                return cls(base_url=base_url, header_name=header_name, api_key=api_key, prefix="")
        prefix = os.environ.get("CHERRY_API_PREFIX") or ""
        return cls(base_url=base_url, header_name=header_name, api_key=api_key, prefix=str(prefix))

    def auth_headers(self) -> Dict[str, str]:
        if not self.header_name or not self.api_key:
            return {}
        return {self.header_name: f"{self.prefix}{self.api_key}"}


class CherryStudioApiError(RuntimeError):
    pass


def _http_get_json(url: str, headers: Dict[str, str] | None = None, timeout: int = 10) -> Any:
    base_headers = {"Accept": "application/json", "User-Agent": "MyJobAgent-New/1.0"}
    for k, v in (headers or {}).items():
        if k and v is not None:
            base_headers[str(k)] = str(v)
    req = urllib.request.Request(url, headers=base_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise CherryStudioApiError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise CherryStudioApiError(f"Connection failed for {url}: {e}") from e
    except Exception as e:
        raise CherryStudioApiError(f"Failed to parse JSON for {url}: {e}") from e


def _count_paths(spec: Dict[str, Any]) -> int:
    paths = spec.get("paths", {})
    if isinstance(paths, dict):
        return len(paths)
    return 0


def fetch_openapi_spec_detail(cfg: CherryApiConfig) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    override = str(os.environ.get("CHERRY_API_SPEC_PATH") or "").strip()
    candidates = []
    if override:
        candidates.append(override)
    candidates.extend(
        [
            "/api-docs.json",
            "/api-docs/swagger.json",
            "/api-docs/openapi.json",
            "/swagger.json",
            "/openapi.json",
            "/api-docs-json",
        ]
    )

    last_err: Exception | None = None
    auth = cfg.auth_headers()
    for path in candidates:
        url = cfg.base_url.rstrip("/") + "/" + str(path).lstrip("/")
        try:
            payload = _http_get_json(url, headers=auth)
        except Exception as e:
            last_err = e
            continue

        spec = _unwrap_spec(payload)
        if isinstance(spec, dict):
            meta = {"spec_url": url, "paths_count": _count_paths(spec), "used_auth": bool(auth)}
            if meta["paths_count"] == 0 and auth:
                try:
                    payload2 = _http_get_json(url, headers={})
                    spec2 = _unwrap_spec(payload2)
                    if isinstance(spec2, dict) and _count_paths(spec2) > 0:
                        return spec2, {"spec_url": url, "paths_count": _count_paths(spec2), "used_auth": False}
                except Exception:
                    pass
            return spec, meta
        last_err = CherryStudioApiError(f"OpenAPI spec is not a JSON object: {type(spec)}")

    if last_err:
        raise CherryStudioApiError(str(last_err)) from last_err
    raise CherryStudioApiError("OpenAPI spec fetch failed")


def fetch_openapi_spec(cfg: CherryApiConfig) -> Dict[str, Any]:
    spec, _ = fetch_openapi_spec_detail(cfg)
    return spec


def _unwrap_spec(payload: Any) -> Any:
    if isinstance(payload, dict):
        if "paths" in payload or "openapi" in payload or "swagger" in payload:
            return payload
        for k in ("data", "result", "spec", "openapi", "swagger", "body"):
            if k in payload:
                inner = payload.get(k)
                if isinstance(inner, dict):
                    if "paths" in inner or "openapi" in inner or "swagger" in inner:
                        return inner
                if isinstance(inner, str):
                    try:
                        decoded = json.loads(inner)
                        return _unwrap_spec(decoded)
                    except Exception:
                        pass
        for v in payload.values():
            if isinstance(v, dict) and ("paths" in v or "openapi" in v or "swagger" in v):
                return v
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
            return _unwrap_spec(decoded)
        except Exception:
            return payload
    return payload


def _iter_openapi_paths(spec: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            m = str(method or "").lower()
            if m not in {"get", "post", "put", "delete", "patch"}:
                continue
            if not isinstance(op, dict):
                continue
            yield str(path), m, op


def _score_path(path: str, method: str, keywords: List[str]) -> int:
    p = path.lower()
    score = 0
    for kw in keywords:
        if kw in p:
            score += 2
    if method == "get":
        score += 1
    return score


def infer_endpoints(spec: Dict[str, Any]) -> Dict[str, str]:
    override_sessions = str(os.environ.get("CHERRY_API_LIST_SESSIONS") or "").strip()
    override_messages = str(os.environ.get("CHERRY_API_GET_MESSAGES") or "").strip()
    if override_sessions and override_messages:
        return {"list_sessions": override_sessions, "get_messages": override_messages}

    candidates_sessions: List[Tuple[int, str]] = []
    candidates_messages: List[Tuple[int, str]] = []

    for path, method, _ in _iter_openapi_paths(spec):
        s1 = _score_path(path, method, ["sessions", "session", "topics", "topic"])
        if method == "get" and s1 >= 3:
            candidates_sessions.append((s1, path))

        s2 = _score_path(path, method, ["messages", "message"])
        if method == "get" and s2 >= 3:
            candidates_messages.append((s2, path))

    candidates_sessions.sort(key=lambda x: (-x[0], x[1]))
    candidates_messages.sort(key=lambda x: (-x[0], x[1]))

    out = {}
    if candidates_sessions:
        out["list_sessions"] = candidates_sessions[0][1]
    if candidates_messages:
        out["get_messages"] = candidates_messages[0][1]
    return out


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        try:
            if v >= 1e14:
                return datetime.fromtimestamp(v / 1e6)
            if v >= 1e12:
                return datetime.fromtimestamp(v / 1e3)
            if v >= 1e9:
                return datetime.fromtimestamp(v)
        except Exception:
            return None
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _coerce_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        for k in ("text", "content", "message"):
            if k in content:
                t = _coerce_text(content.get(k))
                if t:
                    return t
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for item in content[:2000]:
            t = _coerce_text(item)
            if t:
                parts.append(t)
        return "\n".join(parts).strip()
    return str(content).strip()


def _extract_messages(container: Any) -> List[Dict[str, Any]]:
    if isinstance(container, list):
        return [m for m in container if isinstance(m, dict)]
    if isinstance(container, dict):
        for k in ("messages", "data", "items", "rows", "result"):
            v = container.get(k)
            if isinstance(v, list):
                return [m for m in v if isinstance(m, dict)]
        for v in container.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [m for m in v if isinstance(m, dict)]
    return []


def _format_message_line(msg: Dict[str, Any]) -> Tuple[Optional[datetime], str]:
    role = str(msg.get("role") or msg.get("sender") or msg.get("author") or "").strip()
    t = _parse_dt(msg.get("createdAt") or msg.get("created_at") or msg.get("timestamp") or msg.get("time"))
    content = _coerce_text(msg.get("content") or msg.get("text") or msg.get("message") or msg.get("data"))
    if not content:
        return t, ""
    prefix = ""
    if t is not None and role:
        prefix = f"{t.isoformat(timespec='seconds')} {role}: "
    elif t is not None:
        prefix = f"{t.isoformat(timespec='seconds')} "
    elif role:
        prefix = f"{role}: "
    return t, prefix + content


def _http_get_path(cfg: CherryApiConfig, path: str) -> Any:
    url = cfg.base_url.rstrip("/") + "/" + path.lstrip("/")
    return _http_get_json(url, headers=cfg.auth_headers())


def _looks_like_session_list(payload: Any) -> List[Dict[str, Any]]:
    items = _extract_messages(payload)
    if items:
        return items
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("sessions", "topics", "data", "items", "rows", "result"):
            v = payload.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [x for x in v if isinstance(x, dict)]
    return []


def _probe_endpoints(cfg: CherryApiConfig) -> Dict[str, str]:
    list_candidates = [
        "/sessions",
        "/api/sessions",
        "/v1/sessions",
        "/topics",
        "/api/topics",
        "/v1/topics",
    ]
    msg_candidates = [
        "/sessions/{id}/messages",
        "/api/sessions/{id}/messages",
        "/v1/sessions/{id}/messages",
        "/topics/{id}/messages",
        "/api/topics/{id}/messages",
        "/v1/topics/{id}/messages",
        "/sessions/{id}",
        "/topics/{id}",
    ]

    for lp in list_candidates:
        try:
            payload = _http_get_path(cfg, lp)
        except Exception:
            continue
        sessions = _looks_like_session_list(payload)
        if not sessions:
            continue
        sid = str(sessions[0].get("id") or sessions[0].get("sessionId") or sessions[0].get("topicId") or "").strip()
        if not sid:
            continue
        for mp in msg_candidates:
            try:
                path = mp.replace("{id}", urllib.parse.quote(sid))
                msg_payload = _http_get_path(cfg, path)
            except Exception:
                continue
            msgs = _extract_messages(msg_payload)
            if msgs:
                return {"list_sessions": lp, "get_messages": mp}
    return {}


def extract_sessions_via_api(
    cfg: CherryApiConfig,
    domain: str = "cherrystudio",
    days: int = 7,
    max_chars: int = 6000,
    debug: bool = False,
) -> Tuple[List[ChatSession], Dict[str, Any]]:
    spec, spec_meta = fetch_openapi_spec_detail(cfg)
    endpoints = infer_endpoints(spec)
    meta: Dict[str, Any] = {
        "mode": "api",
        "base_url": cfg.base_url,
        "spec": spec_meta,
        "endpoints": endpoints,
    }

    if "list_sessions" not in endpoints or "get_messages" not in endpoints:
        endpoints = _probe_endpoints(cfg)
    if "list_sessions" not in endpoints or "get_messages" not in endpoints:
        paths = []
        for p, m, _ in _iter_openapi_paths(spec):
            if m == "get":
                paths.append(p)
            if len(paths) >= 30:
                break
        hint = {}
        if isinstance(spec, dict):
            for k in ("message", "error", "detail"):
                if k in spec:
                    hint[k] = spec.get(k)
        raise CherryStudioApiError(
            f"Cannot infer endpoints from OpenAPI: {endpoints}; spec_keys={list(spec.keys())[:30]}; hint={hint}; sample_paths={paths}"
        )

    sessions_payload = _http_get_path(cfg, endpoints["list_sessions"])
    sessions_list = _extract_messages(sessions_payload)
    if not sessions_list and isinstance(sessions_payload, dict):
        for k in ("sessions", "topics"):
            v = sessions_payload.get(k)
            if isinstance(v, list):
                sessions_list = [x for x in v if isinstance(x, dict)]
                break

    cutoff = datetime.now() - timedelta(days=max(0, int(days)))
    out: List[ChatSession] = []
    filtered = 0

    for s in sessions_list:
        sid = str(s.get("id") or s.get("sessionId") or s.get("topicId") or s.get("topic_id") or "").strip()
        if not sid:
            continue

        updated = _parse_dt(s.get("updatedAt") or s.get("updated_at") or s.get("lastUpdated") or s.get("last_updated"))
        created = _parse_dt(s.get("createdAt") or s.get("created_at"))
        if updated is not None and updated < cutoff:
            filtered += 1
            continue

        msg_path = endpoints["get_messages"]
        if "{" in msg_path and "}" in msg_path:
            msg_path = msg_path.replace("{id}", urllib.parse.quote(sid)).replace("{sessionId}", urllib.parse.quote(sid))
        else:
            joiner = "&" if "?" in msg_path else "?"
            msg_path = f"{msg_path}{joiner}id={urllib.parse.quote(sid)}"

        msgs_payload = _http_get_path(cfg, msg_path)
        msgs = _extract_messages(msgs_payload)

        lines = []
        times: List[datetime] = []
        for m in msgs:
            t, line = _format_message_line(m)
            if line:
                lines.append(line)
            if t is not None:
                times.append(t)
        if not lines:
            continue

        start_dt = min(times) if times else created
        end_dt = max(times) if times else updated
        if end_dt is not None and end_dt < cutoff:
            filtered += 1
            continue

        raw_text = "\n".join(lines)
        raw_text = redact_sensitive(raw_text)
        compressed = compress_chat_text(raw_text, max_chars=max_chars)
        if not compressed:
            continue

        out.append(
            ChatSession(
                domain=domain,
                source=f"cherrystudio::api::{sid}",
                compressed_text=compressed,
                start=start_dt.isoformat() if start_dt else None,
                end=end_dt.isoformat() if end_dt else None,
            )
        )

    meta["sessions_total"] = len(sessions_list)
    meta["sessions_output"] = len(out)
    meta["sessions_filtered"] = filtered

    return out, meta
