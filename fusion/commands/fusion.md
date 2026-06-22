---
description: 複数モデルに同じプロンプトを並列で投げ、統合された一つの回答を返す。設計判断・トレードオフ比較・レビュー・原因切り分けなど、複数の視点があると質が上がる問いに使う。事実確認や単純な実装作業には不向き。
argument-hint: <prompt>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic:

```bash
~/.claude/hooks/fusion-run.py "$ARGUMENTS"
```

After the command finishes, read the generated `JUDGE_PROMPT=...` file path from the output if needed, then read all fork answers, think deeply, and give the single best answer in Japanese. Output only the answer — no evaluation or analysis of the forks.
