import json
import re
from typing import Dict, List

import requests


class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str, timeout: int = 60, base_url: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def extract_keywords(self, compressed_data: Dict, min_k: int = 3, max_k: int = 5) -> List[Dict]:
        prompt = self._build_prompt(compressed_data, min_k, max_k)
        text = self._call_llm(prompt)
        parsed = self._parse_keywords(text)
        if parsed:
            # Sort by weight descending
            parsed.sort(key=lambda x: x.get("weight", 0), reverse=True)
            
            if len(parsed) > max_k:
                return parsed[:max_k]
            if len(parsed) >= min_k:
                return parsed
        return self._rule_based_keywords(compressed_data, min_k, max_k)

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
    def _build_prompt(compressed_data: Dict, min_k: int, max_k: int) -> str:
        data_preview = json.dumps(compressed_data, ensure_ascii=False, indent=2)[:8000]
        return (
            "You will be given compressed local activity data. "
            "The 'web' section contains domains with title samples and durations from browser history. "
            "The 'non_web_samples' section contains window/audio samples. "
            "The 'meta' section includes afk_ratio (do not exclude on afk). "
            "Extract job-related keywords that best summarize the user's interests. "
            f"Return {min_k}-{max_k} keywords. "
            "Return JSON only in the form: {\"keywords\": [{\"name\": str, \"weight\": float}]}. "
            "Weights should be between 0 and 1.\n\n"
            f"DATA:\n{data_preview}"
        )

    @staticmethod
    def _parse_keywords(text: str) -> List[Dict]:
        if not text:
            return []

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []

        try:
            payload = json.loads(text[start : end + 1])
        except Exception:
            return []

        keywords = payload.get("keywords")
        if not isinstance(keywords, list):
            return []

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
