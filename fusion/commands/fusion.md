---
description: Run parallel forked Claude sessions, collect their outputs, and judge them in the main session
argument-hint: <topic>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic:

```bash
~/.claude/hooks/fusion-run.py "$ARGUMENTS"
```

After the command finishes, read the generated `JUDGE_PROMPT=...` file path from the output if needed, then read all fork answers, think deeply, and give the single best answer in Japanese. Output only the answer — no evaluation or analysis of the forks.
