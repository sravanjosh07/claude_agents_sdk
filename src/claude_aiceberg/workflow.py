#!/usr/bin/env python3
"""
Stateful workflow for Claude hook monitoring.

This module decides which hooks currently send live Aiceberg traffic and how
fallback closing should work when Claude exits early.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .sender import AicebergResponse, AicebergSender, OpenAicebergEvent


AGENT_EVENT_TYPE = "agt_agt"
TOOL_EVENT_TYPE = "agt_tool"
USER_EVENT_TYPE = "user_agt"

LIVE_AICEBERG_HOOKS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
}
DEFAULT_AICEBERG_USER_ID = os.getenv("AICEBERG_USER_ID", "claudeagent").strip() or "claudeagent"
DEFAULT_BLOCK_CLOSE_MESSAGE = (
    os.getenv("AICEBERG_BLOCK_MESSAGE", "This request was blocked by Aiceberg safety policy.").strip()
    or "This request was blocked by Aiceberg safety policy."
)
FALLBACK_FAILURE_MESSAGE = (
    "This run ended before a final assistant answer was safely completed. "
    "Claude reported a runtime or quota error."
)
INCOMPLETE_TOOL_MESSAGE = (
    "Tool execution did not complete before the assistant finished responding."
)


def build_hook_metadata(input_data: dict[str, Any], **extra: str) -> dict[str, Any]:
    metadata = {
        "hook_event_name": str(input_data.get("hook_event_name", "")).strip(),
        "user_id": DEFAULT_AICEBERG_USER_ID,
    }
    for key, value in extra.items():
        text = str(value).strip()
        if text:
            metadata[key] = text
    return metadata


def build_tool_payload(
    hook_phase: str,
    *,
    tool_name: str,
    tool_input: Any,
    tool_use_id: str = "",
    tool_response: Any = None,
    error: str = "",
) -> dict[str, Any]:
    payload = {
        "hook_phase": hook_phase,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if tool_use_id:
        payload["tool_use_id"] = tool_use_id
    if hook_phase == "post_tool_use":
        payload["tool_response"] = tool_response
    if hook_phase == "post_tool_use_failure" and error:
        payload["error"] = error
    return payload


def classify_tool_event(tool_name: str) -> str:
    if tool_name in {"Agent", "Task"}:
        return AGENT_EVENT_TYPE
    return TOOL_EVENT_TYPE


class ClaudeAicebergWorkflow:
    """Owns event state and the hook-to-Aiceberg mapping rules."""

    def __init__(self, sender: AicebergSender | None = None) -> None:
        self.sender = sender or AicebergSender()
        self.user_events: dict[str, OpenAicebergEvent] = {}
        self.tool_events: dict[str, OpenAicebergEvent] = {}
        self.blocked_events: list[OpenAicebergEvent] = []
        self.started_sessions: set[str] = set()
        self.latest_session_id: str | None = None

    @property
    def is_dry_run(self) -> bool:
        return self.sender.dry_run

    async def handle_user_prompt_submit(self, input_data: dict[str, Any]) -> AicebergResponse:
        session_id = self._session_id(input_data)
        prompt = self._prompt_text(input_data)
        if not session_id or not prompt:
            return AicebergResponse(ok=True, message="skipped_missing_prompt")

        return await self._open_event(
            store=self.user_events,
            store_key=session_id,
            label="user prompt",
            event_type=USER_EVENT_TYPE,
            content=prompt,
            session_id=session_id,
            metadata=build_hook_metadata(input_data),
            session_start=session_id not in self.started_sessions,
            after_store=lambda: self.started_sessions.add(session_id),
        )

    async def handle_pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None) -> AicebergResponse:
        session_id = self._session_id(input_data)
        tool_name = self._tool_name(input_data)
        tool_use_key = self._tool_use_key(input_data, tool_use_id)
        if not session_id or not tool_name or not tool_use_key:
            return AicebergResponse(ok=True, message="skipped_missing_tool_context")

        return await self._open_event(
            store=self.tool_events,
            store_key=tool_use_key,
            label=f"tool {tool_name}",
            event_type=classify_tool_event(tool_name),
            content=self._tool_payload("pre_tool_use", input_data, tool_use_key),
            session_id=session_id,
            metadata=self._tool_metadata(input_data, tool_name, tool_use_key),
        )

    async def handle_post_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None) -> None:
        tool_use_key = self._tool_use_key(input_data, tool_use_id)
        if not tool_use_key:
            return

        tool_name = self._tool_name(input_data)
        await self._close_event(
            store=self.tool_events,
            store_key=tool_use_key,
            output=self._tool_payload("post_tool_use", input_data, tool_use_key),
            metadata=self._tool_metadata(input_data, tool_name, tool_use_key),
        )

    async def handle_post_tool_use_failure(self, input_data: dict[str, Any], tool_use_id: str | None) -> None:
        tool_use_key = self._tool_use_key(input_data, tool_use_id)
        if not tool_use_key:
            return

        tool_name = self._tool_name(input_data)
        await self._close_event(
            store=self.tool_events,
            store_key=tool_use_key,
            output=self._tool_payload("post_tool_use_failure", input_data, tool_use_key),
            metadata=self._tool_metadata(input_data, tool_name, tool_use_key),
        )

    async def handle_permission_request(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_stop(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_session_start(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_session_end(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_subagent_start(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_subagent_stop(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_pre_compact(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def handle_notification(self, input_data: dict[str, Any]) -> AicebergResponse:
        return self._skip_non_live_hook(input_data)

    async def complete_user_turn(self, session_id: str, output_text: str) -> None:
        await self.flush_blocked_events(session_id)
        await self._close_session_tool_events(
            session_id,
            message=INCOMPLETE_TOOL_MESSAGE,
            hook_phase="session_close",
        )
        if not output_text.strip():
            return
        await self._close_event(
            store=self.user_events,
            store_key=session_id,
            output=output_text,
        )

    async def fail_session(self, session_id: str, detail: str) -> None:
        if not session_id:
            return

        await self.flush_blocked_events(session_id)
        fallback_output = f"{FALLBACK_FAILURE_MESSAGE}\n\nDetails: {detail}"
        await self._close_event(
            store=self.user_events,
            store_key=session_id,
            output=fallback_output,
        )
        await self._close_session_tool_events(
            session_id,
            message=fallback_output,
            hook_phase="fallback_close",
        )

    async def flush_blocked_events(self, session_id: str | None = None) -> None:
        remaining: list[OpenAicebergEvent] = []
        for event in self.blocked_events:
            if session_id and event.session_id != session_id:
                remaining.append(event)
                continue

            response = await self.sender.close_event(event, output=DEFAULT_BLOCK_CLOSE_MESSAGE)
            if not response.ok:
                self._log_close_failure(event, response)
                remaining.append(event)

        self.blocked_events = remaining

    def report_unresolved_events(self) -> None:
        pending = [*self.user_events.values(), *self.tool_events.values(), *self.blocked_events]
        if not pending:
            return

        print("[claude-aiceberg-workflow] unresolved Aiceberg events remain open:")
        for event in pending:
            print(
                f"  - {event.label}: event_id={event.event_id} "
                f"event_type={event.event_type} session_id={event.session_id}"
            )

    async def _open_event(
        self,
        *,
        store: dict[str, OpenAicebergEvent],
        store_key: str,
        label: str,
        event_type: str,
        content: str | dict[str, Any] | list[Any],
        session_id: str,
        metadata: dict[str, Any] | None = None,
        session_start: bool = False,
        after_store: Callable[[], None] | None = None,
    ) -> AicebergResponse:
        self.latest_session_id = session_id
        response, open_event = await self.sender.create_event(
            label=label,
            event_type=event_type,
            content=content,
            session_id=session_id,
            metadata=metadata,
            session_start=session_start,
        )
        if response.blocked:
            self._queue_blocked_event(open_event)
            return response
        if open_event:
            store[store_key] = open_event
            if after_store:
                after_store()
        return response

    async def _close_event(
        self,
        *,
        store: dict[str, OpenAicebergEvent],
        store_key: str,
        output: str | dict[str, Any] | list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = store.get(store_key)
        if not event:
            return

        response = await self.sender.close_event(event, output=output, metadata=metadata)
        if response.ok:
            store.pop(store_key, None)
            return
        self._log_close_failure(event, response)

    def _queue_blocked_event(self, event: OpenAicebergEvent | None) -> None:
        if event:
            self.blocked_events.append(event)

    async def _close_session_tool_events(self, session_id: str, *, message: str, hook_phase: str) -> None:
        tool_keys = [tool_use_id for tool_use_id, event in self.tool_events.items() if event.session_id == session_id]
        for tool_use_id in tool_keys:
            await self._close_event(
                store=self.tool_events,
                store_key=tool_use_id,
                output={
                    "hook_phase": hook_phase,
                    "tool_use_id": tool_use_id,
                    "message": message,
                },
            )

    @staticmethod
    def _prompt_text(input_data: dict[str, Any]) -> str:
        return str(input_data.get("prompt", input_data.get("user_prompt", ""))).strip()

    @staticmethod
    def _tool_name(input_data: dict[str, Any]) -> str:
        return str(input_data.get("tool_name", "")).strip()

    @staticmethod
    def _tool_payload(hook_phase: str, input_data: dict[str, Any], tool_use_key: str) -> dict[str, Any]:
        return build_tool_payload(
            hook_phase,
            tool_name=str(input_data.get("tool_name", "")).strip(),
            tool_input=input_data.get("tool_input", {}) or {},
            tool_use_id=tool_use_key,
            tool_response=input_data.get("tool_response"),
            error=str(input_data.get("error", "")),
        )

    @staticmethod
    def _tool_metadata(input_data: dict[str, Any], tool_name: str, tool_use_key: str) -> dict[str, Any]:
        return build_hook_metadata(
            input_data,
            tool_name=tool_name,
            tool_use_id=tool_use_key,
        )

    @staticmethod
    def _session_id(input_data: dict[str, Any]) -> str:
        return str(input_data.get("session_id", "")).strip()

    @staticmethod
    def _tool_use_key(input_data: dict[str, Any], tool_use_id: str | None) -> str:
        return str(tool_use_id or input_data.get("tool_use_id", "")).strip()

    def _skip_non_live_hook(self, input_data: dict[str, Any]) -> AicebergResponse:
        hook_name = str(input_data.get("hook_event_name", "")).strip()
        session_id = self._session_id(input_data)
        self.latest_session_id = session_id or self.latest_session_id
        if hook_name and hook_name not in LIVE_AICEBERG_HOOKS:
            return AicebergResponse(ok=True, message=f"skipped_non_live_hook:{hook_name}")
        return AicebergResponse(ok=True, message="skipped_non_live_hook")

    @staticmethod
    def _log_close_failure(event: OpenAicebergEvent, response: AicebergResponse) -> None:
        status = f" status={response.status_code}" if response.status_code is not None else ""
        detail = response.message or "unknown error"
        print(f"[claude-aiceberg-workflow] close failed for {event.label}:{status} {detail}")
