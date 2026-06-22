# my-fusion-command

Claude Code 用の `/fusion` スラッシュコマンドです。

## `/fusion` とは

同じプロンプトを複数の AI モデルに同時に投げ、それぞれの独立した回答を集めたうえで、一つの統合された回答を返します。

```
/fusion このアーキテクチャの問題点は？
```

内部の流れ:

1. tmux 上で複数の Claude Code セッションを並列起動（デフォルト: Claude / Codex / GLM）
2. 各モデルが独立にプロンプトへ回答
3. hook が各セッションの回答と tool 使用履歴を収集
4. メインセッションが全回答を読み、統合された最終回答を出力

ユーザーに見えるのは最終回答だけです。複数モデルの視点を経ることで、単一モデルの盲点を補います。

**注意**: このハーネスは Claude Code 専用です。各エージェントは Claude Code の `--fork-session` と hook 機構に依存しており、他の AI クライアントでは動作しません。

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

- `name` — tmux のウィンドウ名・結果ラベルに使われる
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
- `tmux`, `python3`, `jq`, `sqlite3`, `lsof`
- `~/.local/bin` が `PATH` に入っていること
- エージェントのコマンド（`claude-codex` 等）が使える状態であること
  - CC Switch でのプロバイダー切り替えについては `bin/` 内のスクリプトを参照

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

## 動作確認

```bash
# コマンドが入っているか
type claude claude-codex claude-glm

# /fusion 関連ファイル
test -x ~/.claude/hooks/fusion-run.py && echo ok
test -f ~/.claude/commands/fusion.md && echo ok
```
