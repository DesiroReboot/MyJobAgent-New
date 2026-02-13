import re


_REPLACEMENTS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-[REDACTED]"),
    (re.compile(r"(?i)bearer\\s+[A-Za-z0-9\\-\\._~\\+/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_\\-]{20,}\\.[A-Za-z0-9_\\-]{20,}\\.[A-Za-z0-9_\\-]{10,}"), "[JWT_REDACTED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key\\s*[:=]\\s*)(['\\\"]?)[A-Za-z0-9\\-_=]{12,}\\2"), r"\\1[REDACTED]"),
    (re.compile(r"(?i)(secret\\s*[:=]\\s*)(['\\\"]?)[A-Za-z0-9\\-_=]{12,}\\2"), r"\\1[REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
    (re.compile(r"\\b1\\d{10}\\b"), "[MOBILE_REDACTED]"),
    (re.compile(r"\\b\\d{17}[0-9Xx]\\b"), "[ID_REDACTED]"),
    (re.compile(r"(?i)-----BEGIN [A-Z ]+ PRIVATE KEY-----[\\s\\S]+?-----END [A-Z ]+ PRIVATE KEY-----"), "[PRIVATE_KEY_REDACTED]"),
]


def redact_sensitive(text: str) -> str:
    if not text:
        return ""
    out = text
    for pattern, replacement in _REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return out

