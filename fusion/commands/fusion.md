---
description: Send the same prompt to multiple models in parallel and return a single synthesized answer. Use for questions that benefit from multiple perspectives and deeper thinking — design decisions, tradeoff comparisons, reviews, root cause analysis. Not suited for factual lookups or simple implementation tasks.
argument-hint: <prompt>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic:

```bash
~/.claude/hooks/fusion-run.py "$ARGUMENTS"
```

After the command finishes, read the generated `JUDGE_PROMPT=...` file path from the output if needed, then read all fork answers, think deeply, and give the single best answer in Japanese. Output only the answer — no evaluation or analysis of the forks.
