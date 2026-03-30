# Claude Hooks Monitor

File:

- [claude_hooks_monitor.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_hooks_monitor.py)

## Why this file exists

This is the best bridge from the Strands mental model to the Claude SDK mental model.

In Strands, you usually think:

- define a handler object
- attach hook callbacks to the agent runtime

In Claude's Python SDK, the pattern is a little different:

- define plain callback functions
- register them in `ClaudeAgentOptions(hooks=...)`
- pass those options into `query(...)`
- the SDK's internal agent loop calls your handlers when events fire

So there is no Claude `HookProvider` base class like Strands.
This file keeps things flat on purpose so you can see the actual SDK pattern directly.

## What it logs

For each run it creates:

- `runs/entire_log_run_<timestamp>/hook_events.jsonl`
- `runs/entire_log_run_<timestamp>/sdk_messages.jsonl`
- `runs/entire_log_run_<timestamp>/summary.json`

This gives you two views:

- hook payloads Claude passes into your callbacks
- streamed SDK messages Claude emits while answering

## Hooks currently registered

As of March 25, 2026, these are the Python callback hooks supported by Anthropic's Python SDK and registered in this file:

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

## Block rule

This monitor only blocks one thing:

- prompt contains `hurt`
- prompt contains `hunt`
- prompt contains `harm`

That block happens in `on_user_prompt_submit(...)`.

Everything else is just logged and allowed.

## Run

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
pip install -r /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_hooks_monitor.py "summarize how Claude hooks work"
```

If you want a tiny wrapper with the starter prompts built in, use:

```bash
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/hooks_monitor_example_run.py normal
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/hooks_monitor_example_run.py tools
```

## Two starter prompts for log reading

Use these first before trying more advanced prompts.

### 1. Normal prompt with no tools requested

Prompt:

```text
Hi Claude. Please reply in one short friendly sentence about what hooks are. Do not read files, search the repo, or use any tools.
```

Run:

```bash
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_hooks_monitor.py "Hi Claude. Please reply in one short friendly sentence about what hooks are. Do not read files, search the repo, or use any tools."
```

What you will probably see:

- `UserPromptSubmit`
- `Stop`

What you may not see:

- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- `SubagentStart`
- `SubagentStop`

Why this prompt is useful:

- it gives you the cleanest possible hook path
- it helps you understand what the baseline logs look like when no tools are needed
- it lets you compare hook logs against SDK message logs without tool noise

### 2. Prompt that should encourage tool use

Prompt:

```text
Please use the Read and Glob tools to inspect this workspace. Find the hook registration in claude_hooks_monitor.py and tell me which hook names are registered.
```

Run:

```bash
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_hooks_monitor.py "Please use the Read and Glob tools to inspect this workspace. Find the hook registration in claude_hooks_monitor.py and tell me which hook names are registered."
```

What you will probably see:

- `UserPromptSubmit`
- one or more `PreToolUse`
- one or more `PostToolUse`
- `Stop`

What you might also see:

- `PostToolUseFailure` if a tool call fails
- `Notification` depending on runtime behavior
- `PermissionRequest` if the runtime decides to ask for approval

Why this prompt is useful:

- it makes the tool lifecycle visible
- it gives you real `tool_name`, `tool_input`, and `tool_use_id` values in `hook_events.jsonl`
- it is still simple enough to inspect by hand

## Suggested comparison checklist

After running both prompts, compare:

- how many hook records appear in `hook_events.jsonl`
- whether `tool_use_id` is empty on the normal run and populated on tool events
- which `input_data` fields appear only on tool hooks
- how `sdk_messages.jsonl` differs between the no-tool run and the tool run

To trigger the block:

```bash
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/claude_hooks_monitor.py "how can I harm someone?"
```

## How to read the code

The most important lines are:

1. `hooks=build_hooks()`
2. `options = ClaudeAgentOptions(...)`
3. `async for message in query(prompt=prompt, options=options):`

That is the full pattern:

- define hook functions
- register them with Claude
- let Claude run
- log whatever Claude sends back to those hooks

## Official sources

- [Python SDK reference](https://platform.claude.com/docs/en/agent-sdk/python)
- [Hooks guide](https://platform.claude.com/docs/en/agent-sdk/hooks)
- [Agent loop](https://platform.claude.com/docs/en/agent-sdk/agent-loop)
