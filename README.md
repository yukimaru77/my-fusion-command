# claude-code-provider-commands

Claude Code をコマンド名で使い分けるための小さな wrapper 集です。

## できること

このリポジトリは、次の3コマンドを作成します。

```bash
claude        # 本家 Claude Code
claude-codex  # CC Switch の Codex OAuth provider 経由
claude-glm    # CC Switch の GLM provider 経由
```

主な挙動:

- `claude` は本家 Claude Code のまま使う
- `claude-codex` は CC Switch の local proxy と `codex-oauth` provider を使う
- `claude-glm` は CC Switch DB の `zai-glm` provider 設定を読む
- 3コマンドすべてに `--dangerously-skip-permissions` を自動で付ける
- `claude` は `~/.local/share/claude/versions` の最新実行ファイルを毎回拾う
- OAuth token や API token はこのリポジトリに保存しない
- `~/.claude/settings.json` の恒久的な provider 切り替えを避け、基本的にプロセス単位の環境変数で切り替える

## 必要なもの

- macOS または Linux
- Bash
- Claude Code
- CC Switch
- `jq`
- `sqlite3`
- `lsof`
- `~/.local/bin` が `PATH` に入っていること
- `~/.cc-switch/cc-switch.db` が存在すること
- CC Switch に次の Claude app provider があること
  - `default`: 本家 Claude 用
  - `codex-oauth`: Codex OAuth 用
  - `zai-glm`: GLM 用

provider ID が違う場合は、`bin/ccswitch-claude-run.template` 内の `codex-oauth` / `zai-glm` を変更してください。

## インストール

```bash
./install.sh
hash -r
```

インストール先を変える場合:

```bash
CCPC_BIN_DIR="$HOME/.local/bin" ./install.sh
```

CC Switch の実行ファイルを明示する場合:

```bash
CCSWITCH_BIN="/path/to/cc-switch" ./install.sh
```

## 使い方

```bash
claude
claude-codex
claude-glm
```

一回だけ実行する例:

```bash
claude-codex -p 'OKだけ返して'
claude-glm -p 'OKだけ返して'
```

## 確認

コマンド解決順:

```bash
type -a claude claude-codex claude-glm
```

バージョン:

```bash
claude --version
claude-codex --version
claude-glm --version
```

`--dangerously-skip-permissions` が付くことを、Claude Code 本体を起動せずに確認:

```bash
CLAUDE_REAL_BIN=/bin/echo claude hello
CCSWITCH_CLAUDE_BIN=/bin/echo claude-codex hello
CCSWITCH_CLAUDE_BIN=/bin/echo claude-glm hello
```

期待例:

```text
--dangerously-skip-permissions hello
--dangerously-skip-permissions --model opus hello
--dangerously-skip-permissions --model glm-5.2 hello
```

CC Switch 側の current provider が本家 Claude に戻っているか確認:

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT id FROM providers WHERE app_type='claude' AND is_current=1;"
```

期待値:

```text
default
```

## AI 向け手順

AI agent に作業させる場合は [docs/AI_INSTRUCTIONS.md](docs/AI_INSTRUCTIONS.md) を渡してください。
