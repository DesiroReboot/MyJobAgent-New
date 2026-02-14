import re
from typing import List, Tuple


_CODE_FENCE_RE = re.compile(r"```[\\s\\S]*?```", re.MULTILINE)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_code_blocks(text: str, max_blocks: int, max_block_chars: int) -> List[str]:
    blocks = []
    for m in _CODE_FENCE_RE.finditer(text or ""):
        block = m.group(0)
        if not block:
            continue
        block = block[:max_block_chars]
        blocks.append(block)
        if len(blocks) >= max_blocks:
            break
    return blocks


def _score_line(line: str) -> float:
    if not line:
        return 0.0
    lower = line.lower()
    score = 0.0
    if "traceback" in lower or "exception" in lower or "error" in lower or "failed" in lower:
        score += 3.0
    if "http://" in lower or "https://" in lower:
        score += 2.0
    if re.search(r"\\b(pip|npm|pnpm|yarn|conda|docker|kubectl|git)\\b", lower):
        score += 2.0
    if re.search(r"\\b(py|ts|tsx|js|json|yaml|yml|toml|sql|md)\\b", lower):
        score += 1.0
    if re.search(r"\\b(import|def|class|return|const|function|SELECT|UPDATE|INSERT)\\b", line):
        score += 1.5
    length = len(line)
    if length >= 40:
        score += 0.5
    if length >= 120:
        score += 0.5
    if re.fullmatch(r"[-=_]{6,}", line.strip()):
        score -= 1.0
    return score


def _dedupe_lines(lines: List[str], max_occurrences: int = 2) -> List[str]:
    seen = {}
    out = []
    for line in lines:
        key = line.strip()
        if not key:
            continue
        cnt = seen.get(key, 0)
        if cnt >= max_occurrences:
            continue
        seen[key] = cnt + 1
        out.append(line)
    return out


def compress_chat_text(
    text: str,
    max_chars: int = 6000,
    max_code_blocks: int = 6,
    max_code_block_chars: int = 800,
    max_lines: int = 220,
) -> str:
    text = _normalize(text)
    if not text:
        return ""

    code_blocks = _extract_code_blocks(text, max_blocks=max_code_blocks, max_block_chars=max_code_block_chars)
    text_wo_code = _CODE_FENCE_RE.sub("\n", text)
    lines = [l.strip() for l in text_wo_code.split("\n")]
    lines = [l for l in lines if l]
    lines = _dedupe_lines(lines, max_occurrences=2)

    scored: List[Tuple[float, str]] = [(_score_line(l), l) for l in lines]
    scored.sort(key=lambda x: (-x[0], -len(x[1]), x[1].lower()))

    picked: List[str] = []
    total = 0
    for score, line in scored:
        if score <= 0.0:
            break
        if len(picked) >= max_lines:
            break
        add_len = len(line) + 1
        if total + add_len > max_chars:
            continue
        picked.append(line)
        total += add_len

    picked.sort(key=lambda s: text.find(s) if text.find(s) >= 0 else 10**9)

    out_parts: List[str] = []
    if picked:
        out_parts.append("KEY LINES:")
        out_parts.extend(picked)

    if code_blocks:
        out_parts.append("")
        out_parts.append("CODE BLOCKS:")
        out_parts.extend(code_blocks)

    if not out_parts:
        fallback: List[str] = []
        total2 = 0
        for line in lines[:max_lines]:
            add_len = len(line) + 1
            if total2 + add_len > max_chars:
                break
            fallback.append(line)
            total2 += add_len
        if fallback:
            out_parts.append("TEXT:")
            out_parts.extend(fallback)

    out = "\n".join(out_parts).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
    return out
