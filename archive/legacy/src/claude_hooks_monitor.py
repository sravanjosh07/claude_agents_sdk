#!/usr/bin/env python3
"""
Claude hooks monitor.

This file shows the Claude Python SDK hook model directly:

- there is no Claude HookProvider base class like Strands
- you define plain async callback functions
- you register them in ClaudeAgentOptions(hooks=...)
- query(...) runs Claude's internal agent loop
- Claude calls your functions when the matching hook event fires

This version keeps everything flat on purpose.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
from claude_agent_sdk.types import AssistantMessage, HookContext, HookJSONOutput, Message, ResultMessage, TextBlock


BLOCKED_WORDS = ("hurt", "hunt", "harm")
RUN_DIR: str | None = None
HOOK_LOG_PATH: str | None = None
MESSAGE_LOG_PATH: str | None = None
SUMMARY_PATH: str | None = None
SUMMARY: dict[str, Any] = {}


def json_safe(value: Any) -> Any:
    """Convert SDK objects and nested values into JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return json_safe(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return json_safe(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {str(k): json_safe(v) for k, v in vars(value).items() if not str(k).startswith("_")}
        except Exception:
            pass
    return repr(value)


def initialize_run_dir(runs_root: str | None = None) -> str:
    """Create a run directory and initialize paths used by the logger."""
    global RUN_DIR, HOOK_LOG_PATH, MESSAGE_LOG_PATH, SUMMARY_PATH, SUMMARY

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_dir = runs_root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")
    RUN_DIR = os.path.join(base_dir, f"entire_log_run_{timestamp}")
    os.makedirs(RUN_DIR, exist_ok=True)

    HOOK_LOG_PATH = os.path.join(RUN_DIR, "hook_events.jsonl")
    MESSAGE_LOG_PATH = os.path.join(RUN_DIR, "sdk_messages.jsonl")
    SUMMARY_PATH = os.path.join(RUN_DIR, "summary.json")

    SUMMARY = {
        "run_dir": RUN_DIR,
        "created_at": timestamp,
        "blocked_words": list(BLOCKED_WORDS),
        "hook_count": 0,
        "message_count": 0,
    }
    write_summary()
    return RUN_DIR


def write_summary() -> None:
    if not SUMMARY_PATH:
        return
    with open(SUMMARY_PATH, "w", encoding="utf-8") as handle:
        json.dump(json_safe(SUMMARY), handle, indent=2)


def append_jsonl(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(json_safe(payload), ensure_ascii=True) + "\n")


def log_hook(hook_name: str, input_data: dict[str, Any], tool_use_id: str | None) -> None:
    SUMMARY["hook_count"] = int(SUMMARY.get("hook_count", 0)) + 1
    write_summary()
    append_jsonl(
        HOOK_LOG_PATH,
        {
            "logged_at": datetime.now().isoformat(),
            "kind": "hook",
            "hook_name": hook_name,
            "tool_use_id_param": tool_use_id,
            "input_data": input_data,
        },
    )
    print(f"[claude-hooks-monitor] hook={hook_name} logged", file=sys.stderr)


def log_message(message: Message) -> None:
    SUMMARY["message_count"] = int(SUMMARY.get("message_count", 0)) + 1
    write_summary()
    append_jsonl(
        MESSAGE_LOG_PATH,
        {
            "logged_at": datetime.now().isoformat(),
            "kind": "sdk_message",
            "message_type": type(message).__name__,
            "message": json_safe(message),
        },
    )


def find_blocked_word(prompt: str) -> str | None:
    lowered = prompt.lower()
    for word in BLOCKED_WORDS:
        if word in lowered:
            return word
    return None


async def on_user_prompt_submit(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("UserPromptSubmit", input_data, tool_use_id)

    matched = find_blocked_word(str(input_data.get("prompt", "")))
    if matched:
        reason = f"Blocked by Claude hook monitor: found '{matched}' in prompt."
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
        }
    return {}


async def on_pre_tool_use(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("PreToolUse", input_data, tool_use_id)
    return {}


async def on_post_tool_use(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("PostToolUse", input_data, tool_use_id)
    return {}


async def on_post_tool_use_failure(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("PostToolUseFailure", input_data, tool_use_id)
    return {}


async def on_stop(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("Stop", input_data, tool_use_id)
    return {}


async def on_subagent_start(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("SubagentStart", input_data, tool_use_id)
    return {}


async def on_subagent_stop(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("SubagentStop", input_data, tool_use_id)
    return {}


async def on_pre_compact(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("PreCompact", input_data, tool_use_id)
    return {}


async def on_notification(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("Notification", input_data, tool_use_id)
    return {}


async def on_permission_request(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    del context
    log_hook("PermissionRequest", input_data, tool_use_id)
    return {}


def build_hooks() -> dict[str, list[HookMatcher]]:
    """This is the actual Claude hook registration step."""
    return {
        "UserPromptSubmit": [HookMatcher(hooks=[on_user_prompt_submit])],
        "PreToolUse": [HookMatcher(hooks=[on_pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[on_post_tool_use])],
        "PostToolUseFailure": [HookMatcher(hooks=[on_post_tool_use_failure])],
        "Stop": [HookMatcher(hooks=[on_stop])],
        "SubagentStart": [HookMatcher(hooks=[on_subagent_start])],
        "SubagentStop": [HookMatcher(hooks=[on_subagent_stop])],
        "PreCompact": [HookMatcher(hooks=[on_pre_compact])],
        "Notification": [HookMatcher(hooks=[on_notification])],
        "PermissionRequest": [HookMatcher(hooks=[on_permission_request])],
    }


def print_message(message: Message) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(f"\n[result] subtype={message.subtype} stop_reason={message.stop_reason} session_id={message.session_id}")


async def run_prompt(prompt: str) -> int:
    run_dir = initialize_run_dir()

    options = ClaudeAgentOptions(
        hooks=build_hooks(),
        allowed_tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"],
        permission_mode="default",
        cwd=os.getcwd(),
    )

    print(f"[claude-hooks-monitor] run directory: {run_dir}", file=sys.stderr)

    try:
        async for message in query(prompt=prompt, options=options):
            log_message(message)
            print_message(message)
    except Exception as exc:
        print(f"[claude-hooks-monitor] run failed: {exc}", file=sys.stderr)
        return 1

    return 0


async def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python3 src/claude_hooks_monitor.py "your prompt here"', file=sys.stderr)
        return 2
    return await run_prompt(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
