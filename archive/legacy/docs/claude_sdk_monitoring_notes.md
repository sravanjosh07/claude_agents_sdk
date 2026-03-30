# Claude SDK Monitoring Notes

## Goal

Port the Aiceberg monitoring idea from Strands to Claude's SDK/runtime:

- identify the lifecycle events Claude exposes
- capture the payloads at the right boundaries
- send them to Aiceberg for safety verification
- block or warn when the payload is unsafe

## What the Strands monitor is doing well

The Strands monitor in `/Users/sravanjosh/Documents/Aiceberg/ab_strands_samples/src/ab_strands_samples/aiceberg_monitor.py` is built around a clear invariant:

- every logical INPUT should have exactly one OUTPUT

It tracks three logical channels:

- `user_agent`
- `agent_llm`
- `agent_tool`

And it uses native Strands hooks for:

- user message arrival
- before model call
- after model call
- before tool call
- after tool call
- final response

That gives it a clean pre/post model boundary and a clean pre/post tool boundary.

## What Claude exposes

Claude's current docs call this the **Claude Agent SDK**. It was previously called the **Claude Code SDK**.

Important architecture notes:

- the SDK is built on the same agent harness that powers Claude Code
- the SDK exposes a streamed message loop
- Claude Code also exposes a hook system around the agent lifecycle

The major surfaces we can monitor are:

1. SDK message stream
2. SDK hook callbacks
3. Claude Code transcript files
4. Claude Code OpenTelemetry output

## Core SDK loop

From the Agent SDK docs, the loop is:

1. session starts
2. Claude evaluates prompt and emits an assistant message
3. Claude may request tools
4. tool results are fed back as user-side continuation messages
5. loop repeats until no more tool calls
6. a final `ResultMessage` is emitted

Useful Python message types:

- `SystemMessage`
- `AssistantMessage`
- `UserMessage`
- `ResultMessage`
- `StreamEvent` when `include_partial_messages=True`

Useful TypeScript message types include everything above plus richer observability events:

- hook started/progress/response
- tool progress
- task started/progress/notification
- rate limit events

## Hooks Claude exposes

Claude Code hooks are the strongest deterministic interception point.

Important hook events from the current docs:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- `Stop`
- `SubagentStart`
- `SubagentStop`
- `PermissionRequest`
- `SessionStart`
- `SessionEnd`
- `PreCompact`
- `PostCompact`

Important caveat:

- Python SDK hook support is currently narrower than the full Claude Code/TypeScript hook surface
- Python docs explicitly note that some events are TypeScript-only for now, including `SessionStart` and `SessionEnd`

## Best mapping from Strands to Claude

### 1. `user_agent`

Best equivalent:

- input: `UserPromptSubmit`
- output: final `ResultMessage.result` or final `AssistantMessage` text

Why:

- `UserPromptSubmit` is the earliest deterministic point before Claude processes the prompt
- it lets us block before the agent loop starts

### 2. `agent_tool`

Best equivalent:

- input: `PreToolUse`
- output success: `PostToolUse`
- output failure: `PostToolUseFailure`

Why:

- this is the cleanest one-to-one mapping to the Strands tool hooks
- Claude explicitly supports blocking or denying tools from `PreToolUse`

### 3. `agent_llm`

This is the hardest part.

Claude does **not** expose a Strands-style explicit `BeforeModelCall` / `AfterModelCall` pair in the Python SDK hook system.

The practical approximation is:

- LLM input:
  - synthesize from current session state before Claude's next assistant turn
  - use the prompt plus recent tool results plus system configuration we control
- LLM output:
  - capture each `AssistantMessage`

So for Claude, `agent_llm` is likely a **derived event**, not a native hook event.

## Recommended implementation strategy

### Preferred path for Python

Use `ClaudeSDKClient`, not bare `query()`, because it gives us:

- persistent sessions
- hooks
- streamed message consumption
- better control for multi-turn monitoring

Recommended monitoring stack:

1. `UserPromptSubmit` hook
   - send `user_agent` INPUT to Aiceberg
   - block immediately if unsafe

2. `PreToolUse` hook
   - send `agent_tool` INPUT
   - if blocked, deny tool use using hook output

3. `PostToolUse` hook
   - send `agent_tool` OUTPUT

4. `PostToolUseFailure` hook
   - send `agent_tool` OUTPUT with failure payload

5. streamed `AssistantMessage`
   - treat each assistant turn as `agent_llm` OUTPUT
   - if the assistant contains tool uses, this is still a valid assistant turn

6. streamed `UserMessage` with `tool_use_result`
   - use this to help synthesize the next `agent_llm` INPUT

7. final `ResultMessage`
   - close the open `user_agent` event

### Preferred path if we want the richest observability

Use the TypeScript SDK.

Why:

- broader hook coverage
- extra system messages for hook progress and tool progress
- easier correlation of hook execution with streamed SDK events

If the product goal is "monitor everything Claude exposes," TypeScript currently looks stronger.

## Event correlation model

We should keep the same Aiceberg pairing idea from Strands:

- open `user_agent` at prompt submit
- open `agent_tool` at pre-tool
- close `agent_tool` at post-tool or post-tool-failure
- open/close synthetic `agent_llm` per assistant turn
- close `user_agent` at final result

For correlation keys, Claude gives us useful IDs:

- `session_id`
- `tool_use_id`
- `agent_id` for subagents
- transcript path from hooks

These should be included in Aiceberg metadata.

## What not to rely on as the primary safety path

### OpenTelemetry

Claude Code telemetry is useful for observability, but not ideal as the main safety gate:

- it is opt-in
- prompt content is redacted by default
- tool details are also redacted by default
- it is better for metrics and audits than for inline allow/block decisions

Use it as secondary analytics, not as the primary monitoring pipeline.

### Transcript-only monitoring

Transcripts are useful for debugging and replay, but they are after-the-fact.

They do not replace pre-execution blocking for:

- user prompt checks
- tool call checks

## Main gaps vs Strands

Compared with Strands, Claude has two important differences:

1. tool monitoring is first-class and strong
2. model-call monitoring is less explicit and needs synthesis

That means the Claude version should probably be designed as:

- native monitoring for user and tool boundaries
- synthesized monitoring for assistant/model boundaries

## Safety design recommendation

Use a two-level policy:

1. hard block
   - unsafe user prompts
   - unsafe tool invocations

2. soft warn
   - suspicious assistant output
   - possible data exfiltration
   - sensitive content in tool results

Hard blocking is easy at `UserPromptSubmit` and `PreToolUse`.
Assistant-output blocking is possible, but ergonomically trickier because it happens after the model turn already exists. In many cases, rewriting, masking, or warning may be more practical than trying to retroactively block.

## Proposed next implementation step

Build a small Python prototype around `ClaudeSDKClient` that:

- registers `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `PostToolUseFailure`
- streams `AssistantMessage`, `UserMessage`, and `ResultMessage`
- keeps Strands-style open/close bookkeeping
- emits Aiceberg payloads with `session_id`, `tool_use_id`, `agent_id`, and transcript metadata

If we hit hook coverage limitations in Python, switch the implementation target to TypeScript instead of fighting the runtime.
