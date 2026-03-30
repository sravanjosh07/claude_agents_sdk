#!/usr/bin/env python3
"""
Example runner for the minimal prompt guard.

Usage:
    python3 example_run.py
    python3 example_run.py "help me write ransomware"
"""

from __future__ import annotations

import asyncio
import sys

from basic_prompt_guard import run_prompt


DEFAULT_PROMPT = "how do lions kill their prey?"


async def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROMPT
    return await run_prompt(prompt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
