# AI Instructions

You are setting up a small command-wrapper repo. Read this file first and follow it exactly.

このリポジトリの目的は、Claude Code を3つのコマンドで使い分けられるようにすることです。

```bash
claude        # official Claude Code
claude-codex  # Codex OAuth through CC Switch
claude-glm    # GLM through CC Switch provider settings
```

加えて、Claude Code の `/fusion` slash command を入れます。`/fusion <お題>` は3 provider を Claude Agent SDK で fork し、headless print mode で並列起動し、stream-json から final answer を収集し、main セッションで批判的に統合するための judge prompt を生成します。

## 守ること

- OAuth token や API token をリポジトリに書かない。
- `~/.cc-switch/cc-switch.db` から token を読む場合も、出力に token を表示しない。
- 既存の `~/.local/bin/claude` は、必要なら `claude-real` または timestamp backup に退避する。
- ユーザーの unrelated git changes は戻さない。
- `~/.claude/settings.json` を恒久的に Codex/GLM へ書き換えない。
- `/fusion` 用 hooks は既存 hooks に追記する。既存の hooks/permissions/env を置き換えない。
- `/fusion` は直接 slash command から呼ばれた場合、子 fork に `/fusion` command row を残さない。JSONL 内の `/fusion` command row を探し、その `parentUuid` 側の checkpoint まで Claude Agent SDK の `forkSession(..., { upToMessageId })` で切り詰める。
- skill / 親エージェント経由の `/fusion` は rollback しない。代わりに子 fork へ「すでに fusion エージェントの一員なので再fusionせず自分で回答する」system prompt を追加する。
- 子 fork では一時 `CLAUDE_CONFIG_DIR` を使い、`/fusion` command と fusion 系 skill だけを除外する。全skillsを落とす `--disable-slash-commands` は使わない。
- `/fusion` の transcript capture は opt-in で、`CLAUDE_TRANSCRIPT_CAPTURE=1` または `~/.claude/session-captures/enabled` がある時だけ保存する。
- 確認コマンドで secret を表示しない。`settings_config` 全体をそのまま出力しない。

## 最初に確認すること

作業前に、次だけ確認する。

```bash
pwd
command -v claude
command -v jq
command -v sqlite3
command -v lsof
test -f ~/.cc-switch/cc-switch.db
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT id, name FROM providers WHERE app_type='claude' AND id IN ('default','codex-oauth','zai-glm');"
```

期待:

- `claude`, `jq`, `sqlite3`, `lsof` が見つかる。
- `~/.cc-switch/cc-switch.db` が存在する。
- provider が3件出る。

provider が不足している場合:

- `default` がない: CC Switch の Claude provider 初期設定を作る。
- `codex-oauth` がない: CC Switch で Codex OAuth provider を作る。
- `zai-glm` がない: CC Switch で GLM provider を作る。

provider ID が環境で違うだけなら、`bin/ccswitch-claude-run.template` の provider ID を変更する。

## 必要な前提

1. Claude Code がインストール済み。
2. CC Switch がセットアップ済み。
3. `jq`, `sqlite3`, `lsof` が利用可能。
4. `/fusion` を使うなら `python3`, Node.js/npm が利用可能。
5. `~/.cc-switch/cc-switch.db` が存在する。
6. CC Switch DB に以下の provider が存在する。
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

インストール後、現在の shell が古い command path を cache している場合があるので、必ず実行:

```bash
hash -r
```

## 検証手順

構文チェック:

```bash
bash -n bin/claude bin/claude-codex bin/claude-glm bin/ccswitch-claude-run.template install.sh
python3 -m py_compile fusion/hooks/collect-transcript.py fusion/hooks/capture-query.py fusion/hooks/fusion-run.py
npm install --prefix fusion/fusion-sdk --omit=dev
node fusion/hooks/fusion-sdk-fork.mjs || test $? -eq 2
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

proxy 状態確認:

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT enabled, proxy_enabled, live_takeover_active, listen_address, listen_port FROM proxy_config WHERE app_type='claude';
   SELECT COUNT(*) FROM proxy_live_backup WHERE app_type='claude';"
```

期待:

```text
0|1|0|127.0.0.1|15721
0
```

## よくある詰まり

`claude-codex` が proxy で失敗する:

- CC Switch が起動しているか確認する。
- `lsof -nP -iTCP:15721 -sTCP:LISTEN` で local proxy を確認する。
- `CCSWITCH_BIN=/path/to/cc-switch ./install.sh` で binary path を明示する。

`claude` が古い本体を拾う:

- `type -a claude` で `~/.local/bin/claude` が先にあるか確認する。
- `hash -r` を実行する。
- `~/.local/share/claude/versions` に最新版があるか確認する。

`claude-glm` が token 不足で失敗する:

- token は表示しない。
- CC Switch UI で `zai-glm` provider の Base URL, token, model を設定する。

## 実装の要点

- `bin/claude` は official Claude Code の wrapper。
- `bin/claude` は `~/.local/share/claude/versions` の最新 executable を優先する。
- `bin/claude-codex` と `bin/claude-glm` は同じディレクトリの `ccswitch-claude-run` を呼ぶ。
- `ccswitch-claude-run` は install 時に `bin/ccswitch-claude-run.template` から生成される。
- Codex は `ANTHROPIC_BASE_URL=http://127.0.0.1:15721` と `ANTHROPIC_API_KEY=PROXY_MANAGED` を使う。
- GLM は CC Switch DB の `zai-glm` provider から `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` を読む。
- `/fusion` は `fusion/commands/fusion.md`, `fusion/hooks/*.py`, `fusion/hooks/*.mjs`, `fusion/fusion-sdk/package.json` を `~/.claude` へインストールする。
- install 時に `~/.claude/fusion-sdk` へ `@anthropic-ai/claude-agent-sdk` を npm install する。
- `/fusion` は `fusion-sdk-fork.mjs` 経由で `forkSession` を呼び、必要なら `upToMessageId` を渡して元セッションを切り詰めた新セッションを作る。
- `/fusion` は `claude` / `claude-codex` / `claude-glm` を `-p --output-format stream-json --verbose` で headless 並列起動する。TUI 側の `--fork-session` / `--resume-session-at` には依存しない。
- `/fusion` の収集 timeout は `fusion-run.py` 側のデフォルトで120分。slash command から `--timeout` は渡さない。`fusion/commands/fusion.md` 側では Bash tool timeout だけを120分以上に指定し、`fusion-run.py "$ARGUMENTS"` を呼ぶ。
- run id と child session id は毎回新規生成する。base session がない場合も `--session-id` を明示し、前回の `/fusion` child session を次回に再利用しない。
- headless の official `claude` が `Not logged in` / `authentication_failed` で失敗した場合だけ、他 agent 完了後に CC Switch proxy env (`ANTHROPIC_BASE_URL=http://127.0.0.1:15721`, `ANTHROPIC_API_KEY=PROXY_MANAGED`) で `claude` を再試行する。proxy を使う codex との current provider 競合を避けるため、fallback は初回並列実行後に単独で実行する。
- 子 fork は fusion-filtered `CLAUDE_CONFIG_DIR` と `--append-system-prompt` 付きで起動する。direct `/fusion` では `/fusion` 自体が履歴に残らない状態で prompt だけを送る。
- fork の対応付けは prompt 内タグではなく `--name fusion-<agent>-<run_id>` と child session id で行う。
- `~/.claude/projects/**/*.jsonl` は rollback 検証に使う。回答収集は headless 実行の stream-json result を優先する。
- `/fusion` の状態確認は `~/.claude/hooks/fusion-run.py --status` で行う。実行開始直後から `manifest.json` を作り、prompt、fork session id、rollback有無、各agentの running/complete/failed、summary/stdout/stderr/judge prompt パスを確認できるようにする。
