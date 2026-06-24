#!/usr/bin/env python3
import argparse
import json
import os
import re
import signal
import shlex
import shutil
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
SDK_DELETE_BIN = os.environ.get("CLAUDE_FUSION_SDK_DELETE_BIN", str(Path(__file__).resolve().parent / "fusion-sdk-delete.mjs"))

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


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_json_file(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def process_base_env():
    env = os.environ.copy()
    local_bin = str(HOME / ".local" / "bin")
    path_parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if local_bin not in path_parts:
        env["PATH"] = local_bin + (os.pathsep + env["PATH"] if env.get("PATH") else "")
    return env


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


def path_has_fusion_part(path):
    return any("fusion" in part.lower() for part in Path(path).parts)


def file_mentions_fusion(path, limit=12000):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")[:limit].lower()
    except Exception:
        return False
    return any(token in text for token in ("/fusion", "fusion-run.py", "fusion command", "fusionエージェント"))


def link_path(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except OSError:
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def mirror_commands_without_fusion(src, dst):
    excluded = []
    if not src.is_dir():
        return excluded
    for child in sorted(src.rglob("*")):
        rel = child.relative_to(src)
        target = dst / rel
        if child.is_dir():
            if path_has_fusion_part(rel):
                excluded.append(str(rel))
                continue
            target.mkdir(parents=True, exist_ok=True)
            continue
        if path_has_fusion_part(rel) or file_mentions_fusion(child):
            excluded.append(str(rel))
            continue
        link_path(child, target)
    return sorted(set(excluded))


def skill_dir_is_fusion(path, rel):
    if path_has_fusion_part(rel):
        return True
    skill_file = path / "SKILL.md"
    return skill_file.is_file() and file_mentions_fusion(skill_file)


def mirror_skills_without_fusion(src, dst):
    excluded = []
    if not src.is_dir():
        return excluded

    def visit(current, rel):
        if current.is_dir() and skill_dir_is_fusion(current, rel):
            excluded.append(str(rel) or current.name)
            return
        if current.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for child in sorted(current.iterdir()):
                visit(child, rel / child.name)
            return
        if path_has_fusion_part(rel):
            excluded.append(str(rel))
            return
        link_path(current, dst / rel)

    for child in sorted(src.iterdir()):
        visit(child, Path(child.name))
    return sorted(set(excluded))


def prepare_child_claude_config(result_dir):
    source = claude_config_root()
    target = result_dir / "child-claude-config"
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    excluded_commands = []
    excluded_skills = []
    if source.is_dir():
        for child in sorted(source.iterdir()):
            name = child.name
            if name == "commands":
                excluded_commands = mirror_commands_without_fusion(child, target / name)
            elif name == "skills":
                excluded_skills = mirror_skills_without_fusion(child, target / name)
            elif name == "session-captures":
                continue
            else:
                link_path(child, target / name)

    return {
        "source_config_dir": str(source),
        "child_config_dir": str(target),
        "fusion_command_disabled": True,
        "fusion_skills_disabled": True,
        "excluded_commands": excluded_commands,
        "excluded_skills": excluded_skills,
    }


def unique_values(values):
    seen = set()
    result = []
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def cleanup_child_session(config_root, session_id):
    root = Path(config_root)
    deleted = []
    missing = []
    errors = []

    sdk_result = run([SDK_DELETE_BIN, session_id, str(root)], check=False)
    if sdk_result.returncode == 0:
        try:
            payload = json.loads(sdk_result.stdout)
        except json.JSONDecodeError:
            payload = {}
        deleted.append(f"sdk:deleteSession:{payload.get('sessionId') or session_id}")
    else:
        detail = (sdk_result.stderr or sdk_result.stdout or "").strip() or f"exit status {sdk_result.returncode}"
        errors.append({"path": f"sdk:deleteSession:{session_id}", "error": detail})

    tasks = root / "tasks" / session_id
    try:
        if tasks.is_symlink() or tasks.is_file():
            tasks.unlink()
            deleted.append(str(tasks))
        elif tasks.is_dir():
            shutil.rmtree(tasks)
            deleted.append(str(tasks))
        else:
            missing.append(str(tasks))
    except Exception as exc:
        errors.append({"path": str(tasks), "error": str(exc)})

    remaining_projects = []
    projects = root / "projects"
    if projects.is_dir():
        remaining_projects = [str(path) for path in projects.glob(f"*/{session_id}.jsonl")]

    return {
        "session_id": session_id,
        "deleted_paths": deleted,
        "missing_paths": missing,
        "errors": errors,
        "remaining_project_paths": remaining_projects,
    }


def cleanup_child_sessions(config_root, session_ids):
    return [cleanup_child_session(config_root, sid) for sid in unique_values(session_ids)]


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
        env=process_base_env(),
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
        proc_env = process_base_env()
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


def label_from_title(title, run_id):
    prefix = "fusion-"
    suffix = f"-{run_id}"
    if isinstance(title, str) and title.startswith(prefix) and title.endswith(suffix):
        return title[len(prefix):-len(suffix)]
    return ""


def run_id_from_dir(path):
    name = Path(path).name
    prefix = "fusion-run-"
    return name[len(prefix):] if name.startswith(prefix) else name


def agent_result_payload(sid, path, summary, run_id):
    title = summary.get("session_title") or ""
    label = label_from_title(title, run_id) or Path(path).name.removesuffix(".summary.json")
    stops = summary.get("stops") or []
    stop = stops[-1] if stops and isinstance(stops[-1], dict) else {}
    return {
        "label": label,
        "session_id": summary.get("session_id") or sid,
        "summary_path": str(path),
        "stdout_path": summary.get("stdout_path") or str(Path(path).with_name(f"{label}.stdout.jsonl")),
        "stderr_path": summary.get("stderr_path") or str(Path(path).with_name(f"{label}.stderr.txt")),
        "session_title": title,
        "complete": summary_is_complete(summary),
        "error": summary.get("error") or "",
        "timed_out": bool(summary.get("timed_out")),
        "duration_ms": summary.get("duration_ms"),
        "fallback": summary.get("fallback") or "",
        "returncode": stop.get("returncode"),
    }


def manifest_payload(
    *,
    run_id,
    result_dir,
    topic,
    agents,
    rows,
    fork_mode,
    rollback_forks,
    invocation_info,
    fork_session_ids,
    retried_labels,
    status,
    workdir,
    agent_jobs=None,
    started_at="",
    child_config_info=None,
    child_session_cleanup=None,
):
    expected = {f"fusion-{label}-{run_id}" for label, _ in agents}
    completed = completed_titles(rows)
    missing = expected - completed
    agent_results = [agent_result_payload(sid, path, summary, run_id) for sid, path, summary in rows]
    seen_titles = {item.get("session_title") for item in agent_results}
    for job in agent_jobs or []:
        if job["title"] in seen_titles:
            continue
        label = job["label"]
        agent_results.append({
            "label": label,
            "session_id": fork_session_ids.get(label, job["session_id"]),
            "summary_path": str(result_dir / f"{label}.summary.json"),
            "stdout_path": str(result_dir / f"{label}.stdout.jsonl"),
            "stderr_path": str(result_dir / f"{label}.stderr.txt"),
            "session_title": job["title"],
            "complete": False,
            "error": "",
            "timed_out": False,
            "duration_ms": None,
            "fallback": job.get("fallback", ""),
            "returncode": None,
        })
    for item in agent_results:
        label = item.get("label")
        if label in fork_session_ids:
            item["session_id"] = fork_session_ids[label]
    agent_results.sort(key=lambda item: item.get("label") or item.get("session_title") or "")
    return {
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "updated_at": now_iso(),
        "execution_mode": "headless-print",
        "result_dir": str(result_dir),
        "workdir": str(Path(workdir).resolve()),
        "child_config": child_config_info or {},
        "topic": topic,
        "expected_titles": sorted(expected),
        "captured_titles": sorted(completed),
        "incomplete_titles": sorted(missing),
        "complete": expected <= completed,
        "fork_mode": fork_mode,
        "rollback_performed": rollback_forks,
        "source_session": invocation_info.get("source_session", ""),
        "source_path": invocation_info.get("source_path", ""),
        "source_latest_prompt": compact(invocation_info.get("latest_prompt", ""), 1200),
        "source_session_prompt": compact(invocation_info.get("source_prompt", ""), 1200),
        "history_latest_prompt": compact(invocation_info.get("history_prompt", ""), 1200),
        "fork_session_ids": dict(fork_session_ids),
        "retried_labels": list(retried_labels),
        "child_session_cleanup": child_session_cleanup or {},
        "agent_results": agent_results,
        "judge_prompt": str(result_dir / "judge-prompt.md"),
    }


def resolve_run_dir(spec):
    value = spec or "latest"
    if value in {"latest", "current"}:
        dirs = sorted(
            [p for p in CAPTURE_ROOT.glob("fusion-run-*") if p.is_dir()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not dirs:
            raise RuntimeError(f"no fusion run directories under {CAPTURE_ROOT}")
        return dirs[0]
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    name = value if value.startswith("fusion-run-") else f"fusion-run-{value}"
    candidate = CAPTURE_ROOT / name
    if candidate.is_dir():
        return candidate.resolve()
    raise RuntimeError(f"fusion run not found: {value}")


def load_run_summaries(run_dir):
    summaries = {}
    for path in sorted(Path(run_dir).glob("*.summary.json")):
        label = path.name.removesuffix(".summary.json")
        value = read_json_file(path)
        if isinstance(value, dict) and value:
            summaries[label] = value
    return summaries


def running_fusion_processes(run_id):
    result = run(["ps", "-axo", "pid=,etime=,command="], check=False)
    if result.returncode != 0:
        return {}
    pattern = re.compile(r"(?:^|\s)--name(?:=|\s+)([^\s\"']*fusion-[^\s\"']*-" + re.escape(run_id) + r")\b")
    found = {}
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == current_pid:
            continue
        elapsed = parts[1]
        command = parts[2]
        for match in pattern.findall(command):
            title = match.strip("'\"")
            found[title] = {"pid": pid, "elapsed": elapsed}
    return found


def format_duration(duration_ms):
    if duration_ms is None:
        return "-"
    try:
        seconds = int(duration_ms) / 1000
    except (TypeError, ValueError):
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{seconds:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h{int(minutes):02d}m{seconds:04.1f}s"


def one_line(text, limit=240):
    value = (text or "").replace("\r", " ").replace("\n", " ").strip()
    return compact(value, limit)


def format_status_text(run_dir, manifest, summaries, running):
    run_id = manifest.get("run_id") or run_id_from_dir(run_dir)
    expected = set(manifest.get("expected_titles") or [])
    captured = set(manifest.get("captured_titles") or [])
    running_titles = set(running.keys())
    state = "running" if running_titles else ("complete" if manifest.get("complete") else "incomplete")
    agent_rows = {}

    for item in manifest.get("agent_results") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or label_from_title(item.get("session_title"), run_id)
        if label:
            agent_rows[label] = dict(item)

    for label, summary in summaries.items():
        title = summary.get("session_title") or f"fusion-{label}-{run_id}"
        agent_rows[label] = {
            **agent_rows.get(label, {}),
            **agent_result_payload(summary.get("session_id") or "", Path(run_dir) / f"{label}.summary.json", summary, run_id),
            "session_title": title,
        }

    for title in running_titles:
        label = label_from_title(title, run_id)
        if label and label not in agent_rows:
            agent_rows[label] = {
                "label": label,
                "session_title": title,
                "session_id": (manifest.get("fork_session_ids") or {}).get(label, ""),
                "summary_path": str(Path(run_dir) / f"{label}.summary.json"),
                "stdout_path": str(Path(run_dir) / f"{label}.stdout.jsonl"),
                "stderr_path": str(Path(run_dir) / f"{label}.stderr.txt"),
                "complete": False,
                "error": "",
                "duration_ms": None,
                "fallback": "",
            }

    for label, sid in (manifest.get("fork_session_ids") or {}).items():
        agent_rows.setdefault(label, {
            "label": label,
            "session_title": f"fusion-{label}-{run_id}",
            "session_id": sid,
            "summary_path": str(Path(run_dir) / f"{label}.summary.json"),
            "stdout_path": str(Path(run_dir) / f"{label}.stdout.jsonl"),
            "stderr_path": str(Path(run_dir) / f"{label}.stderr.txt"),
            "complete": False,
            "error": "",
            "duration_ms": None,
            "fallback": "",
        })

    if not expected and agent_rows:
        expected = {row.get("session_title") for row in agent_rows.values() if row.get("session_title")}
    if not captured:
        captured = {
            row.get("session_title")
            for row in agent_rows.values()
            if row.get("session_title") and row.get("complete")
        }

    lines = [
        "Fusion status",
        f"run_id: {run_id}",
        f"state: {state}",
        f"phase: {manifest.get('status') or '-'}",
        f"captured: {len(captured)}/{len(expected) if expected else len(agent_rows)}",
        f"result_dir: {run_dir}",
    ]
    if manifest.get("updated_at"):
        lines.append(f"updated_at: {manifest.get('updated_at')}")
    if manifest.get("workdir"):
        lines.append(f"workdir: {manifest.get('workdir')}")
    if manifest.get("topic"):
        lines.append(f"prompt: {one_line(manifest.get('topic'), 500)}")
    if manifest.get("source_latest_prompt"):
        lines.append(f"source_latest_prompt: {one_line(manifest.get('source_latest_prompt'), 500)}")
    lines.extend([
        f"fork_mode: {manifest.get('fork_mode') or '-'}",
        f"rollback_performed: {bool(manifest.get('rollback_performed'))}",
        f"retried_labels: {', '.join(manifest.get('retried_labels') or []) or '-'}",
    ])
    child_config = manifest.get("child_config") or {}
    if child_config:
        lines.append(f"child_config_dir: {child_config.get('child_config_dir') or '-'}")
        lines.append(f"disabled_commands: {', '.join(child_config.get('excluded_commands') or []) or '-'}")
        lines.append(f"disabled_skills: {', '.join(child_config.get('excluded_skills') or []) or '-'}")
    cleanup = manifest.get("child_session_cleanup") or {}
    if cleanup:
        session_ids = cleanup.get("session_ids") or []
        cleanup_results = cleanup.get("results") or []
        result_session_ids = {item.get("session_id") for item in cleanup_results if isinstance(item, dict)}
        cleanup_errors = sum(len(item.get("errors") or []) for item in cleanup_results)
        remaining_paths = sum(len(item.get("remaining_project_paths") or []) for item in cleanup_results)
        cleanup_complete = (
            bool(cleanup.get("enabled"))
            and bool(session_ids)
            and set(session_ids) <= result_session_ids
            and cleanup_errors == 0
            and remaining_paths == 0
        )
        lines.append(f"child_session_cleanup_enabled: {bool(cleanup.get('enabled'))}")
        lines.append(f"child_sessions_deleted: {cleanup_complete}")
        if cleanup.get("kept_reason"):
            lines.append(f"child_sessions_kept_reason: {cleanup.get('kept_reason')}")
        if cleanup.get("session_ids"):
            lines.append(f"child_session_ids: {', '.join(cleanup.get('session_ids') or [])}")
        deleted_count = sum(len(item.get("deleted_paths") or []) for item in cleanup.get("results") or [])
        lines.append(f"child_session_deleted_paths: {deleted_count}")
        if cleanup_errors:
            lines.append(f"child_session_delete_errors: {cleanup_errors}")
    if manifest.get("judge_prompt"):
        lines.append(f"judge_prompt: {manifest.get('judge_prompt')}")

    lines.append("")
    lines.append("fork sessions:")
    fork_session_ids = manifest.get("fork_session_ids") or {}
    if fork_session_ids:
        for label in sorted(fork_session_ids):
            lines.append(f"  {label}: {fork_session_ids[label]}")
    else:
        lines.append("  -")

    lines.append("")
    lines.append("agents:")
    if not agent_rows:
        lines.append("  -")
    for label in sorted(agent_rows):
        row = agent_rows[label]
        title = row.get("session_title") or f"fusion-{label}-{run_id}"
        proc = running.get(title)
        complete = bool(row.get("complete"))
        error = row.get("error") or ""
        if proc and not complete:
            agent_state = f"running pid={proc['pid']} elapsed={proc['elapsed']}"
        elif complete:
            agent_state = "complete"
        elif error:
            agent_state = "failed"
        else:
            agent_state = "pending"
        suffix = []
        if row.get("session_id"):
            suffix.append(f"session={row.get('session_id')}")
        if row.get("duration_ms") is not None:
            suffix.append(f"duration={format_duration(row.get('duration_ms'))}")
        if row.get("fallback"):
            suffix.append(f"fallback={row.get('fallback')}")
        lines.append(f"  {label}: {agent_state}" + (f" ({', '.join(suffix)})" if suffix else ""))
        if error:
            lines.append(f"    error: {one_line(error, 500)}")
        lines.append(f"    summary: {row.get('summary_path')}")
        lines.append(f"    stdout: {row.get('stdout_path')}")
        lines.append(f"    stderr: {row.get('stderr_path')}")

    incomplete = manifest.get("incomplete_titles") or []
    if incomplete:
        lines.append("")
        lines.append("incomplete_titles:")
        for title in incomplete:
            lines.append(f"  {title}")
    return "\n".join(lines)


def status_payload(run_dir):
    run_id = run_id_from_dir(run_dir)
    manifest = read_json_file(Path(run_dir) / "manifest.json")
    if not manifest:
        manifest = {
            "run_id": run_id,
            "result_dir": str(run_dir),
            "complete": False,
            "agent_results": [],
            "fork_session_ids": {},
        }
    manifest.setdefault("run_id", run_id)
    manifest.setdefault("result_dir", str(run_dir))
    summaries = load_run_summaries(run_dir)
    running = running_fusion_processes(run_id)
    return {
        "run_dir": str(run_dir),
        "manifest": manifest,
        "summaries": summaries,
        "running_processes": running,
    }


def show_status(spec, output_json=False):
    run_dir = resolve_run_dir(spec)
    payload = status_payload(run_dir)
    if output_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_status_text(
            Path(payload["run_dir"]),
            payload["manifest"],
            payload["summaries"],
            payload["running_processes"],
        ))
    return 0


def manifest_child_session_ids(manifest):
    ids = []
    cleanup = manifest.get("child_session_cleanup") or {}
    ids.extend(cleanup.get("session_ids") or [])
    ids.extend((manifest.get("fork_session_ids") or {}).values())
    for item in manifest.get("agent_results") or []:
        if isinstance(item, dict):
            ids.append(item.get("session_id") or "")
    return unique_values(ids)


def cleanup_run_sessions(spec, output_json=False):
    run_dir = resolve_run_dir(spec)
    manifest_path = Path(run_dir) / "manifest.json"
    manifest = read_json_file(manifest_path)
    if not manifest:
        raise RuntimeError(f"manifest not found: {manifest_path}")

    session_ids = manifest_child_session_ids(manifest)
    config_root = ((manifest.get("child_config") or {}).get("source_config_dir") or str(claude_config_root()))
    cleanup = {
        "enabled": True,
        "manual": True,
        "manual_at": now_iso(),
        "source_config_dir": config_root,
        "session_ids": session_ids,
        "results": cleanup_child_sessions(config_root, session_ids),
        "kept_reason": "",
    }
    manifest["child_session_cleanup"] = cleanup
    manifest["updated_at"] = now_iso()
    write_json_file(manifest_path, manifest)

    if output_json:
        print(json.dumps(cleanup, ensure_ascii=False, indent=2))
    else:
        deleted_count = sum(len(item.get("deleted_paths") or []) for item in cleanup["results"])
        error_count = sum(len(item.get("errors") or []) for item in cleanup["results"])
        print(f"RUN_DIR={run_dir}")
        print(f"CHILD_SESSION_IDS={','.join(session_ids) if session_ids else '-'}")
        print(f"DELETED_PATHS={deleted_count}")
        if error_count:
            print(f"ERRORS={error_count}")
    return 0


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
    parser.add_argument("--keep-child-sessions", action="store_true", help="Keep forked Claude child sessions in ~/.claude/projects for debugging")
    parser.add_argument("--status", nargs="?", const="latest", help="Show the latest or specified fusion run status and exit")
    parser.add_argument("--cleanup-sessions", nargs="?", const="latest", help="Delete child Claude sessions for the latest or specified fusion run and exit")
    parser.add_argument("--json", action="store_true", help="With --status, output machine-readable JSON")
    args = parser.parse_args()
    if args.status is not None:
        return show_status(args.status, args.json)
    if args.cleanup_sessions is not None:
        return cleanup_run_sessions(args.cleanup_sessions, args.json)

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
    manifest_path = result_dir / "manifest.json"
    started_at = now_iso()
    child_config_info = prepare_child_claude_config(result_dir)
    child_env = {
        "CLAUDE_CONFIG_DIR": child_config_info["child_config_dir"],
        "CLAUDE_FUSION_CHILD": "1",
    }
    write_json_file(manifest_path, {
        "run_id": run_id,
        "status": "initializing",
        "started_at": started_at,
        "updated_at": started_at,
        "execution_mode": "headless-print",
        "result_dir": str(result_dir),
        "workdir": str(Path(args.workdir).resolve()),
        "child_config": child_config_info,
        "topic": topic,
        "expected_titles": [],
        "captured_titles": [],
        "incomplete_titles": [],
        "complete": False,
        "fork_mode": "initializing",
        "rollback_performed": False,
        "fork_session_ids": {},
        "retried_labels": [],
        "agent_results": [],
        "judge_prompt": str(result_dir / "judge-prompt.md"),
    })

    invocation_info = {}
    rollback_forks = False
    fork_mode = "no-base-session"
    if args.base_session:
        invocation_info = fusion_invocation_info(args.base_session, args.workdir)
        rollback_forks = invocation_info["direct_fusion"]
        fork_mode = "rollback-direct-fusion" if rollback_forks else "plain-fork-skill-or-nested"

    system_prompt = build_child_system_prompt(rollback_forks)

    fork_session_ids = {}
    child_session_ids = []
    agent_jobs = []
    agent_commands = {}
    retried_labels = []
    child_session_cleanup = {
        "enabled": not args.keep_child_sessions,
        "session_ids": [],
        "results": [],
        "kept_reason": "requested by --keep-child-sessions" if args.keep_child_sessions else "",
    }

    def update_manifest(status, rows):
        write_json_file(manifest_path, manifest_payload(
            run_id=run_id,
            result_dir=result_dir,
            topic=topic,
            agents=agents,
            rows=rows,
            fork_mode=fork_mode,
            rollback_forks=rollback_forks,
            invocation_info=invocation_info,
            fork_session_ids=fork_session_ids,
            retried_labels=retried_labels,
            status=status,
            workdir=args.workdir,
            agent_jobs=agent_jobs,
            started_at=started_at,
            child_config_info=child_config_info,
            child_session_cleanup=child_session_cleanup,
        ))

    update_manifest("forking", [])

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
        child_session_ids.append(resume_session)
        child_session_cleanup["session_ids"] = unique_values(child_session_ids)
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
                env=child_env,
            )
        )

    rows = []
    update_manifest("running", rows)
    with ThreadPoolExecutor(max_workers=len(agent_jobs)) as executor:
        futures = [executor.submit(run_headless_agent, job, args.timeout, result_dir) for job in agent_jobs]
        for future in as_completed(futures):
            rows.append(future.result())
            update_manifest("running", rows)

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
        child_session_ids.append(retry_session)
        child_session_cleanup["session_ids"] = unique_values(child_session_ids)
        retried_labels.append(label)
        update_manifest(f"retrying-{label}", list(by_title.values()))
        retry_job = make_headless_job(
            label,
            agent_commands[label],
            title,
            topic,
            args.workdir,
            retry_session,
            retry_args,
            system_prompt,
            env={**child_env, **proxy_retry_env()},
            env_unset=["ANTHROPIC_AUTH_TOKEN"],
            extra_args=["--model", "opus"],
            fallback="proxy-auth-retry",
        )
        retry_row = run_headless_agent(retry_job, args.timeout, result_dir)
        by_title[title] = retry_row
        update_manifest("running", list(by_title.values()))
    rows = list(by_title.values())

    expected = {f"fusion-{label}-{run_id}" for label, _ in agents}
    completed = completed_titles(rows)
    missing = expected - completed
    capture_complete = expected <= completed

    judge_prompt = build_judge_prompt(topic, run_id, rows)
    (result_dir / "judge-prompt.md").write_text(judge_prompt, encoding="utf-8")

    for sid, _path, summary in rows:
        child_session_ids.append(sid)
        child_session_ids.append(summary.get("session_id") or "")
    child_session_cleanup["session_ids"] = unique_values(child_session_ids)
    if args.keep_child_sessions:
        child_session_cleanup["enabled"] = False
        child_session_cleanup["kept_reason"] = "requested by --keep-child-sessions"
    else:
        child_session_cleanup["enabled"] = True
        child_session_cleanup["results"] = cleanup_child_sessions(
            child_config_info["source_config_dir"],
            child_session_cleanup["session_ids"],
        )

    update_manifest("complete" if capture_complete else "incomplete", rows)

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
