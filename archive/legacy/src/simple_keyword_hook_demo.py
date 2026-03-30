#!/usr/bin/env python3
"""
Simplest possible Claude hook demo.

What it shows:
- how to register a hook
- how to inspect the incoming prompt
- how to block execution before Claude starts working

No Aiceberg.
No `.env`.
Just a local keyword rule.

Blocked words:
- hurt
- hunt
- harm
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
from claude_agent_sdk.types import AssistantMessage, HookContext, HookJSONOutput, Message, ResultMessage, TextBlock


BLOCKED_WORDS = ("hurt", "hunt", "harm")


def find_blocked_word(prompt: str) -> str | None:
    lowered = prompt.lower()
    for word in BLOCKED_WORDS:
        if word in lowered:
            return word
    return None


async def block_keywords(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Block at UserPromptSubmit if a blocked word is present."""
    del tool_use_id, context

    prompt = str(input_data.get("prompt", ""))
    matched = find_blocked_word(prompt)

    if matched:
        reason = f"Blocked by simple keyword hook: found '{matched}' in the prompt."
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
        }

    return {}


def print_message(message: Message) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(f"\n[result] subtype={message.subtype} stop_reason={message.stop_reason}")


async def run_prompt(prompt: str) -> int:
    options = ClaudeAgentOptions(
        hooks={
            "UserPromptSubmit": [HookMatcher(hooks=[block_keywords])],
        },
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        cwd=os.getcwd(),
    )

    try:
        async for message in query(prompt=prompt, options=options):
            print_message(message)
    except Exception as exc:
        print(f"[simple-keyword-hook-demo] run failed: {exc}", file=sys.stderr)
        return 1

    return 0


async def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python3 src/simple_keyword_hook_demo.py "your prompt here"', file=sys.stderr)
        return 2

    return await run_prompt(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
