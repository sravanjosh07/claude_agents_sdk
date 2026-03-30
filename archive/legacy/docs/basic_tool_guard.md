# Basic Tool Guard

This is the second minimal prototype in this workspace.

File:

- [basic_tool_guard.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/basic_tool_guard.py)

## What it does

- registers only the `PreToolUse` hook
- sends the tool invocation to Aiceberg before execution
- denies the tool immediately if Aiceberg returns `blocked` or `rejected`

It classifies tool calls into:

- `agt_tool`
- `agt_agt` for `Agent` and legacy `Task`
- `agt_mem` for obvious memory-oriented MCP tools

## Why this is the next best step

`PreToolUse` is the second strongest blockable boundary after `UserPromptSubmit`.

This lets us validate:

- Claude hook wiring
- Aiceberg payload format for tool events
- allow/deny behavior before a tool actually runs

without building full turn-state logic yet.

## Run

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
pip install -r /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/basic_tool_guard.py "create a file named test.txt with hello"
```

## Local-only test helpers

Set either of these in [.env](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.env):

```bash
AICEBERG_TEST_BLOCK_TOOL=Write
AICEBERG_TEST_BLOCK_COMMAND_SUBSTRING=rm -rf
```

Those are just for validating the hook path before using a real Aiceberg backend.
