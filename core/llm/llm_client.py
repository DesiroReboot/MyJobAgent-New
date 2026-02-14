import json
import re
from typing import Dict, List, Optional

import requests

try:
    import prompts
except ImportError:
    try:
        from core import prompts
    except ImportError:
        import sys
        # Assuming we are in core/llm/ and prompts is in core/
        # This is a bit hacky but covers the structure
        pass


class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str, timeout: int = 60, base_url: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    CONTAINERS = {
        'msedge.exe', 'chrome.exe', 'code.exe', 'idea64.exe', 'explorer.exe',
        'Microsoft Edge', 'Google Chrome', 'Visual Studio Code', 'VS Code', 'IntelliJ IDEA', 'File Explorer'
    }

    class RequestError(Exception):
        def __init__(
            self,
            message: str,
            *,
            status_code: Optional[int] = None,
            url: str = "",
            body_excerpt: str = "",
        ) -> None:
            super().__init__(message)
            self.status_code = status_code
            self.url = url
            self.body_excerpt = body_excerpt

    def extract_keywords(
        self,
        compressed_data: Dict,
        min_k: int = 3,
        max_k: int = 5,
        skills_limit: Optional[int] = None,
        tools_limit: Optional[int] = None,
        return_meta: bool = False,
    ) -> Dict:
        """
        Extract keywords from compressed data using LLM.
        Returns a dict with 'skills_interests' and 'tools_platforms' keys, 
        or a legacy list of dicts if LLM fails to structure it (or falls back).
        """
        prompt = self._build_prompt(
            compressed_data,
            min_k,
            max_k,
            skills_limit=skills_limit,
            tools_limit=tools_limit,
        )
        used_llm = False
        fallback_used = False
        meta = {"used_llm": False, "fallback_used": False, "http_status": None, "error": ""}
        try:
            text = self._call_llm(prompt)
            used_llm = True
            parsed = self._parse_keywords(text)
            
            # Filter low weight items (threshold < 0.05)
            # User requested: ignore min quantity requirement if filtering removes items
            parsed = self._filter_keywords(parsed, threshold=0.05)
            
            if parsed:
                # If parsed is a dict (new structure), return it directly
                if isinstance(parsed, dict) and ("skills_interests" in parsed or "tools_platforms" in parsed):
                    if return_meta:
                        meta["used_llm"] = True
                        meta["fallback_used"] = False
                        return parsed, meta
                    return parsed
                
                # If parsed is a list (legacy structure), return it
                if isinstance(parsed, list):
                    # Sort legacy list
                    parsed.sort(key=lambda x: x.get("weight", 0), reverse=True)
                    if len(parsed) > max_k:
                        if return_meta:
                            meta["used_llm"] = True
                            meta["fallback_used"] = False
                            return parsed[:max_k], meta
                        return parsed[:max_k]
                    if return_meta:
                        meta["used_llm"] = True
                        meta["fallback_used"] = False
                        return parsed, meta
                    return parsed
            fallback_used = True
            meta["error"] = "LLM response parsed empty; fallback used"
                    
        except Exception as e:
            print(f"[WARNING] LLM extraction failed: {e}")
            fallback_used = True
            meta["error"] = str(e)
            if isinstance(e, LLMClient.RequestError):
                meta["http_status"] = e.status_code
            
        # Fallback to rule-based
        fallback = self._rule_based_keywords(compressed_data, min_k, max_k)
        if return_meta:
            meta["used_llm"] = bool(used_llm)
            meta["fallback_used"] = bool(fallback_used)
            return fallback, meta
        return fallback

    def extract_chatbot_keywords(
        self,
        chat_sessions: List[Dict],
        skills_limit: int = 10,
        tools_limit: int = 3,
        return_meta: bool = False,
    ) -> Dict:
        if not chat_sessions:
            payload = {"skills_interests": [], "tools_platforms": []}
            if return_meta:
                return payload, {"used_llm": False, "fallback_used": True, "http_status": None, "error": "no chat_sessions"}
            return payload

        prompt = prompts.build_chatbot_keyword_prompt(
            chat_sessions=chat_sessions,
            skills_limit=skills_limit,
            tools_limit=tools_limit,
        )

        combined_text = "\n".join([str(s.get("compressed_text", "") or "") for s in chat_sessions])
        used_llm = False
        fallback_used = False
        meta = {"used_llm": False, "fallback_used": False, "http_status": None, "error": ""}
        try:
            text = self._call_llm(prompt)
            used_llm = True
            parsed = self._parse_keywords(text)
            parsed = self._filter_keywords(parsed, threshold=0.05)

            if isinstance(parsed, dict) and ("skills_interests" in parsed or "tools_platforms" in parsed):
                skills = parsed.get("skills_interests", []) or []
                validated = []
                for item in skills:
                    quote = str(item.get("evidence_quote", "") or "").strip()
                    if quote and quote in combined_text:
                        validated.append(item)
                parsed["skills_interests"] = validated
                parsed["tools_platforms"] = parsed.get("tools_platforms", []) or []
                if return_meta:
                    meta["used_llm"] = True
                    meta["fallback_used"] = False
                    return parsed, meta
                return parsed
            fallback_used = True
            meta["error"] = "LLM response parsed empty; fallback used"
        except Exception as e:
            print(f"[WARNING] Chatbot LLM extraction failed: {e}")
            fallback_used = True
            meta["error"] = str(e)
            if isinstance(e, LLMClient.RequestError):
                meta["http_status"] = e.status_code

        stop = {
            "the", "and", "for", "with", "from", "that", "this", "you", "your", "are",
            "how", "what", "why", "when", "where", "use", "using", "into", "over", "new",
            "教程", "下载", "官网", "登录", "注册", "配置", "安装", "使用", "指南", "文档",
        }
        counter = {}
        tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fa5]+", combined_text)
        for t in tokens:
            t = t.strip().lower()
            if not t or t in stop or len(t) <= 1:
                continue
            counter[t] = counter.get(t, 0.0) + 1.0

        sorted_items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top = sorted_items[: max(1, skills_limit)]
        max_count = max([v for _, v in top] or [1.0])
        skills = [{"name": k, "weight": (v / max_count if max_count else 0.5)} for k, v in top]

        domain_counter = {}
        for s in chat_sessions:
            d = str(s.get("domain", "") or "").strip()
            if not d:
                continue
            domain_counter[d] = domain_counter.get(d, 0) + 1
        tool_items = sorted(domain_counter.items(), key=lambda kv: (-kv[1], kv[0]))[: max(1, tools_limit)]
        max_dc = max([v for _, v in tool_items] or [1])
        tools = [{"name": d, "weight": (c / max_dc if max_dc else 0.5)} for d, c in tool_items]

        payload = {"skills_interests": skills, "tools_platforms": tools}
        if return_meta:
            meta["used_llm"] = bool(used_llm)
            meta["fallback_used"] = bool(fallback_used) or True
            return payload, meta
        return payload

    @classmethod
    def _filter_keywords(cls, parsed: object, threshold: float = 0.05) -> object:
        if not parsed:
            return None
        
        if isinstance(parsed, dict) and ("skills_interests" in parsed or "tools_platforms" in parsed):
            # Filter structured
            skills = parsed.get("skills_interests", [])
            tools = parsed.get("tools_platforms", [])
            
            # Post-Processing: Move/Discard CONTAINERS from Skills
            new_skills = []
            tool_names = {t.get("name", "").lower() for t in tools}
            
            # Pre-compute lower case containers
            lower_containers = {c.lower() for c in cls.CONTAINERS}
            
            for s in skills:
                name = s.get("name", "")
                weight = s.get("weight", 0)
                
                if name.lower() in lower_containers: 
                    # It's a container. Move to tools if not present.
                    if name.lower() not in tool_names:
                        tools.append(s)
                        tool_names.add(name.lower())
                    # Discard from skills
                    continue
                
                if weight >= threshold:
                    new_skills.append(s)
            
            parsed["skills_interests"] = new_skills
            parsed["tools_platforms"] = [k for k in tools if k.get("weight", 0) >= threshold]
            return parsed
            
        if isinstance(parsed, list):
            # Filter legacy list
            return [k for k in parsed if k.get("weight", 0) >= threshold]
            
        return parsed

    def _call_llm(self, prompt: str) -> str:
        if not self.api_key:
            raise ValueError("LLM api_key is required")

        if self.provider == "zhipu":
            return self._call_zhipu(prompt)
        if self.provider in {"doubao", "openai", "openai_compat", "dashscope", "deepseek"}:
            return self._call_openai_compat(prompt)

        raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _call_zhipu(self, prompt: str) -> str:
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_openai_compat(self, prompt: str) -> str:
        if not self.base_url:
            raise ValueError("base_url is required for openai-compatible providers")

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = None
            body_excerpt = ""
            try:
                if e.response is not None:
                    status = int(getattr(e.response, "status_code", 0) or 0) or None
                    body = ""
                    try:
                        body = e.response.text or ""
                    except Exception:
                        body = ""
                    body = body.strip()
                    if body:
                        body_excerpt = body[:400] + ("..." if len(body) > 400 else "")
            except Exception:
                status = None
                body_excerpt = ""

            hint = ""
            if status == 401:
                hint = " Unauthorized(401). Check provider env key, base_url matches the service, and API key belongs to that service."
            msg = f"LLM request failed: HTTP {status or 'error'} for url={url}.{hint}"
            if body_excerpt:
                msg += f" Response: {body_excerpt}"
            raise LLMClient.RequestError(msg, status_code=status, url=url, body_excerpt=body_excerpt) from e
        except requests.exceptions.RequestException as e:
            msg = f"LLM request failed: {type(e).__name__} for url={url}: {e}"
            raise LLMClient.RequestError(msg, status_code=None, url=url, body_excerpt="") from e

        try:
            data = resp.json()
        except Exception as e:
            raise LLMClient.RequestError(f"LLM response is not valid JSON for url={url}: {e}", url=url) from e
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMClient.RequestError(f"LLM response missing choices/message for url={url}: {e}", url=url) from e

    @staticmethod
    def _build_prompt(
        compressed_data: Dict,
        min_k: int,
        max_k: int,
        skills_limit: Optional[int] = None,
        tools_limit: Optional[int] = None,
    ) -> str:
        return prompts.build_keyword_extraction_prompt(
            compressed_data,
            min_k,
            max_k,
            skills_limit=skills_limit,
            tools_limit=tools_limit,
        )

    @staticmethod
    def _parse_keywords(text: str) -> object:
        if not text:
            return None

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            payload = json.loads(text[start : end + 1])
        except Exception:
            return None

        # Check for new structured format
        if "skills_interests" in payload or "tools_platforms" in payload:
            return payload

        # Check for legacy format
        keywords = payload.get("keywords")
        if isinstance(keywords, list):
            parsed = []
            for item in keywords:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                weight = item.get("weight", 0.5)
                try:
                    weight = float(weight)
                except Exception:
                    weight = 0.5
                parsed.append({"name": name, "weight": max(0.0, min(1.0, weight))})
            return parsed
            
        return None

    @staticmethod
    def _rule_based_keywords(compressed_data: Dict, min_k: int, max_k: int) -> List[Dict]:
        stop = {
            "the", "and", "for", "with", "from", "that", "this", "you", "your", "are",
            "how", "what", "why", "when", "where", "use", "using", "into", "over", "new",
            "教程", "下载", "官网", "登录", "注册", "配置", "安装", "使用", "指南", "文档",
        }

        counter = {}
        web = compressed_data.get("web", {}) if isinstance(compressed_data, dict) else {}
        for stats in web.values():
            for title in stats.get("title_samples", []):
                tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fa5]+", title)
                for t in tokens:
                    t = t.strip().lower()
                    if not t or t in stop or len(t) <= 1:
                        continue
                    counter[t] = counter.get(t, 0.0) + 1.0

        non_web = compressed_data.get("non_web_samples", {}) if isinstance(compressed_data, dict) else {}
        for sample in non_web.get("window", []):
            for text in [sample.get("title", ""), sample.get("app", "")]:
                tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fa5]+", text)
                for t in tokens:
                    t = t.strip().lower()
                    if not t or t in stop or len(t) <= 1:
                        continue
                    counter[t] = counter.get(t, 0.0) + 0.6

        for sample in non_web.get("audio", []):
            for text in [sample.get("title", ""), sample.get("app", "")]:
                tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fa5]+", text)
                for t in tokens:
                    t = t.strip().lower()
                    if not t or t in stop or len(t) <= 1:
                        continue
                    counter[t] = counter.get(t, 0.0) + 0.4

        sorted_items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top = sorted_items[:max_k]
        if not top:
            return []

        max_count = max(v for _, v in top)
        keywords = []
        for name, count in top:
            weight = count / max_count if max_count else 0.5
            keywords.append({"name": name, "weight": weight})

        if len(keywords) < min_k:
            return keywords
        return keywords[:max_k]


def create_llm_client(provider: str, api_key: str, model: str, timeout: int = 60, base_url: str = "") -> LLMClient:
    return LLMClient(provider=provider, api_key=api_key, model=model, timeout=timeout, base_url=base_url)
