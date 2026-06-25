---
description: Send the same prompt to multiple models in parallel and return a single synthesized answer. Use for questions that benefit from multiple perspectives and deeper thinking — design decisions, tradeoff comparisons, reviews, root cause analysis. Not suited for factual lookups or simple implementation tasks.
argument-hint: <prompt>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic using exactly one Bash tool call.
Do not set or ask the user to set any timeout. Do not use Agent, Task, background agents, WebSearch, or any fallback fusion path.
If the Bash call fails, report that failure and do not synthesize an answer from any other mechanism.

```bash
~/.claude/hooks/fusion-run.py --supervise "$ARGUMENTS"
```

After the command returns, tell the user the `FUSION_RUN_ID` and `STATUS_COMMAND`. The fusion worker runs in the background. When the user asks for progress or when you need the result, run the status command; once the status is complete, read the generated `JUDGE_PROMPT=...` file path, read all fork answers, think deeply, and give the single best answer in Japanese. Output only the answer — no evaluation or analysis of the forks.
