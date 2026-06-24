# my-fusion-command

Claude Code 用の `/fusion` スラッシュコマンドです。

## `/fusion` とは

同じプロンプトを複数の AI モデルに同時に投げ、それぞれの独立した回答を集めたうえで、一つの統合された回答を返します。

```
/fusion このアーキテクチャの問題点は？
```

内部の流れ:

1. 現在の Claude Code セッションを Claude Agent SDK で3つ fork（デフォルト: Claude / Codex / GLM）
2. 直接 `/fusion <prompt>` から起動された場合だけ、子 fork は `/fusion` の1ターン前へ rollback
3. 子 fork に slash command を無効化した状態で `<prompt>` だけを入力
4. 各モデルを headless print mode で並列実行し、stream-json から回答を収集
5. メインセッションが全回答を読み、統合された最終回答を出力

ユーザーに見えるのは最終回答だけです。複数モデルの視点を経ることで、単一モデルの盲点を補います。

**注意**: このハーネスは Claude Code 専用です。fork は `@anthropic-ai/claude-agent-sdk` の `forkSession` と Claude Code の JSONL 履歴に依存しており、他の AI クライアントでは動作しません。

## rollback の仕様

`/fusion` は Claude Code の slash command なので、普通に fork すると子セッションにも `/fusion` 呼び出しが入ります。このリポジトリの実装では、直接 `/fusion <prompt>` が叩かれた場合に限り、JSONL 内の `/fusion` command row を探して、その直前の checkpoint まで fork を切り詰めます。

そのため、子 fork の実ユーザー履歴は次のようになります。

```text
こんにちは
/fusion 1年で1億稼ぐアプリを作るには何を作ればいい？
```

ではなく:

```text
こんにちは
1年で1億稼ぐアプリを作るには何を作ればいい？
```

skill や親エージェント経由で `/fusion` が呼ばれた場合は rollback しません。その代わり、子 fork には「あなたはすでに fusion エージェントの一員なので、再fusionせず自分で直接回答する」system prompt を追加します。

子 fork では一時的な `CLAUDE_CONFIG_DIR` を使い、`/fusion` command と fusion 系 skill だけを除外します。他の skills / commands は残すため、fusion の再帰起動だけを止めつつ通常の調査能力は維持します。

各子 fork の Claude session 履歴は、回答を回収したあと Claude Agent SDK の `deleteSession()` で自動削除します。`~/.claude/projects/**/*.jsonl` や `~/.claude/tasks/<session-id>` に fusion の子セッションが増え続けないようにするためです。デバッグで履歴を残したい場合だけ、直接実行時に `--keep-child-sessions` を指定してください。

各 agent がエラーで未完了になった場合は、デフォルトで追加2回まで失敗した agent だけを順番にリトライします。`claude` のログイン失敗は CC Switch proxy 経由で再試行します。timeout は長時間停止の再発を避けるため自動リトライしません。直接実行時は `--retries N` で追加試行回数を変えられます。

過去runの子セッションを後から消す場合:

```bash
~/.claude/hooks/fusion-run.py --cleanup-sessions <run-id>
```

## エージェントのカスタマイズ

デフォルトでは `claude` / `claude-codex` / `claude-glm` の3つが使われます。

自分の環境に合わせてエージェントを変更するには、`~/.claude/fusion.json` を作成してください:

```json
[
  {"name": "claude", "command": "claude"},
  {"name": "codex", "command": "claude-codex"},
  {"name": "glm", "command": "claude-glm"}
]
```

- `name` — 結果ラベルと child session 名に使われる
- `command` — 実行されるコマンド名（Claude Code 互換の CLI であること）

例: Claude 2つ + GPT 1つにする場合:

```json
[
  {"name": "claude-a", "command": "claude"},
  {"name": "claude-b", "command": "claude"},
  {"name": "gpt", "command": "claude-codex"}
]
```

このファイルがなければデフォルトの3エージェントが使われます。

## 前提条件

- macOS または Linux
- Claude Code
- Node.js / npm
- `python3`, `jq`, `sqlite3`, `lsof`
- `~/.local/bin` が `PATH` に入っていること
- **[ccswitch-claude-codex-setup](https://github.com/yukimaru77/ccswitch-claude-codex-setup)** を先にセットアップしてください
  - `claude-codex` / `claude-glm` 等のマルチプロバイダーコマンドはこのリポジトリで構築します

## インストール

```bash
git clone https://github.com/yukimaru77/my-fusion-command.git
cd my-fusion-command
./install.sh
hash -r
```

## インストールオプション

```bash
# /fusion なしでコマンドだけ入れる
CCPC_INSTALL_FUSION=0 ./install.sh

# インストール先を変える
CCPC_BIN_DIR="$HOME/.local/bin" ./install.sh

# CC Switch の実行ファイルを明示する
CCSWITCH_BIN="/path/to/cc-switch" ./install.sh
```

## 使い方

Claude Code のセッション内で:

```
/fusion 設計についてどう思う？
```

実行中は `claude` / `codex` / `glm` が裏側で headless に並列実行されます。tmux や cmux の pane は作りません。各 run と child session id は毎回新しく生成されるため、前回の `/fusion` の続きから始まることはありません。headless の official `claude` が OAuth/keychain を拾えず認証失敗した場合は、他 agent の完了後に CC Switch proxy 経由で `claude` だけ再試行します。

`/fusion` の内部収集 timeout はデフォルト120分です。slash command から `--timeout` を指定する必要はありません。長いコードレビューや全ファイル精査でも途中回答を拾って終わらないよう、Claude Code の Bash tool timeout だけは120分に設定します。

## デバッグ/状態確認

最新の `/fusion` run は1コマンドで確認できます。

```bash
~/.claude/hooks/fusion-run.py --status
```

特定runを見る場合:

```bash
~/.claude/hooks/fusion-run.py --status 20260624-223318-cee5799d
```

表示される内容:

- run id / result directory / prompt
- direct `/fusion` rollback が実行されたか
- 子 fork で無効化された fusion command / skill
- 子 fork の session 履歴を削除したか
- child session id
- 各agentの running / complete / failed 状態、pid、duration
- summary / stdout / stderr / judge prompt のファイルパス

実行開始直後から `~/.claude/session-captures/fusion-run-<run_id>/manifest.json` が作られ、agent完了ごとに更新されます。

## 動作確認

```bash
# コマンドが入っているか
type claude claude-codex claude-glm

# /fusion 関連ファイル
test -x ~/.claude/hooks/fusion-run.py && echo ok
test -x ~/.claude/hooks/fusion-sdk-fork.mjs && echo ok
test -x ~/.claude/hooks/fusion-sdk-delete.mjs && echo ok
test -f ~/.claude/commands/fusion.md && echo ok
test -d ~/.claude/fusion-sdk/node_modules/@anthropic-ai/claude-agent-sdk && echo ok

# 構文チェック
python3 -m py_compile ~/.claude/hooks/fusion-run.py
node ~/.claude/hooks/fusion-sdk-fork.mjs || test $? -eq 2
node ~/.claude/hooks/fusion-sdk-delete.mjs || test $? -eq 2

# 最新runの状態確認
~/.claude/hooks/fusion-run.py --status
```
