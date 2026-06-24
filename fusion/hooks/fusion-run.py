#!/usr/bin/env python3
import argparse
import json
import os
import re
import signal
import shlex
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HOME = Path.home()
CAPTURE_ROOT = HOME / ".claude" / "session-captures"
ENABLE_FILE = CAPTURE_ROOT / "enabled"
FUSION_CONFIG = HOME / ".claude" / "fusion.json"
HISTORY_FILE = HOME / ".claude" / "history.jsonl"
WORKDIR_DEFAULT = os.environ.get("PWD", str(Path.cwd()))
SDK_FORK_BIN = os.environ.get("CLAUDE_FUSION_SDK_FORK_BIN", str(Path(__file__).resolve().parent / "fusion-sdk-fork.mjs"))

BUILTIN_AGENTS = [
    ("claude", "claude"),
    ("codex", "claude-codex"),
    ("glm", "claude-glm"),
]


def load_agents_config():
    if FUSION_CONFIG.exists():
        try:
            entries = json.loads(FUSION_CONFIG.read_text())
            if isinstance(entries, list) and entries:
                return [(e["name"], e["command"]) for e in entries]
        except Exception:
            pass
    return BUILTIN_AGENTS


DEFAULT_AGENTS = load_agents_config()


def run(cmd, check=True):
    try:
        result = subprocess.run(cmd, text=True, capture_output=True)
    except FileNotFoundError as exc:
        if check:
            raise RuntimeError(f"command not found: {cmd[0]}") from exc
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {shlex.join(cmd)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def unique_run_id():
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def parse_agents(value):
    if not value:
        return DEFAULT_AGENTS
    mapping = {
        "claude": "claude",
        "codex": "claude-codex",
        "claude-codex": "claude-codex",
        "glm": "claude-glm",
        "claude-glm": "claude-glm",
    }
    agents = []
    for raw in value.split(","):
        name = raw.strip()
        if not name:
            continue
        cmd = mapping.get(name, name)
        label = name.replace("claude-", "")
        agents.append((label, cmd))
    return agents or DEFAULT_AGENTS


def compact(text, limit=5000):
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def command_prompt_text(text):
    value = text or ""
    name_match = re.search(r"<command-name>\s*(/[^<\s]+)\s*</command-name>", value)
    args_match = re.search(r"<command-args>(.*?)</command-args>", value, re.DOTALL)
    if not name_match:
        return value
    command = name_match.group(1).strip()
    args = args_match.group(1).strip() if args_match else ""
    return f"{command} {args}".strip()


def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def read_jsonl(path):
    rows = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def claude_config_root():
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser().resolve() if raw else (HOME / ".claude").resolve()


def resolve_claude_session(spec, workdir):
    projects = claude_config_root() / "projects"
    explicit = Path(spec).expanduser()
    if explicit.is_file():
        return explicit.resolve()

    sid = spec.removesuffix(".jsonl")
    local = projects / project_slug(Path(workdir)) / f"{sid}.jsonl"
    if local.is_file():
        return local.resolve()

    matches = [p.resolve() for p in projects.glob(f"*/{sid}.jsonl") if p.is_file()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"Claude session not found: {spec}")
    raise RuntimeError(f"Claude session is ambiguous: {spec}")


def text_is_synthetic(text):
    value = text.strip()
    if not value:
        return True
    prefixes = (
        "<local-command-caveat>",
        "<command-name>/usage</command-name>",
        "<command-name>/cost</command-name>",
        "<task-notification>",
        "[Request interrupted by user for tool use]",
    )
    if value.startswith(prefixes):
        return True
    return value.startswith("<system-reminder>") and value.endswith("</system-reminder>")


def is_human_prompt(row):
    if row.get("type") != "user" or row.get("isMeta") is True or row.get("isSidechain") is True:
        return False
    message = row.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return not text_is_synthetic(content)
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            continue
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and not text_is_synthetic(text):
                return True
        elif kind in {"image", "document"}:
            return True
    return False


def human_prompt_text(row):
    message = row.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") in {"image", "document"}:
            parts.append(f"[{block.get('type')}]")
    return "\n".join(parts)


def build_uuid_index(project_dir, source):
    files = sorted(p for p in project_dir.glob("*.jsonl") if p.is_file() and p != source)
    files.append(source)
    by_uuid = {}
    source_rows = []
    for path in files:
        rows = read_jsonl(path)
        if path == source:
            source_rows = rows
        for row in rows:
            msg_uuid = row.get("uuid")
            if isinstance(msg_uuid, str) and msg_uuid:
                by_uuid[msg_uuid] = row
    return by_uuid, source_rows


def find_latest_message_leaf(source_rows, sid):
    allowed = {"user", "assistant", "system", "attachment"}
    for row in reversed(source_rows):
        if (
            row.get("sessionId") == sid
            and row.get("type") in allowed
            and row.get("isSidechain") is not True
            and isinstance(row.get("uuid"), str)
        ):
            return row
    return None


def uuid_is_on_chain(uuid_value, leaf, by_uuid):
    current = leaf
    seen = set()
    while current is not None:
        current_uuid = current.get("uuid")
        if not isinstance(current_uuid, str) or current_uuid in seen:
            return False
        if current_uuid == uuid_value:
            return True
        seen.add(current_uuid)
        parent = current.get("parentUuid")
        if not isinstance(parent, str) or not parent:
            return False
        current = by_uuid.get(parent)
    return False


def find_leaf(source_rows, by_uuid, sid):
    message_leaf = find_latest_message_leaf(source_rows, sid)
    for row in reversed(source_rows):
        if row.get("sessionId") == sid and row.get("type") == "last-prompt":
            leaf_uuid = row.get("leafUuid")
            if (
                isinstance(leaf_uuid, str)
                and leaf_uuid in by_uuid
                and (message_leaf is None or uuid_is_on_chain(leaf_uuid, message_leaf, by_uuid))
            ):
                return by_uuid[leaf_uuid]
    if message_leaf is not None:
        return message_leaf
    raise RuntimeError(f"no resumable message found in {sid}")


def walk_back(leaf, by_uuid):
    chain = []
    seen = set()
    current = leaf
    while current is not None:
        current_uuid = current.get("uuid")
        if not isinstance(current_uuid, str) or current_uuid in seen:
            break
        seen.add(current_uuid)
        chain.append(current)
        parent = current.get("parentUuid")
        if not isinstance(parent, str) or not parent:
            break
        current = by_uuid.get(parent)
    return chain


def latest_last_prompt(source_rows, sid, by_uuid=None, active_uuids=None):
    for row in reversed(source_rows):
        if row.get("sessionId") == sid and row.get("type") == "last-prompt":
            leaf_uuid = row.get("leafUuid")
            if by_uuid is not None and leaf_uuid not in by_uuid:
                continue
            if active_uuids is not None and leaf_uuid not in active_uuids:
                continue
            text = row.get("lastPrompt")
            if isinstance(text, str):
                return text
    return ""


def latest_human_prompt_text(source_rows, chain, sid, by_uuid=None):
    # Claude Code custom slash commands are sometimes expanded in the user row,
    # while last-prompt keeps the original "/fusion ..." text.
    active_uuids = {row.get("uuid") for row in chain if isinstance(row.get("uuid"), str)}
    text = latest_last_prompt(source_rows, sid, by_uuid, active_uuids)
    if text.strip():
        return text
    for row in chain:
        if is_human_prompt(row):
            return human_prompt_text(row)
    return ""


def is_direct_fusion_prompt(text):
    value = (text or "").lstrip()
    return value.startswith("/fusion") or "<command-name>/fusion</command-name>" in value


def latest_history_prompt(sid, workdir):
    if not HISTORY_FILE.is_file():
        return ""
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    project = str(Path(workdir).resolve())
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("sessionId") != sid:
            continue
        if row.get("project") != project:
            continue
        display = row.get("display")
        return display if isinstance(display, str) else ""
    return ""


def fusion_invocation_info(base_session, workdir):
    source = resolve_claude_session(base_session, workdir)
    sid = source.stem
    by_uuid, source_rows = build_uuid_index(source.parent, source)
    leaf = find_leaf(source_rows, by_uuid, sid)
    chain = walk_back(leaf, by_uuid)
    source_prompt = command_prompt_text(latest_human_prompt_text(source_rows, chain, sid, by_uuid))
    history_prompt = command_prompt_text(latest_history_prompt(sid, workdir))
    prompt = history_prompt if is_direct_fusion_prompt(history_prompt) else source_prompt
    return {
        "source_session": sid,
        "source_path": str(source),
        "latest_prompt": prompt,
        "source_prompt": source_prompt,
        "history_prompt": history_prompt,
        "direct_fusion": is_direct_fusion_prompt(prompt),
    }


def target_assistant(chain_backwards, turns=1):
    count = 0
    dropped_user_index = None
    for index, row in enumerate(chain_backwards):
        if is_human_prompt(row):
            count += 1
            if count == turns:
                dropped_user_index = index
                break
    if dropped_user_index is None:
        raise RuntimeError(f"active chain has fewer than {turns} human turns")
    for row in chain_backwards[dropped_user_index + 1:]:
        if row.get("type") == "assistant" and isinstance(row.get("uuid"), str):
            return row
    raise RuntimeError("no assistant checkpoint exists before the requested turns")


def target_assistant_before_fusion_block(chain_backwards, fusion_prompt):
    target_prompt = command_prompt_text(fusion_prompt).strip()
    if not is_direct_fusion_prompt(target_prompt):
        return target_assistant(chain_backwards, 1)

    dropped_user_index = None
    saw_target_fusion = False
    for index, row in enumerate(chain_backwards):
        if not is_human_prompt(row):
            continue
        text = command_prompt_text(human_prompt_text(row)).strip()
        if text == target_prompt:
            dropped_user_index = index
            saw_target_fusion = True
            continue
        if saw_target_fusion:
            break

    if dropped_user_index is None:
        return target_assistant(chain_backwards, 1)
    for row in chain_backwards[dropped_user_index + 1:]:
        if row.get("type") == "assistant" and isinstance(row.get("uuid"), str):
            return row
    raise RuntimeError("no assistant checkpoint exists before the fusion command block")


def matching_direct_fusion_row_text(row):
    if not is_human_prompt(row):
        return ""
    text = command_prompt_text(human_prompt_text(row)).strip()
    return text if is_direct_fusion_prompt(text) else ""


def latest_fusion_command_row(source_rows, sid, fusion_prompt):
    target_prompt = command_prompt_text(fusion_prompt).strip()
    latest_index = None
    for index, row in enumerate(source_rows):
        if row.get("sessionId") != sid:
            continue
        if matching_direct_fusion_row_text(row) == target_prompt:
            latest_index = index
    if latest_index is None:
        return None

    # If the same /fusion command has already failed/retried in this session,
    # roll back before the oldest matching prompt in that contiguous block.
    rollback_index = latest_index
    for index in range(latest_index - 1, -1, -1):
        row = source_rows[index]
        if row.get("sessionId") != sid or not is_human_prompt(row):
            continue
        text = matching_direct_fusion_row_text(row)
        if text == target_prompt:
            rollback_index = index
            continue
        break
    return source_rows[rollback_index]


def checkpoint_before_row(row, by_uuid):
    parent = row.get("parentUuid")
    current = by_uuid.get(parent) if isinstance(parent, str) else None
    fallback = current
    seen = set()
    while current is not None:
        current_uuid = current.get("uuid")
        if not isinstance(current_uuid, str) or current_uuid in seen:
            break
        if current.get("type") == "assistant":
            return current
        seen.add(current_uuid)
        parent = current.get("parentUuid")
        current = by_uuid.get(parent) if isinstance(parent, str) else None
    if fallback is not None and isinstance(fallback.get("uuid"), str):
        return fallback
    raise RuntimeError("no checkpoint exists before the fusion command")


def target_checkpoint_before_fusion(source_rows, by_uuid, chain_backwards, sid, fusion_prompt):
    command_row = latest_fusion_command_row(source_rows, sid, fusion_prompt)
    if command_row is not None:
        return checkpoint_before_row(command_row, by_uuid)
    return target_assistant_before_fusion_block(chain_backwards, fusion_prompt)


def find_new_transcript(root, sid):
    matches = [p.resolve() for p in (root / "projects").glob(f"*/{sid}.jsonl") if p.is_file()]
    return matches[0] if len(matches) == 1 else None


def new_session_launch_args():
    new_sid = str(uuid.uuid4())
    return new_sid, ["--session-id", new_sid]


def fork_launch_args(base_session, workdir, rollback_to_previous_turn, rollback_prompt="", fork_title="fusion fork"):
    source = resolve_claude_session(base_session, workdir)
    sid = source.stem
    checkpoint_uuid = ""
    if rollback_to_previous_turn:
        by_uuid, source_rows = build_uuid_index(source.parent, source)
        leaf = find_leaf(source_rows, by_uuid, sid)
        chain = walk_back(leaf, by_uuid)
        checkpoint = target_checkpoint_before_fusion(source_rows, by_uuid, chain, sid, rollback_prompt)
        checkpoint_uuid = checkpoint["uuid"]
    result = subprocess.run(
        [SDK_FORK_BIN, sid, workdir, checkpoint_uuid, fork_title],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"Claude Agent SDK could not fork session: {detail}")
    try:
        payload = json.loads(result.stdout)
        new_sid = payload["sessionId"]
    except Exception as exc:
        raise RuntimeError(f"Claude Agent SDK returned invalid fork result: {result.stdout!r}") from exc
    if find_new_transcript(claude_config_root(), new_sid) is None:
        raise RuntimeError(f"Claude Agent SDK reported success but fork transcript was not found: {new_sid}")
    return new_sid, ["--resume", new_sid]


def stream_json_answer(stdout):
    answer = ""
    session_id = ""
    result_payload = None
    json_errors = 0
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            json_errors += 1
            continue
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("session_id"), str):
            session_id = row["session_id"]
        if row.get("type") == "assistant":
            text = assistant_text(row)
            if text:
                answer = text
        elif row.get("type") == "result":
            result_payload = row
            result_text = row.get("result")
            if isinstance(result_text, str) and result_text.strip():
                answer = result_text
    return answer.strip(), session_id, result_payload, json_errors


def process_error_text(returncode, stderr, result_payload, timed_out):
    if timed_out:
        return "timed out"
    if isinstance(result_payload, dict) and result_payload.get("is_error"):
        errors = result_payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        result = result_payload.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        subtype = result_payload.get("subtype")
        return f"Claude Code returned error result: {subtype or 'unknown'}"
    if returncode != 0:
        detail = (stderr or "").strip()
        return detail or f"process exited with {returncode}"
    return ""


def terminate_process_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()


def run_headless_agent(agent, timeout_s, result_dir):
    label = agent["label"]
    stdout_path = result_dir / f"{label}.stdout.jsonl"
    stderr_path = result_dir / f"{label}.stderr.txt"
    summary_path = result_dir / f"{label}.summary.json"
    started = time.time()
    timed_out = False
    stdout = ""
    stderr = ""
    returncode = 1
    try:
        proc_env = os.environ.copy()
        for key in agent.get("env_unset") or []:
            proc_env.pop(key, None)
        proc_env.update(agent.get("env") or {})
        proc = subprocess.Popen(
            agent["argv"],
            cwd=agent["workdir"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_group(proc)
            stdout, stderr = proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else -1
    except FileNotFoundError as exc:
        stderr = str(exc)
        returncode = 127
    except Exception as exc:
        stderr = str(exc)
        returncode = 1

    stdout_path.write_text(stdout or "", encoding="utf-8")
    stderr_path.write_text(stderr or "", encoding="utf-8")

    answer, observed_session_id, result_payload, json_errors = stream_json_answer(stdout)
    error = process_error_text(returncode, stderr, result_payload, timed_out)
    complete = returncode == 0 and not error and bool(answer)
    duration_ms = int((time.time() - started) * 1000)
    summary = {
        "session_title": agent["title"],
        "prompts": [{"prompt": agent["topic"]}],
        "last_assistant": answer,
        "complete": complete,
        "stops": [{"event": "HeadlessPrint", "returncode": returncode}],
        "error": error,
        "timed_out": timed_out,
        "json_errors": json_errors,
        "duration_ms": duration_ms,
        "session_id": observed_session_id or agent["session_id"],
        "argv": agent["argv"],
        "fallback": agent.get("fallback", ""),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return (agent["session_id"], summary_path, summary)


def make_headless_job(label, cmd, title, topic, workdir, session_id, launch_args, system_prompt, *, env=None, env_unset=None, extra_args=None, fallback=""):
    argv = (
        shlex.split(cmd)
        + list(extra_args or [])
        + [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--name", title,
            "--disable-slash-commands",
            "--append-system-prompt", system_prompt,
        ]
        + list(launch_args)
        + [topic]
    )
    return {
        "label": label,
        "title": title,
        "argv": argv,
        "workdir": workdir,
        "topic": topic,
        "session_id": session_id,
        "env": env or {},
        "env_unset": env_unset or [],
        "fallback": fallback,
    }


def auth_error_needs_proxy_retry(label, summary):
    if label != "claude" or summary_is_complete(summary):
        return False
    error = (summary.get("error") or summary.get("last_assistant") or "").lower()
    return "not logged in" in error or "authentication_failed" in error


def proxy_retry_env():
    return {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
        "ANTHROPIC_API_KEY": "PROXY_MANAGED",
    }


def completed_titles(rows):
    return {
        summary.get("session_title")
        for _, _, summary in rows
        if summary_is_complete(summary)
    }


def summary_is_complete(summary):
    if not bool((summary.get("last_assistant") or "").strip()):
        return False
    if summary.get("complete") is False:
        return False
    return summary.get("complete") is True or len(summary.get("stops") or []) >= 1


def assistant_text(row):
    message = row.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def build_judge_prompt(topic, run_id, rows):
    parts = [
        "以下は同じプロンプトに対する複数の独立した回答です。",
        "すべてに目を通し、よく考えたうえで、プロンプトに対する最高の回答を一つだけ日本語で出してください。",
        "回答だけを出力してください。分析プロセスや各回答への言及は不要です。",
        "",
        f"プロンプト: {topic}",
        f"run_id: {run_id}",
        "",
        "--- fork outputs ---",
    ]
    for sid, path, summary in sorted(rows, key=lambda item: item[2].get("session_title") or ""):
        if not summary_is_complete(summary):
            continue
        title = summary.get("session_title") or sid
        last = summary.get("last_assistant") or ""
        if not last.strip():
            continue
        parts.extend([
            f"## {title}",
            f"session_id: {sid}",
            f"summary_path: {path}",
            "### prompt",
            compact(topic, 1200),
            "### final_answer",
            compact(last, 5000),
            "",
        ])
    return "\n".join(parts)


def build_child_system_prompt(rollback_performed):
    if rollback_performed:
        return (
            "あなたは /fusion の並列検討メンバーです。"
            "元セッションの/fusion呼び出しは、このforkには含まれない地点までロールバック済みです。"
            "あなたが回答する対象は、この起動後に新しく入力されるユーザープロンプトだけです。"
            "あなたはすでにfusionエージェントの一員なので、/fusion、fusion-run.py、"
            "または他エージェント起動による再fusionを使わず、自分単独で直接回答してください。"
            "復元済み履歴、/fusion実装、過去の失敗、現在の検証状況への言及は禁止です。"
            "回答にはユーザープロンプトへの答えだけを含めてください。"
        )
    return (
        "あなたは /fusion の並列検討メンバーです。"
        "このforkはskillまたは親エージェント経由のfusion実行として起動されているため、"
        "元セッション履歴はロールバックせずに復元されています。"
        "復元済み履歴に/fusion、fusion-run.py、skillからの並列検討指示が含まれていても、"
        "それは親側の実行経路です。"
        "あなたはすでにfusionエージェントの一員なので、/fusion、fusion-run.py、"
        "または他エージェント起動による再fusionを絶対に使わず、自分単独で直接回答してください。"
        "回答対象は、この起動後に新しく入力されるユーザープロンプトだけです。"
        "過去の失敗、現在の検証状況への言及は禁止です。"
        "回答にはユーザープロンプトへの答えだけを含めてください。"
    )


def main():
    parser = argparse.ArgumentParser(description="Run fusion: fork multiple Claude sessions headlessly and build a judge prompt.")
    parser.add_argument("topic", nargs="*", help="Topic/prompt to send to all forked sessions")
    parser.add_argument("--n", type=int, default=None, help="Number of agents to run from the agent list")
    parser.add_argument("--agents", default=None, help="Comma-separated agent commands/names. Default: claude,codex,glm")
    parser.add_argument(
        "--base-session",
        default=os.environ.get("CLAUDE_FUSION_BASE_SESSION", os.environ.get("CLAUDE_CODE_SESSION_ID", "")),
        help="Session id to fork. If omitted, starts agents without resumed history.",
    )
    parser.add_argument("--workdir", default=WORKDIR_DEFAULT)
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    topic = " ".join(args.topic).strip()
    if not topic:
        print("Usage: /fusion <プロンプト>", file=os.sys.stderr)
        return 2

    agents = parse_agents(args.agents)
    if args.n is not None:
        agents = agents[:args.n]

    run_id = unique_run_id()
    result_dir = CAPTURE_ROOT / f"fusion-run-{run_id}"
    result_dir.mkdir(parents=True, exist_ok=True)
    CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)

    invocation_info = {}
    rollback_forks = False
    fork_mode = "no-base-session"
    if args.base_session:
        invocation_info = fusion_invocation_info(args.base_session, args.workdir)
        rollback_forks = invocation_info["direct_fusion"]
        fork_mode = "rollback-direct-fusion" if rollback_forks else "plain-fork-skill-or-nested"

    system_prompt = build_child_system_prompt(rollback_forks)

    fork_session_ids = {}
    agent_jobs = []
    agent_commands = {}

    for label, cmd in agents:
        title = f"fusion-{label}-{run_id}"
        fork_args = []
        if args.base_session:
            resume_session, fork_args = fork_launch_args(
                args.base_session,
                args.workdir,
                rollback_forks,
                invocation_info.get("latest_prompt", ""),
                title,
            )
        else:
            resume_session, fork_args = new_session_launch_args()
        fork_session_ids[label] = resume_session
        agent_commands[label] = cmd
        agent_jobs.append(
            make_headless_job(
                label,
                cmd,
                title,
                topic,
                args.workdir,
                resume_session,
                fork_args,
                system_prompt,
            )
        )

    rows = []
    with ThreadPoolExecutor(max_workers=len(agent_jobs)) as executor:
        futures = [executor.submit(run_headless_agent, job, args.timeout, result_dir) for job in agent_jobs]
        for future in as_completed(futures):
            rows.append(future.result())

    retried_labels = []
    by_title = {summary.get("session_title"): (sid, path, summary) for sid, path, summary in rows}
    for label, _cmd in agents:
        title = f"fusion-{label}-{run_id}"
        row = by_title.get(title)
        if row is None or not auth_error_needs_proxy_retry(label, row[2]):
            continue
        if args.base_session:
            retry_session, retry_args = fork_launch_args(
                args.base_session,
                args.workdir,
                rollback_forks,
                invocation_info.get("latest_prompt", ""),
                title,
            )
        else:
            retry_session, retry_args = new_session_launch_args()
        fork_session_ids[label] = retry_session
        retry_job = make_headless_job(
            label,
            agent_commands[label],
            title,
            topic,
            args.workdir,
            retry_session,
            retry_args,
            system_prompt,
            env=proxy_retry_env(),
            env_unset=["ANTHROPIC_AUTH_TOKEN"],
            extra_args=["--model", "opus"],
            fallback="proxy-auth-retry",
        )
        retry_row = run_headless_agent(retry_job, args.timeout, result_dir)
        by_title[title] = retry_row
        retried_labels.append(label)
    rows = list(by_title.values())

    expected = {f"fusion-{label}-{run_id}" for label, _ in agents}
    completed = completed_titles(rows)
    missing = expected - completed
    capture_complete = expected <= completed

    judge_prompt = build_judge_prompt(topic, run_id, rows)
    (result_dir / "judge-prompt.md").write_text(judge_prompt, encoding="utf-8")

    (result_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "execution_mode": "headless-print",
        "topic": topic,
        "expected_titles": sorted(expected),
        "captured_titles": sorted(completed),
        "incomplete_titles": sorted(missing),
        "complete": capture_complete,
        "fork_mode": fork_mode,
        "rollback_performed": rollback_forks,
        "source_latest_prompt": compact(invocation_info.get("latest_prompt", ""), 1200),
        "source_session_prompt": compact(invocation_info.get("source_prompt", ""), 1200),
        "history_latest_prompt": compact(invocation_info.get("history_prompt", ""), 1200),
        "fork_session_ids": fork_session_ids,
        "retried_labels": retried_labels,
        "agent_results": [
            {
                "session_id": sid,
                "summary_path": str(path),
                "session_title": summary.get("session_title"),
                "complete": summary_is_complete(summary),
                "error": summary.get("error") or "",
                "duration_ms": summary.get("duration_ms"),
            }
            for sid, path, summary in rows
        ],
        "judge_prompt": str(result_dir / "judge-prompt.md"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"FUSION_RUN_ID={run_id}")
    print("EXECUTION_MODE=headless-print")
    print(f"FORK_MODE={fork_mode}")
    print(f"CAPTURED={len(completed)}/{len(expected)}")
    if missing:
        print("MISSING=" + ",".join(sorted(missing)))
    print(f"JUDGE_PROMPT={result_dir / 'judge-prompt.md'}")
    print("\n--- paste the following into main judge, or read the file above ---\n")
    print(judge_prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
