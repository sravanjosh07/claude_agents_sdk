#!/usr/bin/env python3
"""
Minimal Claude Agent SDK + Aiceberg tool guard.

Goal:
- observe the strongest tool boundary: PreToolUse
- send the tool invocation payload to Aiceberg
- deny the tool immediately if Aiceberg says blocked/rejected

This intentionally stays minimal.
It does not pair outputs or monitor subagent completion yet.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
from claude_agent_sdk.types import HookContext, HookJSONOutput

from basic_prompt_guard import BasicAicebergClient, print_message


MEMORY_PATTERNS = ("memory", "store", "save", "remember", "retrieve")
CLIENT = BasicAicebergClient()


def classify_tool(tool_name: str) -> str | None:
    if tool_name == "Agent":
        return "agt_agt"
    if tool_name.lower() == "task":
        return "agt_agt"
    if "aiceberg" in tool_name.lower():
        return None
    if tool_name.startswith("mcp__") and any(p in tool_name.lower() for p in MEMORY_PATTERNS):
        return "agt_mem"
    return "agt_tool"


def local_tool_block(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """
    Small local-only helper for wiring tests without the real API.

    Supported env vars:
    - AICEBERG_TEST_BLOCK_TOOL=Write
    - AICEBERG_TEST_BLOCK_COMMAND_SUBSTRING=rm -rf
    """
    block_tool = os.getenv("AICEBERG_TEST_BLOCK_TOOL", "").strip()
    if block_tool and tool_name == block_tool:
        return f"Blocked by local test rule for tool: {tool_name}"

    command_substring = os.getenv("AICEBERG_TEST_BLOCK_COMMAND_SUBSTRING", "").strip()
    command = str(tool_input.get("command", ""))
    if command_substring and command_substring in command:
        return f"Blocked by local test rule for command substring: {command_substring}"

    return None


async def guard_tool_use(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Hook callback for PreToolUse."""
    del context

    tool_name = str(input_data.get("tool_name", ""))
    tool_input = input_data.get("tool_input", {}) or {}
    session_id = str(input_data.get("session_id", ""))
    event_type = classify_tool(tool_name)

    if not event_type:
        return {}

    local_reason = local_tool_block(tool_name, tool_input)
    if local_reason:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": local_reason,
            },
            "systemMessage": local_reason,
            "reason": local_reason,
        }

    payload = json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id or "",
        }
    )

    metadata = {
        "user_id": "agent",
        "tool_name": tool_name,
        "tool_use_id": tool_use_id or "",
        "cwd": input_data.get("cwd", ""),
        "transcript_path": input_data.get("transcript_path", ""),
        "permission_mode": input_data.get("permission_mode", ""),
        "hook_event_name": input_data.get("hook_event_name", ""),
        "agent_id": input_data.get("agent_id", ""),
        "agent_type": input_data.get("agent_type", ""),
    }

    decision = CLIENT.check_event(
        event_type=event_type,
        content=payload,
        session_id=session_id,
        metadata=metadata,
        session_start=False,
    )

    if decision.blocked:
        reason = decision.message or f"{tool_name} denied by Aiceberg safety policy."
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
            "systemMessage": reason,
            "reason": reason,
        }

    return {}


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 src/basic_tool_guard.py \"your prompt here\"", file=sys.stderr)
        return 2

    prompt = sys.argv[1]

    options = ClaudeAgentOptions(
        hooks={
            "PreToolUse": [HookMatcher(hooks=[guard_tool_use])],
        },
        allowed_tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash"],
        permission_mode="default",
        cwd=os.getcwd(),
    )

    try:
        async for message in query(prompt=prompt, options=options):
            print_message(message)
    except Exception as exc:
        print(f"[basic-tool-guard] run failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
