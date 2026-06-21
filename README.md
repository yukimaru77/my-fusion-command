# claude-code-provider-commands

Claude Code をコマンド名で使い分けるための小さな wrapper 集です。

## 渡すもの

人に渡す場合は、このリポジトリを丸ごと渡してください。

```text
claude-code-provider-commands/
```

GitHub 経由で渡す場合は、相手に clone してもらってください。

```bash
git clone <repo-url>
cd claude-code-provider-commands
```

AI agent に作業させる場合は、最初にこのファイルを読ませてください。

```text
docs/AI_INSTRUCTIONS.md
```

AI に渡す最短依頼文:

```text
この repo の docs/AI_INSTRUCTIONS.md に従ってセットアップしてください。
OAuth token / API token は表示しないでください。
作業後に README の確認コマンドを実行してください。
```

token は渡しません。Codex OAuth と GLM API token は、作業する人の CC Switch に登録されている前提です。

## できること

このリポジトリは、次の3コマンドを作成します。

```bash
claude        # 本家 Claude Code
claude-codex  # CC Switch の Codex OAuth provider 経由
claude-glm    # CC Switch の GLM provider 経由
```

加えて、Claude Code の slash command として `/fusion` もインストールできます（デフォルト有効）。

```text
/fusion <お題>
```

`/fusion` は `claude` / `claude-codex` / `claude-glm` を tmux 上で並列 fork し、各回答と tool input/output を hook で収集して、main セッションで批判的に統合するための judge prompt を生成します。

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
- `tmux`（`/fusion` を使う場合）
- `python3`（`/fusion` hooks を使う場合）
- `~/.local/bin` が `PATH` に入っていること
- `~/.cc-switch/cc-switch.db` が存在すること
- CC Switch に次の Claude app provider があること
  - `default`: 本家 Claude 用
  - `codex-oauth`: Codex OAuth 用
  - `zai-glm`: GLM 用

provider ID が違う場合は、`bin/ccswitch-claude-run.template` 内の `codex-oauth` / `zai-glm` を変更してください。

作業前チェック:

```bash
command -v claude
command -v jq
command -v sqlite3
command -v lsof
test -f ~/.cc-switch/cc-switch.db
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT id, name FROM providers WHERE app_type='claude' AND id IN ('default','codex-oauth','zai-glm');"
```

3 provider が出ない場合は、先に CC Switch で provider を登録してください。

## インストール

```bash
./install.sh
hash -r
```

インストール先を変える場合:

```bash
CCPC_BIN_DIR="$HOME/.local/bin" ./install.sh
```

`/fusion` を入れない場合:

```bash
CCPC_INSTALL_FUSION=0 ./install.sh
```

Claude Code 設定ディレクトリを変える場合:

```bash
CCPC_CLAUDE_DIR="$HOME/.claude" ./install.sh
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

Claude Code 内で `/fusion` を使う例:

```text
/fusion AIエージェント評価では成果物と過程ログのどちらを重視すべきか
```

`/fusion` の流れ:

1. `claude` / `claude-codex` / `claude-glm` を tmux 上で `--continue --fork-session` 起動
2. それぞれに同じお題を投入
3. hook で `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` を収集
4. `~/.claude/session-captures/fusion-run-<run_id>/judge-prompt.md` を生成
5. main セッションが各回答を批判的に統合して最終回答

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

`/fusion` 関連ファイル:

```bash
test -x ~/.claude/hooks/fusion-run.py
test -x ~/.claude/hooks/collect-transcript.py
test -x ~/.claude/hooks/capture-query.py
test -f ~/.claude/commands/fusion.md
python3 -m py_compile ~/.claude/hooks/fusion-run.py ~/.claude/hooks/collect-transcript.py ~/.claude/hooks/capture-query.py
```

収集結果の確認:

```bash
~/.claude/hooks/capture-query.py --tag fusion-
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

AI に渡すときの短い依頼文:

```text
この repo の docs/AI_INSTRUCTIONS.md に従って、claude / claude-codex / claude-glm コマンドをセットアップしてください。
token は表示しないでください。作業後に README の確認コマンドを実行してください。
```
