# AI Instructions

このリポジトリの目的は、Claude Code を3つのコマンドで使い分けられるようにすることです。

```bash
claude        # official Claude Code
claude-codex  # Codex OAuth through CC Switch
claude-glm    # GLM through CC Switch provider settings
```

## 守ること

- OAuth token や API token をリポジトリに書かない。
- `~/.cc-switch/cc-switch.db` から token を読む場合も、出力に token を表示しない。
- 既存の `~/.local/bin/claude` は、必要なら `claude-real` または timestamp backup に退避する。
- ユーザーの unrelated git changes は戻さない。
- `~/.claude/settings.json` を恒久的に Codex/GLM へ書き換えない。

## 必要な前提

1. Claude Code がインストール済み。
2. CC Switch がセットアップ済み。
3. `jq`, `sqlite3`, `lsof` が利用可能。
4. `~/.cc-switch/cc-switch.db` が存在する。
5. CC Switch DB に以下の provider が存在する。
   - `default`
   - `codex-oauth`
   - `zai-glm`

provider ID が違う場合は `bin/ccswitch-claude-run.template` の provider ID を変更する。

## インストール手順

リポジトリルートで実行:

```bash
./install.sh
hash -r
```

CC Switch binary が自動検出できない場合:

```bash
CCSWITCH_BIN="/path/to/cc-switch" ./install.sh
```

インストール先を変える場合:

```bash
CCPC_BIN_DIR="$HOME/.local/bin" ./install.sh
```

## 検証手順

構文チェック:

```bash
bash -n bin/claude bin/claude-codex bin/claude-glm bin/ccswitch-claude-run.template install.sh
```

コマンド確認:

```bash
type -a claude claude-codex claude-glm
claude --version
claude-codex --version
claude-glm --version
```

permission skip flag 確認:

```bash
CLAUDE_REAL_BIN=/bin/echo claude hello
CCSWITCH_CLAUDE_BIN=/bin/echo claude-codex hello
CCSWITCH_CLAUDE_BIN=/bin/echo claude-glm hello
```

期待値:

```text
--dangerously-skip-permissions hello
--dangerously-skip-permissions --model opus hello
--dangerously-skip-permissions --model glm-5.2 hello
```

CC Switch DB 後始末確認:

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT id FROM providers WHERE app_type='claude' AND is_current=1;"
```

期待値:

```text
default
```

## 実装の要点

- `bin/claude` は official Claude Code の wrapper。
- `bin/claude` は `~/.local/share/claude/versions` の最新 executable を優先する。
- `bin/claude-codex` と `bin/claude-glm` は同じディレクトリの `ccswitch-claude-run` を呼ぶ。
- `ccswitch-claude-run` は install 時に `bin/ccswitch-claude-run.template` から生成される。
- Codex は `ANTHROPIC_BASE_URL=http://127.0.0.1:15721` と `ANTHROPIC_API_KEY=PROXY_MANAGED` を使う。
- GLM は CC Switch DB の `zai-glm` provider から `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` を読む。
