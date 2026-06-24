---
description: Send the same prompt to multiple models in parallel and return a single synthesized answer. Use for questions that benefit from multiple perspectives and deeper thinking — design decisions, tradeoff comparisons, reviews, root cause analysis. Not suited for factual lookups or simple implementation tasks.
argument-hint: <prompt>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic using exactly one Bash tool call.
Set the Bash tool timeout to at least 7200000 ms. Do not use Agent, Task, background agents, WebSearch, or any fallback fusion path.
If the Bash call times out or fails, report that failure and do not synthesize an answer from any other mechanism.

```bash
~/.claude/hooks/fusion-run.py --timeout 7200 --keep-session "$ARGUMENTS"
```

After the command finishes, read the generated `JUDGE_PROMPT=...` file path from the output if needed, then read all fork answers, think deeply, and give the single best answer in Japanese. Output only the answer — no evaluation or analysis of the forks.
