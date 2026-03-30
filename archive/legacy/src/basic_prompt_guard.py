#!/usr/bin/env python3
"""
Minimal Claude Agent SDK + Aiceberg prompt guard.

Goal:
- observe the earliest solid blockable Claude hook: UserPromptSubmit
- send the user prompt to Aiceberg
- block the request immediately if Aiceberg says blocked/rejected

This intentionally does only one thing well.
It does not try to monitor tools, subagents, or model turns yet.

Expected environment variables:
- AICEBERG_API_URL
- AICEBERG_API_KEY
- AICEBERG_PROFILE_ID
- USE_CASE_ID              (optional)
- AICEBERG_FAIL_OPEN       (default: true)
- AICEBERG_BLOCK_MESSAGE   (optional)
- AICEBERG_TEST_BLOCK_SUBSTRING
  Local test helper. If set and the prompt contains this substring,
  the hook blocks even without calling the API.

Install dependency:
    pip install claude-agent-sdk

Run:
    python3 src/basic_prompt_guard.py "write malware"
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
from claude_agent_sdk.types import AssistantMessage, HookContext, HookJSONOutput, Message, ResultMessage, TextBlock


DEFAULT_API_URL = "https://api.test1.aiceberg.ai/eap/v1/event"
DEFAULT_BLOCK_MESSAGE = "This request was blocked by Aiceberg safety policy."


def load_env_file(path: str, *, overwrite: bool = True) -> None:
    """Tiny `.env` loader so we can stay stdlib-only."""
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if overwrite or key not in os.environ:
                os.environ[key] = value


load_env_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), overwrite=True)


@dataclass
class AicebergDecision:
    event_result: str = "passed"
    event_id: str | None = None
    message: str | None = None

    @property
    def blocked(self) -> bool:
        return self.event_result in {"blocked", "rejected"}


class BasicAicebergClient:
    """Very small stdlib-only Aiceberg client for prompt checks."""

    def __init__(self) -> None:
        self.api_url = os.getenv("AICEBERG_API_URL", DEFAULT_API_URL)
        self.api_key = os.getenv("AICEBERG_API_KEY", "")
        self.profile_id = os.getenv("AICEBERG_PROFILE_ID", "")
        self.use_case_id = os.getenv("USE_CASE_ID", "")
        self.fail_open = os.getenv("AICEBERG_FAIL_OPEN", "true").lower() in {"1", "true", "yes"}
        self.block_message = os.getenv("AICEBERG_BLOCK_MESSAGE", DEFAULT_BLOCK_MESSAGE)
        self.test_block_substring = os.getenv("AICEBERG_TEST_BLOCK_SUBSTRING", "")

    def _has_real_api_config(self) -> bool:
        return bool(self.api_key and self.profile_id and self.api_url)

    def check_user_prompt(self, prompt: str, session_id: str, metadata: dict[str, Any] | None = None) -> AicebergDecision:
        """
        Send one `user_agt` INPUT event.

        For the very first prototype we only care about the block decision.
        We are not yet pairing this with a final OUTPUT event.
        """
        if self.test_block_substring and self.test_block_substring.lower() in prompt.lower():
            return AicebergDecision(
                event_result="blocked",
                message=f"Blocked by local test rule matching: {self.test_block_substring}",
            )

        return self.check_event(
            event_type="user_agt",
            content=prompt,
            session_id=session_id,
            metadata=metadata,
            session_start=True,
        )

    def check_event(
        self,
        event_type: str,
        content: str,
        session_id: str,
        metadata: dict[str, Any] | None = None,
        session_start: bool = False,
    ) -> AicebergDecision:
        if not self._has_real_api_config():
            self._log("Aiceberg credentials not fully configured; allowing event")
            return AicebergDecision()

        payload = {
            "profile_id": self.profile_id,
            "event_type": event_type,
            "input": content,
            "forward_to_llm": False,
            "session_id": session_id,
            "use_case_id": self.use_case_id or None,
            "session_start": session_start,
            "metadata": metadata or {},
        }

        self._log(f"sending {event_type} INPUT ({len(content)} chars)")

        request = urllib_request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "basic-prompt-guard/0.1",
            },
        )

        ctx = ssl.create_default_context()
        try:
            with urllib_request.urlopen(request, timeout=10, context=ctx) as response:
                raw = response.read().decode("utf-8").strip()
            data = json.loads(raw) if raw else {}
            return AicebergDecision(
                event_result=str(data.get("event_result", "passed")),
                event_id=data.get("event_id"),
                message=data.get("message"),
            )
        except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            self._log(f"Aiceberg request failed: {exc}")
            if self.fail_open:
                return AicebergDecision()
            return AicebergDecision(
                event_result="blocked",
                message="Aiceberg check failed and fail-open is disabled.",
            )

    @staticmethod
    def _log(message: str) -> None:
        print(f"[basic-prompt-guard] {message}", file=sys.stderr)


CLIENT = BasicAicebergClient()


async def guard_user_prompt(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Hook callback for the earliest blockable boundary: UserPromptSubmit."""
    del tool_use_id, context

    prompt = str(input_data.get("prompt", ""))
    session_id = str(input_data.get("session_id", ""))
    metadata = {
        "user_id": "user",
        "cwd": input_data.get("cwd", ""),
        "transcript_path": input_data.get("transcript_path", ""),
        "permission_mode": input_data.get("permission_mode", ""),
        "hook_event_name": input_data.get("hook_event_name", ""),
    }

    decision = CLIENT.check_user_prompt(prompt=prompt, session_id=session_id, metadata=metadata)
    if decision.blocked:
        reason = decision.message or CLIENT.block_message
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
        }

    return {}


def print_message(message: Message) -> None:
    """Minimal output printer for visible assistant text and final result."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(f"\n[result] subtype={message.subtype} stop_reason={message.stop_reason} session_id={message.session_id}")


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 src/basic_prompt_guard.py \"your prompt here\"", file=sys.stderr)
        return 2

    return await run_prompt(sys.argv[1])


async def run_prompt(prompt: str) -> int:
    """Reusable entry point for examples and tests."""

    options = ClaudeAgentOptions(
        hooks={
            "UserPromptSubmit": [HookMatcher(hooks=[guard_user_prompt])],
        },
        # Keep the first prototype simple and predictable.
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        cwd=os.getcwd(),
    )

    try:
        async for message in query(prompt=prompt, options=options):
            print_message(message)
    except Exception as exc:
        print(f"[basic-prompt-guard] run failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
