# my-fusion-command

Claude Code の `/fusion` コマンドを追加するためのリポジトリです。

## `/fusion` とは

`/fusion` は、同じプロンプトを複数の異なる AI モデルに同時に投げ、それぞれの回答を集めたうえで一つの統合された回答を返す Claude Code のスラッシュコマンドです。

```
/fusion このアーキテクチャの問題点は？
```

内部では以下が起きます:

1. tmux 上で `claude`（本家）/ `claude-codex`（GPT系）/ `claude-glm`（GLM系）を並列起動
2. 各モデルが独立にプロンプトへ回答
3. hook が各セッションの回答と tool 使用履歴を収集
4. メインセッションが全回答を読み、統合された一つの最終回答を出力

ユーザーに見えるのは最終回答だけです。複数モデルの視点を経ることで、単一モデルの盲点や思い込みを補い、より質の高い回答が得られます。

## 前提条件

- macOS または Linux
- Claude Code がインストール済み
- [CC Switch](https://github.com/yukimaru77/ccswitch-codex-glm) がセットアップ済みで、以下の provider が登録されていること:
  - `default` — 本家 Claude
  - `codex-oauth` — Codex OAuth（GPT系モデル）
  - `zai-glm` — Z.AI GLM
- `tmux`, `python3`, `jq`, `sqlite3`, `lsof` がインストール済み
- `~/.local/bin` が `PATH` に入っていること

## インストール

```bash
git clone https://github.com/yukimaru77/my-fusion-command.git
cd my-fusion-command
./install.sh
hash -r
```

これで以下が設定されます:

- `claude`, `claude-codex`, `claude-glm` コマンドを `~/.local/bin` に配置
- `/fusion` コマンド（`~/.claude/commands/fusion.md`）を配置
- 回答収集用の hook スクリプトを `~/.claude/hooks/` に配置
- `~/.claude/settings.json` に hook を登録

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
