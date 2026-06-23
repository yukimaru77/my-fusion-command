#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path.home()
CAPTURE_ROOT = HOME / ".claude" / "session-captures"
PROJECTS_ROOT = HOME / ".claude" / "projects"
ENABLE_FILE = CAPTURE_ROOT / "enabled"


def capture_enabled():
    return os.environ.get("CLAUDE_TRANSCRIPT_CAPTURE") == "1" or ENABLE_FILE.exists()


def safe_name(value):
    value = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)[:120] or "unknown"


def read_stdin_json():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}, raw
    try:
        return json.loads(raw), raw
    except Exception:
        return {"_parse_error": True}, raw


def find_transcript(session_id, cwd=None, transcript_path=None):
    if transcript_path:
        path = Path(transcript_path).expanduser()
        if path.exists():
            return path
    if not session_id:
        return None
    candidates = []
    if cwd:
        sanitized = "-" + str(Path(cwd).resolve()).strip("/").replace("/", "-")
        candidates.append(PROJECTS_ROOT / sanitized / f"{session_id}.jsonl")
    candidates.extend(PROJECTS_ROOT.glob(f"*/{session_id}.jsonl"))
    for path in candidates:
        if path.exists():
            return path
    token = f'"sessionId":"{session_id}"'
    fallback = []
    for path in PROJECTS_ROOT.glob("*/*.jsonl"):
        try:
            if token in path.read_text(errors="replace"):
                fallback.append(path)
        except Exception:
            pass
    return max(fallback, key=lambda p: p.stat().st_mtime) if fallback else None


def append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_hooks(events_path, dst_summary):
    summary = {
        "session_id": None,
        "session_title": None,
        "cwd": None,
        "prompts": [],
        "tool_uses": [],
        "tool_results": [],
        "tool_failures": [],
        "messages": [],
        "stops": [],
        "last_assistant": None,
        "events": 0,
    }
    if events_path.exists():
        for line in events_path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            summary["events"] += 1
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            name = event.get("event") if event.get("event") != "unknown" else payload.get("hook_event_name", "unknown")
            summary["session_id"] = payload.get("session_id") or event.get("session_id") or summary["session_id"]
            summary["session_title"] = payload.get("session_title") or summary["session_title"]
            summary["cwd"] = payload.get("cwd") or event.get("cwd") or summary["cwd"]
            if name == "UserPromptSubmit":
                summary["prompts"].append({"timestamp": event.get("timestamp"), "prompt": payload.get("prompt")})
            elif name == "PreToolUse":
                summary["tool_uses"].append({"timestamp": event.get("timestamp"), "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input")})
            elif name == "PostToolUse":
                summary["tool_results"].append({"timestamp": event.get("timestamp"), "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"), "tool_response": payload.get("tool_response")})
            elif name == "PostToolUseFailure":
                summary["tool_failures"].append({"timestamp": event.get("timestamp"), "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"), "tool_response": payload.get("tool_response")})
            elif name == "MessageDisplay":
                summary["messages"].append({"timestamp": event.get("timestamp"), "payload": payload})
            elif name in ("Stop", "SessionEnd"):
                last = payload.get("last_assistant_message")
                if last:
                    summary["last_assistant"] = last
                summary["stops"].append({"timestamp": event.get("timestamp"), "event": name, "last_assistant_message": last, "transcript_path": payload.get("transcript_path")})
    dst_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize_transcript(src, dst_summary):
    summary = {"users": 0, "assistants": 0, "tool_uses": 0, "tool_results": 0, "turns": 0, "last_user": None, "last_assistant": None, "last_turn": None}
    def text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                typ = item.get("type")
                if typ == "text":
                    parts.append(item.get("text", ""))
                elif typ == "tool_use":
                    summary["tool_uses"] += 1
                elif typ == "tool_result":
                    summary["tool_results"] += 1
            return "\n".join(p for p in parts if p)
        return ""
    try:
        for line in src.read_text(errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            typ = obj.get("type")
            if typ == "system" and obj.get("subtype") == "turn_duration":
                summary["turns"] += 1
                summary["last_turn"] = {k: obj.get(k) for k in ("timestamp", "durationMs", "messageCount", "uuid")}
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            body = text(msg.get("content"))
            if typ == "user":
                summary["users"] += 1
                if body.strip():
                    summary["last_user"] = body.strip()[-1000:]
            elif typ == "assistant":
                summary["assistants"] += 1
                if body.strip():
                    summary["last_assistant"] = body.strip()[-4000:]
    except Exception as exc:
        summary["error"] = str(exc)
    dst_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if not capture_enabled():
        print(json.dumps({"suppressOutput": True}, ensure_ascii=False))
        return
    payload, raw = read_stdin_json()
    event = os.environ.get("CLAUDE_HOOK_EVENT") or payload.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "unknown")
    session_id = payload.get("session_id") or payload.get("sessionId")
    cwd = payload.get("cwd") or payload.get("workspace_dir")
    transcript_path = payload.get("transcript_path")
    session_dir = CAPTURE_ROOT / safe_name(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "hook-events.jsonl"
    append_jsonl(events_path, {"event": event, "session_id": session_id, "cwd": cwd, "payload": payload, "raw_stdin": raw if payload.get("_parse_error") else None})
    summarize_hooks(events_path, session_dir / "summary.json")
    src = find_transcript(session_id, cwd, transcript_path)
    manifest = {"event": event, "session_id": session_id, "cwd": cwd, "transcript_path": transcript_path, "transcript_source": str(src) if src else None, "capture_dir": str(session_dir)}
    if src and src.exists():
        dst = session_dir / "transcript.jsonl"
        shutil.copy2(src, dst)
        summarize_transcript(dst, session_dir / "transcript-summary.json")
        manifest["transcript_copy"] = str(dst)
        manifest["size_bytes"] = dst.stat().st_size
    (session_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_jsonl(CAPTURE_ROOT / "index.jsonl", manifest)
    print(json.dumps({"suppressOutput": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
