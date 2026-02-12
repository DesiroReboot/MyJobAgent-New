import re
from urllib.parse import urlparse, urlunparse
from typing import Dict, List
from collections import Counter, defaultdict
from datetime import timedelta


class DataCleaner:
    """Data cleaning and compression for Step 1 (ActivityWatch only)."""

    EMAIL_PATTERN = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", re.IGNORECASE)
    PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
    TOKEN_PATTERN = re.compile(r"\b(Bearer|Token|API[_-]?Key)[:\s]+[\w\-]{20,}\b", re.IGNORECASE)
    PASSWORD_PATTERN = re.compile(r"\bpassword[:\s]+[^\s]+\b", re.IGNORECASE)

    FILE_EXTENSIONS = {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".rar", ".tar", ".gz", ".jpg", ".png", ".gif", ".mp4", ".mp3",
    }
    MIN_EVENT_SECONDS = 10
    APP_BLACKLIST = {
        "explorer.exe",
        "applicationframehost.exe",
        "systemsettings.exe",
        "lockapp.exe",
        "searchui.exe",
        "shellexperiencehost.exe",
        "textinputhost.exe",
        "zoom.exe",
        "teams.exe",
        "slack.exe",
        "wechat.exe",
        "cmd.exe",
        "powershell.exe",
        "conhost.exe",
        "taskmgr.exe",
        "svchost.exe",
        "runtimebroker.exe",
        "searchhost.exe",
        "startmenuexperiencehost.exe",
        "csrss.exe",
        "wmiprvse.exe",
        "sihost.exe",
        "ctfmon.exe",
        "smartscreen.exe",
    }
    TITLE_BLACKLIST = {
        "new tab",
        "untitled",
        "loading",
        "home",
        "settings",
        "downloads",
        "program manager",
        "start",
        "search",
        "task switching",
        "notification center",
        "volume control",
        "network flyout",
        "input indicator",
        "clock flyout",
        "action center",
        "battery flyout",
        "calendar flyout",
        "desktop",
    }
    
    CHATBOT_DOMAINS = {
        'chatgpt.com', 
        'claude.ai', 
        'gemini.google.com', 
        'sass-node5.chatshare.biz', 
        # Wildcards handled by logic
    }
    
    TRUNCATION_MAP = {
        'agent开': 'Agent开发',
        'ai开': 'AI开发',
        '需求分': '需求分析',
        '代码优': '代码优化',
    }

    
    # High Value: Logic implementation files
    HIGH_VALUE_EXTENSIONS = {
        ".py", ".java", ".kt", ".cpp", ".c", ".h", ".hpp", ".cs", ".go", ".rs", 
        ".ts", ".tsx", ".js", ".jsx", ".php", ".rb", ".swift", ".scala", ".lua", ".pl"
    }
    
    # High Value: Logic keywords (typically class/file names)
    HIGH_VALUE_KEYWORDS = {
        "controller", "service", "repository", "model", "viewmodel", "component", 
        "hook", "utils", "helper", "manager", "handler", "provider", "adapter"
    }

    # Low Value: Configuration, logs, generated files
    LOW_VALUE_EXTENSIONS = {
        ".json", ".yaml", ".yml", ".log", ".lock", ".xml", ".ini", ".toml", 
        ".conf", ".cfg", ".properties", ".svg", ".css", ".scss", ".less", 
        ".map", ".meta", ".tmp", ".bak"
    }
    
    # Low Value: Specific filenames (case insensitive check)
    LOW_VALUE_FILENAMES = {
        "readme", "license", "changelog", "settings", "preferences", "configuration", 
        "package-lock", "yarn", "dockerfile", "makefile", "gitignore"
    }

    # 常见的应用/网页标题后缀，清洗时移除
    SUFFIX_PATTERNS = [
        r" - Google Chrome$",
        r" - Microsoft Edge$",
        r" - Mozilla Firefox$",
        r" - Visual Studio Code$",
        r" - Visual Studio$",
        r" - PyCharm$",
        r" - IntelliJ IDEA$",
        r" - Notepad\+\+$",
        r" - 记事本$",
        r" - Word$",
        r" - Excel$",
        r" - PowerPoint$",
        r" - Outlook$",
        r" - OneNote$",
        r" - Teams$",
        r" - Slack$",
        r" - Zoom$",
        r" - Discord$",
        r" - Spotify$",
        r" - 网易云音乐$",
        r" - QQ音乐$",
        r" - 微信$",
        r" - 飞书$",
        r" - 钉钉$",
        r" - 知乎$",
        r" - 豆瓣$",
        r" - 简书$",
        r" - 掘金$",
        r" - GitHub$",
        r" - Stack Overflow$",
        r" - CSDN博客$",
        r" - 博客园$",
        r" - 哔哩哔哩_bilibili$",
        r" - YouTube$",
        r" - Wikipedia$",
        r" - 百度百科$",
    ]

    @classmethod
    def clean_url(cls, url: str) -> str:
        try:
            parsed = urlparse(url)
            clean = parsed._replace(query="", fragment="")

            path = clean.path
            for ext in cls.FILE_EXTENSIONS:
                if path.lower().endswith(ext):
                    path = path[:-len(ext)]
                    break

            clean = clean._replace(path=path)
            return urlunparse(clean)
        except Exception:
            return url

    @classmethod
    def clean_title(cls, title: str, max_len: int = 150) -> str:
        if not title:
            return ""

        # Remove notifications like (1) or (20+)
        title = re.sub(r"^\(\d+\+?\)\s*", "", title)

        title = cls.EMAIL_PATTERN.sub("***@***.***", title)
        title = cls.PHONE_PATTERN.sub("***********", title)
        title = cls.TOKEN_PATTERN.sub("***", title)
        title = cls.PASSWORD_PATTERN.sub("***", title)
        
        # Remove common suffixes
        for pattern in cls.SUFFIX_PATTERNS:
            title = re.sub(pattern, "", title, flags=re.IGNORECASE)

        # Truncation Repair
        for k, v in cls.TRUNCATION_MAP.items():
            if title.lower().endswith(k.lower()):
                title = title[:-len(k)] + v
            elif k.lower() in title.lower():
                title = re.sub(re.escape(k), v, title, flags=re.IGNORECASE)

        if len(title) > max_len:
            title = title[: max_len - 3] + "..."

        return title.strip()

    @classmethod
    def extract_domain(cls, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return "unknown"

    @classmethod
    def _is_low_value_title(cls, title: str) -> bool:
        """
        Determines if a title is considered 'low value' (config, logs, readme, etc.).
        Low value titles should be discarded to avoid noise.
        """
        if not title:
            return True
            
        lower_title = title.lower()
        
        # Check extensions
        # Extract extension: last part after dot
        parts = lower_title.rsplit('.', 1)
        if len(parts) > 1:
            ext = "." + parts[1]
            # Special case: .md files
            if ext == ".md":
                # README.md is low value
                if "readme" in parts[0]:
                    return True
                # Other .md (TODO.md, DevDoc.md) are NOT low value (keep them)
                return False
                
            if ext in cls.LOW_VALUE_EXTENSIONS:
                return True
        
        # Check filenames/keywords
        # If title matches exactly or looks like a file "settings.json" -> handled by ext
        # If title is just "Settings", it might be caught by TITLE_BLACKLIST, but double check
        for keyword in cls.LOW_VALUE_FILENAMES:
            # Check if keyword appears as a standalone word or the main name
            # e.g. "Project Settings" -> "settings" in ...
            if keyword in lower_title:
                 return True
                 
        return False

    @classmethod
    def compress_data(cls, aw_records: List) -> Dict:
        domain_stats: Dict = {}
        # New: Group by App Name, aggregate duration, collect unique titles (0/1 logic)
        app_stats: Dict = {}
        
        audio_agg = defaultdict(int)
        afk_intervals = []
        total_intervals = []

        for record in aw_records:
            duration = max(0, int(record.duration))
            if duration <= 0:
                continue
            start = record.timestamp
            end = start + timedelta(seconds=duration)
            total_intervals.append((start, end))

            if record.event_type == "afk":
                if str(record.status).lower() == "afk":
                    afk_intervals.append((start, end))
                continue

            if record.event_type == "web":
                clean_url = cls.clean_url(record.url)
                domain = cls.extract_domain(clean_url)
                clean_title = cls.clean_title(record.title)

                # Chatbot Masking
                is_chatbot = domain in cls.CHATBOT_DOMAINS
                if not is_chatbot:
                    # Check for wildcard matches (e.g., *.chatshare.biz)
                    if domain.endswith("chatshare.biz"):
                         is_chatbot = True
                
                if is_chatbot:
                    clean_title = f"AI Assistance Session ({domain})"

                if domain not in domain_stats:
                    domain_stats[domain] = {
                        "cnt": {"aw_events": 0},
                        "dur": {"active_seconds": 0},
                        "title_samples": [],
                        "raw_titles_with_meta": [],
                    }

                domain_stats[domain]["cnt"]["aw_events"] += 1
                domain_stats[domain]["dur"]["active_seconds"] += duration

                if len(domain_stats[domain]["title_samples"]) < 3:
                    domain_stats[domain]["title_samples"].append(clean_title)

                domain_stats[domain]["raw_titles_with_meta"].append(
                    {"title": clean_title, "visit_count": 1, "duration": record.duration}
                )

            elif record.event_type == "window":
                if duration < cls.MIN_EVENT_SECONDS:
                    continue
                title = cls.clean_title(record.title)
                app = cls.clean_title(record.app)
                if cls._is_noise_app(app) or cls._is_noise_title(title):
                    continue
                
                # New Logic: Group by App, accumulate duration, set of titles
                if app not in app_stats:
                    app_stats[app] = {
                        "duration": 0,
                        "titles": set()
                    }
                app_stats[app]["duration"] += duration
                
                # Filter Low Value Titles
                if title and not cls._is_low_value_title(title):
                    app_stats[app]["titles"].add(title)

            elif record.event_type == "audio":
                if duration < cls.MIN_EVENT_SECONDS:
                    continue
                title = cls.clean_title(record.title)
                app = cls.clean_title(record.app)
                if cls._is_noise_app(app) or cls._is_noise_title(title):
                    continue
                # Audio still uses simple aggregation for now (less critical)
                key = (app, title)
                audio_agg[key] += duration

        for domain in domain_stats:
            seen = set()
            unique_samples = []
            for title in domain_stats[domain]["title_samples"]:
                if title and title not in seen:
                    seen.add(title)
                    unique_samples.append(title)
            domain_stats[domain]["title_samples"] = unique_samples

        for domain, stats in domain_stats.items():
            title_freq = Counter()
            for item in stats["raw_titles_with_meta"]:
                title_freq[item["title"]] += 1
            stats["title_freq"] = dict(title_freq)
            del stats["raw_titles_with_meta"]

        # Format app stats for output
        # Convert sets to lists and sort by app duration
        formatted_apps = []
        for app, stats in app_stats.items():
            # Limit titles to top N to avoid token overflow? 
            # User wants 0/1 logic, so we keep all unique titles but maybe limit count
            # Let's keep up to 20 unique titles per app for now to be safe
            unique_titles = list(stats["titles"])[:20] 
            formatted_apps.append({
                "app": app,
                "duration": stats["duration"],
                "titles": unique_titles
            })
        
        # Sort apps by duration descending
        formatted_apps.sort(key=lambda x: x["duration"], reverse=True)

        def top_samples(agg_map, limit=5):
            items = [
                {"app": app, "title": title, "duration": dur}
                for (app, title), dur in agg_map.items()
                if title or app
            ]
            items.sort(key=lambda x: (-x["duration"], x["app"], x["title"]))
            return items[:limit]

        total_seconds = cls._union_seconds(total_intervals)
        afk_seconds = cls._union_seconds(afk_intervals)

        compressed = {
            "meta": {
                "afk_seconds": afk_seconds,
                "total_seconds": total_seconds,
                "afk_ratio": round(afk_seconds / total_seconds, 4) if total_seconds else 0.0,
            },
            "web": domain_stats,
            "non_web_samples": {
                "window": formatted_apps,  # Now uses App-Grouped structure
                "audio": top_samples(audio_agg),
            },
        }

        return compressed

    @classmethod
    def _is_noise_app(cls, app: str) -> bool:
        if not app:
            return False
        name = app.strip().lower()
        return name in cls.APP_BLACKLIST

    @classmethod
    def _is_noise_title(cls, title: str) -> bool:
        if not title:
            return False
        name = title.strip().lower()
        if name in cls.TITLE_BLACKLIST:
            return True
        # Filter pure numbers or single characters (unless C/R/etc, but usually noise)
        if re.match(r"^\d+$", name):
            return True
        if len(name) < 2 and name not in {"c", "r", "v"}: # Allow some single letters if meaningful
            return True
        return False

    @staticmethod
    def _union_seconds(intervals: List) -> int:
        if not intervals:
            return 0
        intervals = sorted(intervals, key=lambda x: x[0])
        total = 0
        cur_start, cur_end = intervals[0]
        for start, end in intervals[1:]:
            if start <= cur_end:
                if end > cur_end:
                    cur_end = end
            else:
                total += int((cur_end - cur_start).total_seconds())
                cur_start, cur_end = start, end
        total += int((cur_end - cur_start).total_seconds())
        return max(0, total)
