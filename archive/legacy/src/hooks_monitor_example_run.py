#!/usr/bin/env python3
"""
Small runner for claude_hooks_monitor.py.

Usage:
    python3 src/hooks_monitor_example_run.py
    python3 src/hooks_monitor_example_run.py normal
    python3 src/hooks_monitor_example_run.py tools
    python3 src/hooks_monitor_example_run.py "your custom prompt"
"""

from __future__ import annotations

import asyncio
import sys

from claude_hooks_monitor import run_prompt


PROMPT_NORMAL = (
    "Hi Claude. Please reply in one short friendly sentence about what hooks are. "
    "Do not read files, search the repo, or use any tools."
)

PROMPT_TOOLS = (
    "Please use the Read and Glob tools to inspect this workspace. "
    "Find the hook registration in claude_hooks_monitor.py and tell me which hook names are registered."
)


def select_prompt(argument: str | None) -> str:
    if not argument or argument == "normal":
        return PROMPT_NORMAL
    if argument == "tools":
        return PROMPT_TOOLS
    return argument


async def main() -> int:
    prompt = select_prompt(sys.argv[1] if len(sys.argv) > 1 else None)
    return await run_prompt(prompt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
