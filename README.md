# Claude Aiceberg Hooks

This workspace is organized as:

- [src/](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src) for runnable Python code
- [docs/](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/docs) for notes and design docs
- [tests/](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/tests) for unit tests
- [archive/legacy/](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/archive/legacy) for older demos and exploratory notes

## Quick Start

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
pip install -r /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py
python3 -m unittest discover -s /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/tests
```

Change the `PROMPT` constant in
[run_claude_aiceberg_hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py)
when you want to try a different example.

## Smallest Integration

Most people should only need this shape:

```python
from claude_aiceberg import ClaudeAicebergHooks

aiceberg = ClaudeAicebergHooks()
options = aiceberg.agent_options()
```

That `options` object already includes:

- the full Claude hook registry
- the default Claude model alias
- the default tool allowlist
- the default permission mode

## Claude Code

For project-local Claude Code CLI integration, this repo now includes:

- [settings.local.json](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.claude/settings.local.json)
- [claude_code_aiceberg_hook.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_code_aiceberg_hook.py)

That path uses Claude Code command hooks plus a tiny SQLite state file under `.claude/`
so prompt and tool events can still be opened and closed across separate hook processes.

## Main Files

- [sender.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/sender.py)
- [workflow.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/workflow.py)
- [hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/hooks.py)
- [run_claude_aiceberg_hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py)
- [claude_aiceberg_hooks.md](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/docs/claude_aiceberg_hooks.md)
