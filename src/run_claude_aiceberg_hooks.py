#!/usr/bin/env python3
"""
Very small local runner for the Claude + Aiceberg hooks.

Edit `PROMPT` when you want to try a new case.
"""

from __future__ import annotations

import asyncio
import sys

from claude_agent_sdk import ProcessError, query
from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage, TextBlock

from claude_aiceberg import ClaudeAicebergHooks, DEFAULT_MODEL


MODEL = DEFAULT_MODEL
PROMPT = "what is the weather in houston for this weekend?"
# PROMPT = "Please use the Read and Glob tools to inspect this workspace and tell me which hook names are registered."


def print_message(message: Message) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(f"\n[result] subtype={message.subtype} stop_reason={message.stop_reason} session_id={message.session_id}")


def extract_assistant_text(message: Message) -> str:
    if not isinstance(message, AssistantMessage):
        return ""
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock) and block.text:
            parts.append(block.text)
    return "\n".join(parts).strip()


async def run_prompt(prompt: str) -> int:
    aiceberg = ClaudeAicebergHooks(model=MODEL)
    options = aiceberg.agent_options()
    latest_assistant_text = ""
    session_id: str | None = None
    result_message: ResultMessage | None = None

    mode = "dry_run" if aiceberg.is_dry_run else "live"
    print(f"[run-claude-aiceberg-hooks] model={aiceberg.model} aiceberg={mode}")

    try:
        async for message in query(prompt=prompt, options=options):
            print_message(message)
            assistant_text = extract_assistant_text(message)
            if assistant_text:
                latest_assistant_text = assistant_text
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                result_message = message
    except ProcessError as exc:
        failed_session_id = session_id or aiceberg.latest_session_id
        if failed_session_id:
            await aiceberg.flush_blocked_events(failed_session_id)
            await aiceberg.fail_session(failed_session_id, str(exc))
            aiceberg.report_unresolved_events()
        print(f"[run-claude-aiceberg-hooks] process failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        failed_session_id = session_id or aiceberg.latest_session_id
        if failed_session_id:
            await aiceberg.flush_blocked_events(failed_session_id)
            await aiceberg.fail_session(failed_session_id, str(exc))
            aiceberg.report_unresolved_events()
        print(f"[run-claude-aiceberg-hooks] run failed: {exc}", file=sys.stderr)
        return 1

    if result_message and result_message.is_error:
        failed_session_id = session_id or aiceberg.latest_session_id
        if failed_session_id:
            detail = str(result_message.result or result_message.stop_reason or result_message.subtype)
            await aiceberg.flush_blocked_events(failed_session_id)
            await aiceberg.fail_session(failed_session_id, detail)
            aiceberg.report_unresolved_events()
        return 1

    completed_session_id = session_id or aiceberg.latest_session_id
    if completed_session_id:
        await aiceberg.flush_blocked_events(completed_session_id)
        await aiceberg.complete_user_turn(completed_session_id, latest_assistant_text)

    aiceberg.report_unresolved_events()
    return 0


async def main() -> int:
    return await run_prompt(PROMPT)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
