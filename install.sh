#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${CCPC_BIN_DIR:-$HOME/.local/bin}"

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

cat <<EOF
Installed Claude provider commands into:
  $bin_dir

Commands:
  claude
  claude-codex
  claude-glm

If your current shell cached an older command path, run:
  hash -r
EOF
