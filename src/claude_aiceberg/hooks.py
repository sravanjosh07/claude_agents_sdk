#!/usr/bin/env python3
"""
Claude hook callbacks and registration for the Aiceberg workflow.

This file keeps the Claude-specific wiring in one place:
- which hook names are supported
- which callback Claude should call for each hook
- how blocked workflow responses become Claude hook JSON
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookContext, HookJSONOutput

from .workflow import ClaudeAicebergWorkflow


SUPPORTED_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "Notification",
    "PermissionRequest",
)


def build_block_output(hook_name: str, reason: str) -> HookJSONOutput:
    output: HookJSONOutput = {
        "decision": "block",
        "reason": reason,
        "systemMessage": reason,
    }
    if hook_name in {"PreToolUse", "PermissionRequest"}:
        output["hookSpecificOutput"] = {
            "hookEventName": hook_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    return output


class ClaudeHookCallbacks:
    """Thin callback adapter around the workflow."""

    def __init__(self, workflow: ClaudeAicebergWorkflow) -> None:
        self.workflow = workflow

    async def on_user_prompt_submit(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_blocking_hook(
            "UserPromptSubmit",
            input_data,
            lambda: self.workflow.handle_user_prompt_submit(input_data),
        )

    async def on_session_start(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_session_start(input_data))

    async def on_pre_tool_use(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del context
        return await self._run_blocking_hook(
            "PreToolUse",
            input_data,
            lambda: self.workflow.handle_pre_tool_use(input_data, tool_use_id),
        )

    async def on_post_tool_use(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del context
        return await self._run_non_blocking_hook(
            lambda: self.workflow.handle_post_tool_use(input_data, tool_use_id)
        )

    async def on_post_tool_use_failure(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del context
        return await self._run_non_blocking_hook(
            lambda: self.workflow.handle_post_tool_use_failure(input_data, tool_use_id)
        )

    async def on_permission_request(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_blocking_hook(
            "PermissionRequest",
            input_data,
            lambda: self.workflow.handle_permission_request(input_data),
        )

    async def on_stop(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_stop(input_data))

    async def on_session_end(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_session_end(input_data))

    async def on_subagent_start(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_subagent_start(input_data))

    async def on_subagent_stop(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_subagent_stop(input_data))

    async def on_pre_compact(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_pre_compact(input_data))

    async def on_notification(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        del tool_use_id, context
        return await self._run_non_blocking_hook(lambda: self.workflow.handle_notification(input_data))

    async def _run_blocking_hook(
        self,
        hook_name: str,
        input_data: dict[str, Any],
        handler: Callable[[], Awaitable[Any]],
    ) -> HookJSONOutput:
        response = await handler()
        if not getattr(response, "blocked", False):
            return {}
        return build_block_output(hook_name, self._block_reason(hook_name, input_data, response))

    async def _run_non_blocking_hook(self, handler: Callable[[], Awaitable[Any]]) -> HookJSONOutput:
        await handler()
        return {}

    @staticmethod
    def _block_reason(hook_name: str, input_data: dict[str, Any], response: Any) -> str:
        if response.message:
            return str(response.message)
        if hook_name == "UserPromptSubmit":
            return "Prompt blocked by Aiceberg safety policy."
        tool_name = str(input_data.get("tool_name", "tool")).strip() or "tool"
        if hook_name == "PermissionRequest":
            return f"Permission request for {tool_name} blocked by Aiceberg safety policy."
        return f"{tool_name} blocked by Aiceberg safety policy."


def build_hook_registry(callbacks: ClaudeHookCallbacks) -> dict[str, list[HookMatcher]]:
    callback_by_hook = {
        "SessionStart": callbacks.on_session_start,
        "UserPromptSubmit": callbacks.on_user_prompt_submit,
        "PreToolUse": callbacks.on_pre_tool_use,
        "PostToolUse": callbacks.on_post_tool_use,
        "PostToolUseFailure": callbacks.on_post_tool_use_failure,
        "Stop": callbacks.on_stop,
        "SessionEnd": callbacks.on_session_end,
        "SubagentStart": callbacks.on_subagent_start,
        "SubagentStop": callbacks.on_subagent_stop,
        "PreCompact": callbacks.on_pre_compact,
        "Notification": callbacks.on_notification,
        "PermissionRequest": callbacks.on_permission_request,
    }
    return {
        hook_name: [HookMatcher(hooks=[callback_by_hook[hook_name]])]
        for hook_name in SUPPORTED_HOOK_EVENTS
    }
