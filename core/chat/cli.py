import argparse
import json
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1]
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from analysis.auditor import annotate_keywords
from chat.ingest import ingest_chat_sessions, save_chat_sessions_jsonl, select_recent_session_files
from chat.sources import collect_chat_sessions
from config import AppConfig, resolve_api_key
from llm.llm_client import create_llm_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat session tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest_p = sub.add_parser("ingest", help="Ingest chat exports and write compressed sessions")
    ingest_p.add_argument("--input", required=True, help="Input file or directory (txt/md/json/zip)")
    ingest_p.add_argument("--domain", required=True, help="Chatbot domain label (e.g. chatgpt.com)")
    ingest_p.add_argument("--out", required=True, help="Output .jsonl file path")
    ingest_p.add_argument("--max-chars", type=int, default=6000, help="Max chars per session after compression")
    ingest_p.add_argument("--mode", type=str, default="manual", choices=["manual", "auto"], help="manual=read files; auto=reserved")

    analyze_p = sub.add_parser("analyze", help="Analyze chatbot logs only and extract keywords")
    analyze_p.add_argument("--input", required=True, help="Input directory with per-session files (md/txt/json/zip)")
    analyze_p.add_argument("--domain", required=True, help="Chatbot domain label (recommended: canonical base domain)")
    analyze_p.add_argument("--days", type=int, default=7, help="Only include sessions within past N days")
    analyze_p.add_argument("--max-chars", type=int, default=6000, help="Max chars per session after compression")
    analyze_p.add_argument("--sessions-out", required=True, help="Output .jsonl sessions file path")
    analyze_p.add_argument("--out", required=True, help="Output keywords .json file path")
    analyze_p.add_argument("--skills-limit", type=int, default=10, help="TopN skills limit")
    analyze_p.add_argument("--tools-limit", type=int, default=3, help="TopN tools limit")
    analyze_p.add_argument("--pool-per-session", type=int, default=300, help="Estimated seconds per session for auditing")

    collect_p = sub.add_parser("collect", help="Collect chat sessions from configured sources")
    collect_p.add_argument("--out", type=str, default="", help="Override output .jsonl sessions file path")
    collect_p.add_argument("--days", type=int, default=0, help="Override days window (0=use config)")
    collect_p.add_argument("--max-chars", type=int, default=0, help="Override max chars per session (0=use config)")

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        if args.mode != "manual":
            raise RuntimeError("auto mode is reserved; use --mode manual")
        sessions = ingest_chat_sessions(args.input, domain=args.domain, max_chars=int(args.max_chars))
        save_chat_sessions_jsonl(sessions, args.out)
        print(f"[Chat] sessions: {len(sessions)}")
        print(f"[Chat] wrote: {args.out}")
        return 0

    if args.cmd == "analyze":
        selected_files = select_recent_session_files(args.input, days=int(args.days))
        sessions = []
        for p in selected_files:
            sessions.extend(ingest_chat_sessions(str(p), domain=args.domain, max_chars=int(args.max_chars)))

        save_chat_sessions_jsonl(sessions, args.sessions_out)
        print(f"[Chat] sessions: {len(sessions)}")
        print(f"[Chat] wrote: {args.sessions_out}")

        config = AppConfig.from_file("config.json")
        llm_cfg = config.llm_config()
        provider = llm_cfg.get("provider", "zhipu")
        api_key = ""
        key_map = {
            "zhipu": "ZHIPU_API_KEY",
            "doubao": "VOLCANO_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openai_compat": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
        }
        env_key = key_map.get(provider, "")
        if env_key:
            api_key = config.get_env(env_key)
        if not api_key:
            api_key = resolve_api_key(provider, llm_cfg.get("api_key", ""))

        llm_client = create_llm_client(
            provider=provider,
            api_key=api_key,
            model=llm_cfg.get("model", "glm-4.7"),
            timeout=llm_cfg.get("timeout", 60),
            base_url=llm_cfg.get("base_url", ""),
        )

        session_dicts = [s.to_dict() for s in sessions]
        chatbot_keywords = llm_client.extract_chatbot_keywords(
            chat_sessions=session_dicts,
            skills_limit=int(args.skills_limit),
            tools_limit=int(args.tools_limit),
        )

        compressed_data = {
            "chat_sessions": session_dicts,
            "chatbot": {"pool_seconds": int(args.pool_per_session) * max(0, len(session_dicts))},
        }
        audited = annotate_keywords(chatbot_keywords, compressed_data)

        out_path = Path(args.out)
        if not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(audited, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[Chat] wrote: {args.out}")
        return 0

    if args.cmd == "collect":
        config = AppConfig.from_file("config.json")
        sources = config.chatbot_sources()
        if not sources:
            raise RuntimeError("chatbot.sources is empty in config.json")

        days = int(args.days) if int(args.days or 0) > 0 else config.chatbot_days(7)
        max_chars = int(args.max_chars) if int(args.max_chars or 0) > 0 else config.chatbot_max_chars(6000)
        out_path = str(args.out or "").strip() or config.chatbot_sessions_out("")
        if not out_path:
            raise RuntimeError("chatbot.sessions_out is empty in config.json (or pass --out)")

        sessions, results = collect_chat_sessions(sources=sources, days=days, max_chars=max_chars)
        save_chat_sessions_jsonl(sessions, out_path)
        print(f"[Chat] sessions: {len(sessions)}")
        for r in results:
            t = str((r.source or {}).get("type", "") or "")
            d = str((r.source or {}).get("domain", "") or "")
            print(f"[Chat] source={t} domain={d} sessions={len(r.sessions)} errors={len(r.errors)}")
            for e in r.errors[:3]:
                print(f"[Chat]   error: {e}")
        print(f"[Chat] wrote: {out_path}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
