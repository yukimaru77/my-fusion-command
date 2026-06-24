#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${CCPC_BIN_DIR:-$HOME/.local/bin}"
claude_dir="${CCPC_CLAUDE_DIR:-$HOME/.claude}"
install_fusion="${CCPC_INSTALL_FUSION:-1}"

mkdir -p "$bin_dir"

find_cc_switch_bin() {
  local candidate
  for candidate in \
    "${CCSWITCH_BIN:-}" \
    "$HOME/tasks/ccswitch-codex-glm/src-tauri/target/debug/cc-switch" \
    "$HOME/tasks/ccswitch-codex-glm/src-tauri/target/release/cc-switch" \
    "$HOME/tasks/cc-switch/src-tauri/target/debug/cc-switch" \
    "$HOME/tasks/cc-switch/src-tauri/target/release/cc-switch" \
    "/Applications/CC Switch.app/Contents/MacOS/cc-switch"
  do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf "%s\n" "$candidate"
      return 0
    fi
  done

  command -v cc-switch 2>/dev/null || true
}

install_mode_755() {
  local src="$1"
  local dest="$2"
  install -m 755 "$src" "$dest"
}

is_our_claude_wrapper() {
  local path="$1"
  [ -f "$path" ] && grep -q "claude-provider-commands wrapper" "$path"
}

if [ -e "$bin_dir/claude" ] || [ -L "$bin_dir/claude" ]; then
  if ! is_our_claude_wrapper "$bin_dir/claude"; then
    if [ ! -e "$bin_dir/claude-real" ] && [ ! -L "$bin_dir/claude-real" ]; then
      mv "$bin_dir/claude" "$bin_dir/claude-real"
    else
      backup="$bin_dir/claude.backup.$(date +%Y%m%d%H%M%S)"
      mv "$bin_dir/claude" "$backup"
      echo "Backed up existing claude wrapper to $backup"
    fi
  fi
fi

cc_switch_bin="$(find_cc_switch_bin)"
if [ -z "$cc_switch_bin" ]; then
  cc_switch_bin=""
fi

install_mode_755 "$repo_root/bin/claude" "$bin_dir/claude"
install_mode_755 "$repo_root/bin/claude-codex" "$bin_dir/claude-codex"
install_mode_755 "$repo_root/bin/claude-glm" "$bin_dir/claude-glm"

escaped_bin="${cc_switch_bin//\\/\\\\}"
escaped_bin="${escaped_bin//&/\\&}"
escaped_bin="${escaped_bin//|/\\|}"
sed "s|@CC_SWITCH_BIN@|$escaped_bin|g" \
  "$repo_root/bin/ccswitch-claude-run.template" > "$bin_dir/ccswitch-claude-run"
chmod 755 "$bin_dir/ccswitch-claude-run"

if [ "$install_fusion" != "0" ]; then
  mkdir -p "$claude_dir/hooks" "$claude_dir/commands" "$claude_dir/fusion-sdk"
  install_mode_755 "$repo_root/fusion/hooks/collect-transcript.py" "$claude_dir/hooks/collect-transcript.py"
  install_mode_755 "$repo_root/fusion/hooks/capture-query.py" "$claude_dir/hooks/capture-query.py"
  install_mode_755 "$repo_root/fusion/hooks/fusion-run.py" "$claude_dir/hooks/fusion-run.py"
  install_mode_755 "$repo_root/fusion/hooks/fusion-sdk-fork.mjs" "$claude_dir/hooks/fusion-sdk-fork.mjs"
  install_mode_755 "$repo_root/fusion/hooks/fusion-sdk-delete.mjs" "$claude_dir/hooks/fusion-sdk-delete.mjs"
  install -m 644 "$repo_root/fusion/commands/fusion.md" "$claude_dir/commands/fusion.md"
  install -m 644 "$repo_root/fusion/fusion-sdk/package.json" "$claude_dir/fusion-sdk/package.json"

  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to install @anthropic-ai/claude-agent-sdk for /fusion rollback." >&2
    exit 69
  fi
  npm install --prefix "$claude_dir/fusion-sdk" --omit=dev >/dev/null

  CLAUDE_FUSION_HOOK_PATH="$claude_dir/hooks/collect-transcript.py" python3 - "$claude_dir/settings.json" <<'PY'
import json
import os
import sys
from pathlib import Path
path = Path(sys.argv[1])
hook_path = os.environ["CLAUDE_FUSION_HOOK_PATH"]
if path.exists():
    data = json.loads(path.read_text())
else:
    data = {}
hooks = data.setdefault("hooks", {})
entries = [
    ("SessionStart", 10),
    ("UserPromptSubmit", 10),
    ("PreToolUse", 10),
    ("PostToolUse", 30),
    ("PostToolUseFailure", 30),
    ("Stop", 30),
    ("SessionEnd", 30),
    ("MessageDisplay", 10),
]
for event, timeout in entries:
    arr = hooks.setdefault(event, [])
    command = f"{hook_path} {event}"
    exists = any(h.get("type") == "command" and h.get("command") == command for entry in arr for h in entry.get("hooks", []))
    if not exists:
        arr.append({
            "hooks": [{
                "type": "command",
                "command": command,
                "timeout": timeout,
                "statusMessage": "Capturing Claude fusion event",
            }]
        })
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
fi

cat <<EOF
Installed Claude provider commands into:
  $bin_dir

Commands:
  claude
  claude-codex
  claude-glm
EOF

if [ "$install_fusion" != "0" ]; then
  cat <<EOF

Fusion command installed into:
  $claude_dir

Fusion SDK dependency installed into:
  $claude_dir/fusion-sdk

Fusion usage:
  /fusion <topic>

If the current Claude Code session does not see /fusion or the hooks, restart Claude Code or open /hooks once.
EOF
fi

cat <<EOF

If your current shell cached an older command path, run:
  hash -r
EOF
