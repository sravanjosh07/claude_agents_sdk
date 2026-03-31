# Claude Aiceberg

Drop-in observability and control for Claude agents — whether you build with
the **Claude Agent SDK** (Python) or the **Claude Code CLI**.

Add Aiceberg to your agent and get:

- **Full visibility** — every user prompt, tool call, and subagent delegation
  appears as open/close event pairs on the Aiceberg dashboard.
- **Policy control** — Aiceberg can **block** a prompt or tool call before
  Claude acts on it. Blocked events are closed cleanly, no orphans.
- **Zero config** — one import for the SDK path, one CLI command for Claude Code.

---

## Quick Start

### Install

```bash
pip install -e .          # from this repo
# or
pip install git+https://github.com/sravanjosh07/claude_agents_sdk.git
```

### Environment

Create a `.env` file in your project root:

```env
AICEBERG_API_KEY=your-api-key
USE_CASE_ID=your-use-case-id
AICEBERG_API_URL=https://api.test1.aiceberg.ai/eap/v1/event   # optional, this is the default
AICEBERG_DRY_RUN=false          # set true to skip real API calls
AICEBERG_USER_ID=claudeagent    # optional, tags events with a user id
AICEBERG_BLOCK_MESSAGE=...      # optional, custom block message
AICEBERG_HOOK_DEBUG=1           # optional, writes debug JSONL log
```

### Run the example

```bash
python examples/run_hooks.py                # simple prompt
python examples/run_hooks.py --subagents    # with named subagents
```

### Run tests

```bash
pytest tests/ -v
```

---

## SDK Integration (Python)

The smallest integration — three lines:

```python
from claude_agent_sdk import query
from claude_aiceberg import ClaudeAicebergHooks

hooks = ClaudeAicebergHooks()
options = hooks.agent_options(prompt="What is the weather in Houston?")

async for msg in query(options=options):
    ...
```

`agent_options()` returns a `ClaudeAgentOptions` with:

- The full Aiceberg hook registry wired in
- Default model (`haiku`)
- Default allowed tools (`Read`, `Glob`, `Grep`, `Write`, `Edit`, `Bash`, `Agent`)

### Hook Events Handled (SDK path)

| Claude Hook | Aiceberg Event | Blockable? | Async? |
|---|---|---|---|
| `UserPromptSubmit` | `user_agt` — opens a user event | ✅ | — |
| `PreToolUse` | `agt_tool` / `agt_agt` — opens a tool event | ✅ | — |
| `PostToolUse` | closes the tool event | — | ✅ |
| `PostToolUseFailure` | closes the tool event with error | — | ✅ |
| `Stop` | closes the user event with assistant response | — | — |
| `StopFailure` | closes the user event with error info | — | ✅ |
| `SubagentStart` | registers subagent locally | — | ✅ |
| `SubagentStop` | records subagent stop time + transcript | — | ✅ |

- **`agt_tool`** for normal tools (Read, Bash, WebSearch, …)
- **`agt_agt`** for Agent/Task tools (subagent delegation)

### Subagent Support

Pass an `agents` dict to enable subagent delegation:

```python
from claude_agent_sdk import AgentDefinition
from claude_aiceberg import ClaudeAicebergHooks

agents = {
    "file-counter": AgentDefinition(
        name="file-counter",
        description="Counts files matching a pattern.",
        instructions="Use Glob + Bash to count files.",
        allowed_tools=["Glob", "Bash"],
    ),
    "summarizer": AgentDefinition(
        name="summarizer",
        description="Summarises project structure.",
        instructions="Read key files and give a concise summary.",
        allowed_tools=["Read", "Glob", "Grep"],
    ),
}

hooks = ClaudeAicebergHooks(agents=agents)
options = hooks.agent_options(prompt="Count files and summarise the project.")
```

After the run, inspect what happened:

```python
for record in hooks.list_subagents():
    print(record.agent_id, record.agent_type, record.stopped_at)
```

### Lifecycle Helpers

```python
# After the query loop finishes successfully:
await hooks.complete_user_turn(session_id, final_text)

# If the run fails with an exception:
await hooks.fail_session(session_id, str(exc))

# Debug: check for leaked events
hooks.report_unresolved_events()
hooks.report_subagents()
```

---

## Claude Code CLI Integration

For Claude Code (the CLI), this package provides two console scripts:

| Command | Purpose |
|---|---|
| `claude-aiceberg-init` | Writes `.claude/settings.local.json` with all hook entries |
| `claude-aiceberg-hook` | The hook handler that Claude Code invokes per event |

### Setup

```bash
cd /path/to/your-project
claude-aiceberg-init --workspace . --allow-web-search --debug
```

This generates `.claude/settings.local.json` with hooks for all 8 managed
events, correct `async: true` flags, and `matcher: "*"` on tool hooks.

### How It Works

1. Claude Code fires a hook event (e.g. `PreToolUse`)
2. It spawns `claude-aiceberg-hook PreToolUse --workspace /your/project`
3. The hook reads JSON from stdin, sends it to Aiceberg, and returns a
   decision on stdout (`{"decision": "block", ...}` or nothing)
4. Cross-process state is persisted in SQLite at `.claude/aiceberg_state.sqlite3`

The CLI hook handles the same 8 events as the SDK path with identical
semantics — blocking, event pairing, subagent tracking, and error cleanup.

---

## Project Structure

```
src/
  claude_aiceberg/          # Main package
    __init__.py             # Public facade (ClaudeAicebergHooks)
    workflow.py             # Stateful event lifecycle (SDK path)
    hooks.py                # SDK hook wiring via dispatch table
    sender.py               # HTTP transport to Aiceberg API
    config.py               # Settings, paths, hook config builder
    cli.py                  # claude-aiceberg-init entrypoint
  claude_code_aiceberg_hook.py  # Claude Code CLI hook handler
examples/
  run_hooks.py              # Example runner (simple + subagent modes)
tests/
  test_sdk_core.py          # Sender, workflow, hooks, subagent tests
  test_claude_code_hook.py  # CLI hook state store + dispatch tests
  test_packaging_cli.py     # Package metadata + init command tests
docs/
  claude_aiceberg_hooks.md  # Design notes
```

---

## Architecture

```
┌──────────────────────┐     ┌──────────────────────┐
│  Claude Agent SDK    │     │  Claude Code CLI      │
│  (Python process)    │     │  (spawns hook process)│
└─────────┬────────────┘     └─────────┬────────────┘
          │                            │
          ▼                            ▼
   ClaudeAicebergHooks          claude-aiceberg-hook
   (workflow.py + hooks.py)     (claude_code_aiceberg_hook.py)
          │                            │
          │   in-memory state          │   SQLite state
          │                            │
          └──────────┬─────────────────┘
                     ▼
              AicebergSender
              (sender.py)
                     │
                     ▼
           Aiceberg REST API
           (open/close event pairs)
```

Both paths produce identical Aiceberg events — `user_agt`, `agt_tool`, `agt_agt` —
with the same blocking, close, and error-handling semantics.

---

## License

Private / internal.
