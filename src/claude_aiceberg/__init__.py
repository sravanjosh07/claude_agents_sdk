"""
claude_aiceberg -- Claude + Aiceberg hook monitoring.

Public API:
    hooks = ClaudeAicebergHooks()
    options = hooks.agent_options(prompt="Hello")
    result = await query(options=options)
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from .hooks import build_block_output, build_hook_registry
from .sender import AicebergResponse, AicebergSender
from .workflow import ClaudeAicebergWorkflow, SubagentRecord

__all__ = [
    "ClaudeAicebergHooks",
    "AicebergSender",
    "AicebergResponse",
    "SubagentRecord",
    "AgentDefinition",
    "DEFAULT_MODEL",
    "DEFAULT_ALLOWED_TOOLS",
]

DEFAULT_MODEL = "haiku"
DEFAULT_ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent",
]


class ClaudeAicebergHooks:
    """One-stop facade: builds agent options with Aiceberg hooks wired in."""

    def __init__(
        self,
        sender: AicebergSender | None = None,
        agents: dict[str, AgentDefinition] | None = None,
    ) -> None:
        self.workflow = ClaudeAicebergWorkflow(sender)
        self._agents = agents

    @property
    def is_dry_run(self) -> bool:
        return self.workflow.is_dry_run

    def agent_options(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
        allowed_tools: list[str] | None = None,
        max_turns: int = 25,
        **extra: Any,
    ) -> ClaudeAgentOptions:
        hooks = build_hook_registry(self.workflow)
        opts: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "allowed_tools": allowed_tools or list(DEFAULT_ALLOWED_TOOLS),
            "max_turns": max_turns,
            "hooks": hooks,
            **extra,
        }
        if self._agents:
            opts["agents"] = self._agents
        return ClaudeAgentOptions(**opts)

    # ── Lifecycle delegators ─────────────────────────────────────────────

    async def complete_user_turn(
        self, session_id: str, output_text: str,
    ) -> None:
        await self.workflow.complete_user_turn(session_id, output_text)

    async def fail_session(self, session_id: str, detail: str) -> None:
        await self.workflow.fail_session(session_id, detail)

    def report_unresolved_events(self) -> None:
        self.workflow.report_unresolved_events()

    def report_subagents(self) -> None:
        self.workflow.report_subagents()

    def list_subagents(self) -> list[SubagentRecord]:
        return self.workflow.list_subagents()
