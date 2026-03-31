#!/usr/bin/env python3
"""
Example: Claude + Aiceberg hooks.

Usage:
    python examples/run_hooks.py                  # simple prompt
    python examples/run_hooks.py --subagents      # with named subagents
"""

from __future__ import annotations

import asyncio
import sys

from claude_agent_sdk import AgentDefinition, query

from claude_aiceberg import ClaudeAicebergHooks, AicebergSender

PROMPT = "what is the weather in houston for this weekend?"

SUBAGENT_PROMPT = (
    "Count the Python files in this project, then summarise the project "
    "structure. Use the file-counter agent for counting and the summarizer "
    "agent for the summary."
)

AGENTS = {
    "file-counter": AgentDefinition(
        name="file-counter",
        description="Counts files matching a pattern.",
        instructions="Use Glob + Bash to count files. Return the count.",
        allowed_tools=["Glob", "Bash"],
    ),
    "summarizer": AgentDefinition(
        name="summarizer",
        description="Summarises project structure.",
        instructions="Read key files and give a concise summary.",
        allowed_tools=["Read", "Glob", "Grep"],
    ),
}


async def run(prompt: str, hooks: ClaudeAicebergHooks) -> None:
    options = hooks.agent_options(prompt=prompt)
    print(f"[run] model={options.model} dry_run={hooks.is_dry_run}")
    print(f"[run] prompt: {prompt[:80]}...")

    session_id: str | None = None
    final_text = ""

    try:
        async for msg in query(options=options):
            kind = type(msg).__name__
            sid = getattr(msg, "session_id", None)
            if sid:
                session_id = sid

            if kind == "AssistantMessage":
                for block in getattr(msg, "content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        final_text = block["text"]
            elif kind == "ResultMessage":
                result_text = getattr(msg, "text", "")
                if result_text:
                    final_text = result_text

            if kind not in ("AssistantMessage", "ResultMessage"):
                print(f"  [{kind}]")

    except Exception as exc:
        print(f"[run] error: {exc}", file=sys.stderr)
        if session_id:
            await hooks.fail_session(session_id, str(exc))
        return

    if session_id and final_text:
        await hooks.complete_user_turn(session_id, final_text)

    hooks.report_subagents()
    hooks.report_unresolved_events()

    if final_text:
        print(f"\n-- Assistant --\n{final_text[:500]}")


def main() -> None:
    use_subagents = "--subagents" in sys.argv
    sender = AicebergSender()
    agents = AGENTS if use_subagents else None
    hooks = ClaudeAicebergHooks(sender=sender, agents=agents)
    prompt = SUBAGENT_PROMPT if use_subagents else PROMPT
    asyncio.run(run(prompt, hooks))


if __name__ == "__main__":
    main()
