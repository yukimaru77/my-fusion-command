---
description: Run parallel forked Claude sessions, collect their outputs, and judge them in the main session
argument-hint: <topic>
allowed-tools: Bash, Read
---

Run the fusion harness for this topic:

```bash
~/.claude/hooks/fusion-run.py "$ARGUMENTS"
```

After the command finishes, read the generated `JUDGE_PROMPT=...` file path from the output if needed, then act as the main judge: critically review each forked answer with respect, incorporate the strongest parts, add your own independent reasoning, and give the final conclusion in Japanese.
