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

    def extract_keywords(
        self,
        compressed_data: Dict,
        min_k: int = 3,
        max_k: int = 5,
        skills_limit: Optional[int] = None,
        tools_limit: Optional[int] = None,
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
        try:
            text = self._call_llm(prompt)
            parsed = self._parse_keywords(text)
            
            # Filter low weight items (threshold < 0.05)
            # User requested: ignore min quantity requirement if filtering removes items
            parsed = self._filter_keywords(parsed, threshold=0.05)
            
            if parsed:
                # If parsed is a dict (new structure), return it directly
                if isinstance(parsed, dict) and ("skills_interests" in parsed or "tools_platforms" in parsed):
                    return parsed
                
                # If parsed is a list (legacy structure), return it
                if isinstance(parsed, list):
                    # Sort legacy list
                    parsed.sort(key=lambda x: x.get("weight", 0), reverse=True)
                    if len(parsed) > max_k:
                        return parsed[:max_k]
                    return parsed
                    
        except Exception as e:
            print(f"[WARNING] LLM extraction failed: {e}")
            
        # Fallback to rule-based
        fallback = self._rule_based_keywords(compressed_data, min_k, max_k)
        return fallback

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
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

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
