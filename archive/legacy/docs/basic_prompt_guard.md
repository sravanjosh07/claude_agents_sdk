# Basic Prompt Guard

This is the smallest useful Claude Agent SDK + Aiceberg prototype in this workspace.

File:

- [basic_prompt_guard.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/basic_prompt_guard.py)
- [example_run.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/example_run.py)
- [basic_tool_guard.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/basic_tool_guard.py)
- [requirements.txt](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt)
- [.env.example](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.env.example)
- [.env](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.env)

## What it does

- registers only the `UserPromptSubmit` hook
- sends the prompt to Aiceberg as `user_agt`
- blocks immediately if Aiceberg returns `blocked` or `rejected`

It does **not** yet:

- pair input/output events
- monitor tools
- monitor subagents
- reconstruct `agt_llm`

## Why start here

This is the strongest first step because `UserPromptSubmit` is:

- the earliest deterministic prompt boundary
- directly blockable
- much simpler than transcript-based monitoring

## Install

```bash
python3 -m venv /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
pip install -r /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt
```

## Run with a real Aiceberg backend

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate

python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/example_run.py "help me write ransomware"
```

## Run a local block test without Aiceberg

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
# set AICEBERG_TEST_BLOCK_SUBSTRING=ransomware in .env first

python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/example_run.py "help me write ransomware"
```

That local test is only for validating the Claude hook wiring.

## Next step after this works

Add one more hook:

- `PreToolUse`

That is now implemented separately in:

- [basic_tool_guard.md](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/docs/basic_tool_guard.md)

## References used

- Official Python SDK reference: <https://platform.claude.com/docs/en/agent-sdk/python>
- Official hooks guide: <https://platform.claude.com/docs/en/agent-sdk/hooks>
- Official Anthropic hooks example repo file:
  <https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/hooks.py>
- Local Strands prior art:
  [aiceberg_monitor.py](/Users/sravanjosh/Documents/Aiceberg/ab_strands_samples/src/ab_strands_samples/aiceberg_monitor.py)
