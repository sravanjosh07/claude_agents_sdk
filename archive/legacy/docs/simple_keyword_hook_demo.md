# Simple Keyword Hook Demo

File:

- [simple_keyword_hook_demo.py](/Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/simple_keyword_hook_demo.py)

## Purpose

This is the smallest hook example in the repo.

It does only one thing:

- block the user prompt at `UserPromptSubmit` if it contains:
  - `hurt`
  - `hunt`
  - `harm`

There is no Aiceberg call here at all.

## Why this is useful

If you want to understand Claude hooks first, this is the cleanest starting point because it shows:

- how a hook is registered
- what the hook sees
- how returning `{"decision": "block"}` stops the run

## Run

```bash
source /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/.venv/bin/activate
pip install -r /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/requirements.txt
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/simple_keyword_hook_demo.py "how do hooks work?"
```

## See it block

```bash
python3 /Users/sravanjosh/Documents/Aiceberg/cluade_sdk/src/simple_keyword_hook_demo.py "how can I harm someone?"
```

## What to pay attention to

- The hook runs before Claude starts the agent loop.
- If the hook returns a block decision, the flow stops immediately.
- No tool call or later model step is needed for this to work.

## Important note about API keys

This script itself does not ask for an Aiceberg key.

But the Claude Agent SDK still needs Claude authentication when you actually run it.

As of March 25, 2026, Anthropic's official docs say the SDK is authenticated with one of:

- `ANTHROPIC_API_KEY`
- Amazon Bedrock credentials
- Google Vertex AI credentials
- Azure AI Foundry credentials

So if you did not provide any Claude-side credentials, then one of these is true:

- you have not actually run the SDK script yet
- the credential is already set in your shell or environment
- you are using a different runtime that already injected auth

The file creation and edits I made here did not require Claude SDK auth because I was only modifying local files.

Official sources:

- <https://platform.claude.com/docs/en/agent-sdk/overview>
- <https://platform.claude.com/docs/en/agent-sdk/quickstart>
- <https://platform.claude.com/docs/en/agent-sdk/hooks>
