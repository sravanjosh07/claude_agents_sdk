# Claude + Aiceberg Hooks

This is the current implementation path in this repo.

Main files:

- [__init__.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/__init__.py)
- [sender.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/sender.py)
- [workflow.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/workflow.py)
- [hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/hooks.py)
- [claude_code_aiceberg_hook.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_code_aiceberg_hook.py)
- [settings.local.json](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.claude/settings.local.json)
- [run_claude_aiceberg_hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py)

Older exploration files were moved to:

- [archive/legacy/](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/archive/legacy)

## Why this version exists

The older monitor files are still useful for learning and historical context.

This package is the cleaner implementation meant to grow from here.

## Claude Code path

For Claude Code CLI, this repo uses a separate project-local adapter:

- Claude Code hook commands call [claude_code_aiceberg_hook.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_code_aiceberg_hook.py)
- hook config lives in [settings.local.json](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.claude/settings.local.json)
- open prompt/tool event ids are persisted in `.claude/aiceberg_state.sqlite3`

This is separate from the Python SDK integration because Claude Code hooks run as
separate processes and cannot share the in-memory workflow state.

The current Claude Code config intentionally uses only the CLI-supported subset
we need for v1:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `Stop`

## Current hook scope

This version registers all Claude Agent SDK hook events supported by the
installed Python SDK:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- `Stop`
- `SubagentStart`
- `SubagentStop`
- `PreCompact`
- `Notification`
- `PermissionRequest`

The first version keeps the behavior intentionally simple:

- prompt and tool hooks are the only paired input/output events
- only prompt and tool-use hooks send live Aiceberg traffic right now
- lifecycle hooks and permission requests are registered but skipped locally for now
- prompt and pre-tool hooks are the block-capable ones

## File split

If you only want to use this package, start with
[__init__.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_aiceberg/__init__.py).
That is the small public entrypoint.

The smallest integration shape is:

```python
from claude_aiceberg import ClaudeAicebergHooks

aiceberg = ClaudeAicebergHooks()
options = aiceberg.agent_options()
```

The other files are internal support layers that keep the public entrypoint
small and the responsibilities readable.

### 1. `__init__.py`

Owns:

- the small public facade
- default model selection
- default tool allowlist
- building ready-to-use `ClaudeAgentOptions`

### 2. `sender.py`

Owns:

- `.env` loading
- Aiceberg payload construction
- dry-run simulation
- live HTTP POST logic
- the wait gap between outbound events
- response parsing and HTTP error capture

Important choice:

- `AICEBERG_DRY_RUN=true` is the default for now

### 3. `workflow.py`

Owns:

- open event tracking
- prompt/tool close handling
- fallback close attempts on Claude failure
- local skipping for non-live hooks
- unresolved-event reporting

Important choice:

- prompt and tool hooks are paired
- non-live hooks are still registered, but they do not send Aiceberg events in v1

### 4. `hooks.py`

Owns:

- one callback method per Claude hook
- hook registration order
- `HookMatcher` wiring for all supported hook events
- block decision shaping for Claude hook JSON output

Important choice:

- callback code stays thin and delegates real decisions to the workflow

### 5. `run_claude_aiceberg_hooks.py`

Owns:

- one starter prompt
- building `ClaudeAgentOptions`
- printing Claude output

## Run

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py
```

To try a different example, edit the `PROMPT` constant in
[run_claude_aiceberg_hooks.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/run_claude_aiceberg_hooks.py).

## Auth and model behavior

This code does **not** hardcode a Claude API key.

By default:

- Claude auth comes from your existing Claude SDK / CLI environment
- the runner uses the Claude Code model alias `haiku`
- Aiceberg stays in dry-run mode unless you explicitly set `AICEBERG_DRY_RUN=false`

## Sources

- [Anthropic Python SDK reference](https://docs.claude.com/en/docs/agent-sdk/python)
- [Anthropic hooks guide](https://docs.claude.com/en/docs/agent-sdk/hooks)
- [Anthropic agent loop guide](https://docs.claude.com/en/docs/agent-sdk/agent-loop)
