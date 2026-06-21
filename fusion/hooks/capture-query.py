#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

ROOT = Path.home() / ".claude" / "session-captures"


def compact(text, limit):
    text = "" if text is None else (text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=2))
    return text if len(text) <= limit else text[:limit] + "…"


def detect_tag(text):
    m = re.search(r"\[([A-Z0-9_:-]+)\]", text or "")
    return m.group(1) if m else ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", help="Filter by tag, session title, or session id substring")
    p.add_argument("--recent", type=int, default=20)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    rows = []
    for path in sorted(ROOT.glob("*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            s = json.loads(path.read_text())
        except Exception:
            continue
        prompt = ((s.get("prompts") or [{}])[-1].get("prompt") if s.get("prompts") else "") or ""
        title = s.get("session_title") or ""
        sid = path.parent.name
        haystack = "\n".join([sid, title, prompt])
        if args.tag and args.tag not in haystack:
            continue
        tr = s.get("tool_results") or []
        last = tr[-1] if tr else {}
        response = last.get("tool_response") if isinstance(last, dict) else {}
        row = {
            "session_id": sid,
            "session_title": title,
            "tag": detect_tag(prompt),
            "events": s.get("events"),
            "prompts": len(s.get("prompts") or []),
            "tool_uses": len(s.get("tool_uses") or []),
            "tool_results": len(s.get("tool_results") or []),
            "tool_failures": len(s.get("tool_failures") or []),
            "stops": len(s.get("stops") or []),
            "last_tool_stdout": response.get("stdout") if isinstance(response, dict) else None,
            "last_tool_stderr": response.get("stderr") if isinstance(response, dict) else None,
            "last_assistant": s.get("last_assistant"),
            "summary_path": str(path),
        }
        rows.append(row)
        if len(rows) >= args.recent:
            break
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(f"\n=== {row['tag'] or '-'} | {row['session_title'] or '-'} | {row['session_id']} ===")
        print(f"events={row['events']} prompts={row['prompts']} tool_uses={row['tool_uses']} tool_results={row['tool_results']} failures={row['tool_failures']} stops={row['stops']}")
        if row.get("last_tool_stdout") is not None:
            print("--- last tool stdout ---")
            print(compact(row["last_tool_stdout"], 1200))
        if row.get("last_assistant"):
            print("--- last assistant ---")
            print(compact(row["last_assistant"], 2000))
        print(f"summary={row['summary_path']}")


if __name__ == "__main__":
    main()
