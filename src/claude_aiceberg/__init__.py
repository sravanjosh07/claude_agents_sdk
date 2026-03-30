#!/usr/bin/env python3
"""
Public package surface for the Claude + Aiceberg integration.

The internal modules stay split by responsibility, but the main thing you
import should stay small and friendly.
"""

from __future__ import annotations

import os

from claude_agent_sdk import ClaudeAgentOptions

from .hooks import ClaudeHookCallbacks, SUPPORTED_HOOK_EVENTS, build_hook_registry
from .sender import AicebergResponse, AicebergSender, OpenAicebergEvent
from .workflow import ClaudeAicebergWorkflow


DEFAULT_MODEL = "haiku"
DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"]
DEFAULT_PERMISSION_MODE = "default"


class ClaudeAicebergHooks:
    """Small facade for the full Claude + Aiceberg hook package."""

    def __init__(
        self,
        *,
        sender: AicebergSender | None = None,
        model: str | None = DEFAULT_MODEL,
        cwd: str | None = None,
    ) -> None:
        self.sender = sender or AicebergSender()
        self.workflow = ClaudeAicebergWorkflow(sender=self.sender)
        self.callbacks = ClaudeHookCallbacks(self.workflow)
        self.model = model
        self.cwd = cwd or os.getcwd()
        self.allowed_tools = list(DEFAULT_ALLOWED_TOOLS)
        self.permission_mode = DEFAULT_PERMISSION_MODE
        self._hooks = build_hook_registry(self.callbacks)

    @property
    def is_dry_run(self) -> bool:
        return self.workflow.is_dry_run

    @property
    def latest_session_id(self) -> str | None:
        return self.workflow.latest_session_id

    @property
    def hooks(self) -> dict[str, list]:
        return self._hooks

    def agent_options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            hooks=self.hooks,
            allowed_tools=self.allowed_tools,
            permission_mode=self.permission_mode,
            cwd=self.cwd,
            model=self.model,
        )

    async def complete_user_turn(self, session_id: str, output_text: str) -> None:
        await self.workflow.complete_user_turn(session_id, output_text)

    async def fail_session(self, session_id: str, detail: str) -> None:
        await self.workflow.fail_session(session_id, detail)

    async def flush_blocked_events(self, session_id: str | None = None) -> None:
        await self.workflow.flush_blocked_events(session_id)

    def report_unresolved_events(self) -> None:
        self.workflow.report_unresolved_events()


__all__ = [
    "AicebergResponse",
    "AicebergSender",
    "ClaudeAicebergHooks",
    "ClaudeAicebergWorkflow",
    "ClaudeHookCallbacks",
    "DEFAULT_ALLOWED_TOOLS",
    "DEFAULT_MODEL",
    "OpenAicebergEvent",
    "SUPPORTED_HOOK_EVENTS",
    "build_hook_registry",
]
