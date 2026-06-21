#!/usr/bin/env python3
import argparse, json, os, shlex, subprocess, time
from pathlib import Path

HOME = Path.home()
CAPTURE_ROOT = HOME / ".claude" / "session-captures"
ENABLE_FILE = CAPTURE_ROOT / "enabled"
DEFAULT_AGENTS = [("claude", "claude"), ("codex", "claude-codex"), ("glm", "claude-glm")]


def run(cmd, check=True):
    r = subprocess.run(cmd, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"failed: {shlex.join(cmd)}\nstdout={r.stdout}\nstderr={r.stderr}")
    return r


def tmux(*args, check=True):
    return run(["tmux", *args], check=check)


def parse_agents(value):
    if not value:
        return DEFAULT_AGENTS
    mapping = {"claude": "claude", "codex": "claude-codex", "claude-codex": "claude-codex", "glm": "claude-glm", "claude-glm": "claude-glm"}
    out = []
    for raw in value.split(","):
        name = raw.strip()
        if name:
            out.append((name.replace("claude-", ""), mapping.get(name, name)))
    return out or DEFAULT_AGENTS


def collect(run_id, agents, timeout):
    expected = {f"fusion-{label}-{run_id}" for label, _ in agents}
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        by_title = {}
        for summary_path in CAPTURE_ROOT.glob("*/summary.json"):
            try:
                s = json.loads(summary_path.read_text())
            except Exception:
                continue
            title = s.get("session_title") or ""
            if title not in expected or not s.get("prompts"):
                continue
            item = (summary_path.parent.name, summary_path, s)
            cur = by_title.get(title)
            if cur is None or summary_path.stat().st_mtime > cur[1].stat().st_mtime:
                by_title[title] = item
        rows = list(by_title.values())
        last = rows
        done = {s.get("session_title") for _, _, s in rows if len(s.get("stops") or []) >= 1}
        if expected <= done:
            return rows
        time.sleep(2)
    return last


def compact(text, limit):
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def judge_prompt(topic, run_id, rows):
    parts = [
        "あなたは /fusion のmain judgeです。以下は同じお題に対する複数forkの回答とtool証跡です。",
        "各案を批判的に、ただしリスペクトを持って評価し、良い部分は取り入れてください。",
        "最後に、あなた自身も独立に考えたうえで、最終結論を日本語で出してください。",
        "", f"お題: {topic}", f"run_id: {run_id}", "", "--- fork outputs ---"
    ]
    for sid, path, s in sorted(rows, key=lambda x: x[2].get("session_title") or ""):
        title = s.get("session_title") or sid
        prompt = ((s.get("prompts") or [{}])[-1].get("prompt") if s.get("prompts") else "") or ""
        tr = s.get("tool_results") or []
        resp = (tr[-1].get("tool_response") if tr and isinstance(tr[-1], dict) else {}) or {}
        parts += [
            f"## {title}", f"session_id: {sid}", f"summary_path: {path}",
            "### prompt", compact(prompt, 1200),
            "### last_tool_stdout", compact(resp.get("stdout") or "", 1200),
            "### last_tool_stderr", compact(resp.get("stderr") or "", 600),
            "### final_answer", compact(s.get("last_assistant") or "", 5000), ""
        ]
    parts += ["--- judge instructions ---", "1. 各forkの良い点を拾う。", "2. 各forkの弱点・見落としを指摘する。", "3. 共通点と相違点を整理する。", "4. 最後に自分自身の結論を出す。単なる多数決にはしない。"]
    return "\n".join(parts)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("topic", nargs="*")
    p.add_argument("--n", type=int)
    p.add_argument("--agents")
    p.add_argument("--base-session", default=os.environ.get("CLAUDE_FUSION_BASE_SESSION"), help="Session id to resume. If omitted, uses --continue.")
    p.add_argument("--workdir", default=os.getcwd())
    p.add_argument("--timeout", type=int, default=240)
    args = p.parse_args()
    topic = " ".join(args.topic).strip()
    if not topic:
        print("Usage: /fusion <topic>", file=os.sys.stderr)
        return 2
    agents = parse_agents(args.agents)
    if args.n:
        agents = agents[:args.n]
    run_id = time.strftime("%Y%m%d-%H%M%S")
    session = f"fusion-{run_id}"
    CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
    ENABLE_FILE.touch()
    tmux("kill-session", "-t", session, check=False)
    tmux("new-session", "-d", "-s", session, "-n", agents[0][0], "-c", args.workdir)
    for label, _ in agents[1:]:
        tmux("new-window", "-t", f"{session}:", "-n", label, "-c", args.workdir)
    for i, (label, cmd) in enumerate(agents):
        title = f"fusion-{label}-{run_id}"
        resume = f"--resume {shlex.quote(args.base_session)}" if args.base_session else "--continue"
        command = f"CLAUDE_TRANSCRIPT_CAPTURE=1 {cmd} --name {shlex.quote(title)} {resume} --fork-session"
        tmux("send-keys", "-t", f"{session}:{i}", command, "C-m")
        time.sleep(2)
    time.sleep(10)
    member_prompt = "あなたは /fusion の並列検討メンバーです。必要ならBash toolを1回だけ使って作業ディレクトリ等を確認してください。その後、次のお題について、立場・理由・リスク/反論・最終提案を簡潔に述べてください。他メンバーの回答は見えない前提で独立に考えてください。\n\nお題: " + topic
    for i in range(len(agents)):
        tmux("send-keys", "-t", f"{session}:{i}", member_prompt, "C-m")
        time.sleep(0.5)
        tmux("send-keys", "-t", f"{session}:{i}", "Enter")
    rows = collect(run_id, agents, args.timeout)
    expected = {f"fusion-{label}-{run_id}" for label, _ in agents}
    got = {s.get("session_title") for _, _, s in rows}
    outdir = CAPTURE_ROOT / f"fusion-run-{run_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    prompt = judge_prompt(topic, run_id, rows)
    (outdir / "judge-prompt.md").write_text(prompt, encoding="utf-8")
    (outdir / "manifest.json").write_text(json.dumps({"run_id": run_id, "tmux_session": session, "topic": topic, "expected_titles": sorted(expected), "captured_titles": sorted(x for x in got if x), "complete": expected <= got, "judge_prompt": str(outdir / "judge-prompt.md")}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FUSION_RUN_ID={run_id}")
    print(f"TMUX_SESSION={session}")
    print(f"CAPTURED={len(rows)}/{len(expected)}")
    if expected - got:
        print("MISSING=" + ",".join(sorted(expected - got)))
    print(f"JUDGE_PROMPT={outdir / 'judge-prompt.md'}")
    print("\n--- judge prompt ---\n")
    print(prompt)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
