"""
Stateful event lifecycle for Claude + Aiceberg hook monitoring.

Tracks open Aiceberg events (prompts, tools), subagents, and handles
close/fallback logic when Claude finishes or errors out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .sender import AicebergResponse, AicebergSender, OpenAicebergEvent

# ── Event types ──────────────────────────────────────────────────────────────

AGENT_EVENT_TYPE = "agt_agt"
TOOL_EVENT_TYPE = "agt_tool"
USER_EVENT_TYPE = "user_agt"

# Hooks that actually open/close Aiceberg events or track subagents.
LIVE_HOOKS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "SubagentStart", "SubagentStop",
})

DEFAULT_USER_ID = os.getenv("AICEBERG_USER_ID", "claudeagent").strip() or "claudeagent"
BLOCK_MESSAGE = (
    os.getenv("AICEBERG_BLOCK_MESSAGE",
              "This request was blocked by Aiceberg safety policy.").strip()
    or "This request was blocked by Aiceberg safety policy."
)
FAILURE_MESSAGE = (
    "This run ended before a final assistant answer was safely completed. "
    "Claude reported a runtime or quota error."
)
INCOMPLETE_TOOL_MESSAGE = (
    "Tool execution did not complete before the assistant finished responding."
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def classify_tool_event(tool_name: str) -> str:
    """Agent/Task tools get agt_agt, everything else gets agt_tool."""
    return AGENT_EVENT_TYPE if tool_name in {"Agent", "Task"} else TOOL_EVENT_TYPE


def _get(data: dict[str, Any], key: str) -> str:
    return str(data.get(key, "")).strip()


def _metadata(data: dict[str, Any], **extra: str) -> dict[str, Any]:
    md: dict[str, Any] = {
        "hook_event_name": _get(data, "hook_event_name"),
        "user_id": DEFAULT_USER_ID,
    }
    for k, v in extra.items():
        text = str(v).strip()
        if text:
            md[k] = text
    return md


def _tool_payload(
    phase: str,
    *,
    tool_name: str,
    tool_input: Any,
    tool_use_id: str = "",
    tool_response: Any = None,
    error: str = "",
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "hook_phase": phase, "tool_name": tool_name, "tool_input": tool_input,
    }
    if tool_use_id:
        p["tool_use_id"] = tool_use_id
    if phase == "post_tool_use":
        p["tool_response"] = tool_response
    if phase == "post_tool_use_failure" and error:
        p["error"] = error
    return p


# ── Subagent record ─────────────────────────────────────────────────────────

@dataclass
class SubagentRecord:
    """Tracks one subagent from start to stop."""
    agent_id: str
    agent_type: str
    parent_session_id: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    stopped_at: str | None = None
    transcript_path: str | None = None


# ── Workflow ─────────────────────────────────────────────────────────────────

class ClaudeAicebergWorkflow:
    """Owns open-event state and the hook -> Aiceberg mapping rules."""

    def __init__(self, sender: AicebergSender | None = None) -> None:
        self.sender = sender or AicebergSender()
        self.user_events: dict[str, OpenAicebergEvent] = {}
        self.tool_events: dict[str, OpenAicebergEvent] = {}
        self.started_sessions: set[str] = set()
        self.latest_session_id: str | None = None
        self.subagents: dict[str, SubagentRecord] = {}

    @property
    def is_dry_run(self) -> bool:
        return self.sender.dry_run

    # ── Prompt ───────────────────────────────────────────────────────────

    async def handle_user_prompt_submit(
        self, data: dict[str, Any],
    ) -> AicebergResponse:
        sid = _get(data, "session_id")
        prompt = str(data.get("prompt", data.get("user_prompt", ""))).strip()
        if not sid or not prompt:
            return AicebergResponse(ok=True, message="skipped_missing_prompt")

        self.latest_session_id = sid
        is_new = sid not in self.started_sessions
        response, event = await self.sender.create_event(
            label="user prompt",
            event_type=USER_EVENT_TYPE,
            content=prompt,
            session_id=sid,
            metadata=_metadata(data),
            session_start=is_new,
        )
        if response.blocked:
            if event:
                await self.sender.close_event(event, output=BLOCK_MESSAGE)
            return response
        if event:
            self.user_events[sid] = event
            self.started_sessions.add(sid)
        return response

    # ── Tools ────────────────────────────────────────────────────────────

    async def handle_pre_tool_use(
        self, data: dict[str, Any], tool_use_id: str | None,
    ) -> AicebergResponse:
        sid = _get(data, "session_id")
        name = _get(data, "tool_name")
        key = str(tool_use_id or data.get("tool_use_id", "")).strip()
        if not sid or not name or not key:
            return AicebergResponse(ok=True, message="skipped_missing_tool_context")

        self.latest_session_id = sid
        response, event = await self.sender.create_event(
            label=f"tool {name}",
            event_type=classify_tool_event(name),
            content=_tool_payload(
                "pre_tool_use", tool_name=name,
                tool_input=data.get("tool_input", {}), tool_use_id=key,
            ),
            session_id=sid,
            metadata=self._tool_meta(data, name, key),
        )
        if response.blocked:
            if event:
                await self.sender.close_event(event, output=BLOCK_MESSAGE)
            return response
        if event:
            self.tool_events[key] = event
        return response

    async def handle_post_tool_use(
        self, data: dict[str, Any], tool_use_id: str | None,
    ) -> None:
        await self._close_tool(data, tool_use_id, "post_tool_use")

    async def handle_post_tool_use_failure(
        self, data: dict[str, Any], tool_use_id: str | None,
    ) -> None:
        await self._close_tool(data, tool_use_id, "post_tool_use_failure")

    async def _close_tool(
        self, data: dict[str, Any], tool_use_id: str | None, phase: str,
    ) -> None:
        key = str(tool_use_id or data.get("tool_use_id", "")).strip()
        event = self.tool_events.get(key) if key else None
        if not event:
            return
        name = _get(data, "tool_name")
        resp = await self.sender.close_event(
            event,
            output=_tool_payload(
                phase, tool_name=name,
                tool_input=data.get("tool_input", {}), tool_use_id=key,
                tool_response=data.get("tool_response"),
                error=str(data.get("error", "")),
            ),
            metadata=self._tool_meta(data, name, key),
        )
        if resp.ok:
            self.tool_events.pop(key, None)

    # ── Subagents ────────────────────────────────────────────────────────

    async def handle_subagent_start(
        self, data: dict[str, Any],
    ) -> AicebergResponse:
        sid = _get(data, "session_id")
        agent_id = _get(data, "agent_id")
        self.latest_session_id = sid or self.latest_session_id
        if not agent_id:
            return AicebergResponse(ok=True, message="skipped_missing_agent_id")
        self.subagents[agent_id] = SubagentRecord(
            agent_id=agent_id,
            agent_type=_get(data, "agent_type"),
            parent_session_id=sid,
        )
        return AicebergResponse(ok=True, message=f"subagent_registered:{agent_id}")

    async def handle_subagent_stop(
        self, data: dict[str, Any],
    ) -> AicebergResponse:
        sid = _get(data, "session_id")
        agent_id = _get(data, "agent_id")
        self.latest_session_id = sid or self.latest_session_id
        record = self.subagents.get(agent_id)
        if record:
            record.stopped_at = datetime.now(timezone.utc).isoformat()
            record.transcript_path = _get(data, "agent_transcript_path") or None
        return AicebergResponse(ok=True, message=f"subagent_stopped:{agent_id}")

    def list_subagents(self) -> list[SubagentRecord]:
        return list(self.subagents.values())

    # ── Stop (SDK path) ────────────────────────────────────────────────

    async def handle_stop(self, data: dict[str, Any]) -> AicebergResponse:
        """Close the user event when the SDK fires a Stop hook.

        The SDK's Stop data may include `last_assistant_message`.
        If the runner already called complete_user_turn(), the user event
        will be gone and this is a harmless no-op.
        """
        sid = _get(data, "session_id") or self.latest_session_id
        if not sid:
            return AicebergResponse(ok=True, message="skipped:Stop:no_session")

        output = str(data.get("last_assistant_message", "")).strip()
        if not output:
            return AicebergResponse(ok=True, message="skipped:Stop:no_text")

        # Close any orphaned tool events, then close the user event.
        await self._close_session_tools(sid, INCOMPLETE_TOOL_MESSAGE)
        event = self.user_events.get(sid)
        if not event:
            return AicebergResponse(ok=True, message="skipped:Stop:already_closed")

        resp = await self.sender.close_event(event, output=output)
        if resp.ok:
            self.user_events.pop(sid, None)
        return resp

    async def handle_stop_failure(self, data: dict[str, Any]) -> AicebergResponse:
        """Close the user event when the turn ends due to an API error.

        StopFailure fires instead of Stop on rate limits, auth failures, etc.
        We close everything with the error info so events don't stay orphaned.
        """
        sid = _get(data, "session_id") or self.latest_session_id
        if not sid:
            return AicebergResponse(ok=True, message="skipped:StopFailure:no_session")

        error_type = _get(data, "error") or "unknown"
        error_details = _get(data, "error_details")
        last_msg = str(data.get("last_assistant_message", "")).strip()
        output = last_msg or f"API error: {error_type}"
        if error_details:
            output = f"{output} ({error_details})"

        await self._close_session_tools(sid, INCOMPLETE_TOOL_MESSAGE)
        event = self.user_events.get(sid)
        if not event:
            return AicebergResponse(ok=True, message="skipped:StopFailure:already_closed")

        resp = await self.sender.close_event(event, output=output)
        if resp.ok:
            self.user_events.pop(sid, None)
        return resp

    # ── Noop (hooks we register but don't act on yet) ────────────────────

    async def handle_noop(self, data: dict[str, Any]) -> AicebergResponse:
        hook = _get(data, "hook_event_name")
        sid = _get(data, "session_id")
        self.latest_session_id = sid or self.latest_session_id
        return AicebergResponse(ok=True, message=f"skipped:{hook or 'unknown'}")

    # ── Session close helpers ────────────────────────────────────────────

    async def complete_user_turn(
        self, session_id: str, output_text: str,
    ) -> None:
        """Close all events for a finished turn."""
        await self._close_session_tools(session_id, INCOMPLETE_TOOL_MESSAGE)
        if not output_text.strip():
            return
        event = self.user_events.get(session_id)
        if event:
            resp = await self.sender.close_event(event, output=output_text)
            if resp.ok:
                self.user_events.pop(session_id, None)

    async def fail_session(self, session_id: str, detail: str) -> None:
        """Close everything with a failure message."""
        if not session_id:
            return
        msg = f"{FAILURE_MESSAGE}\n\nDetails: {detail}"
        event = self.user_events.get(session_id)
        if event:
            resp = await self.sender.close_event(event, output=msg)
            if resp.ok:
                self.user_events.pop(session_id, None)
        await self._close_session_tools(session_id, msg)

    def report_unresolved_events(self) -> None:
        pending = [
            *self.user_events.values(),
            *self.tool_events.values(),
        ]
        if not pending:
            return
        print("[aiceberg] unresolved events remain open:")
        for e in pending:
            print(f"  - {e.label}: event_id={e.event_id} session={e.session_id}")

    def report_subagents(self) -> None:
        if not self.subagents:
            return
        print(f"[aiceberg] tracked {len(self.subagents)} subagent(s):")
        for r in self.subagents.values():
            status = "stopped" if r.stopped_at else "running"
            print(f"  - {r.agent_id} type={r.agent_type} status={status}")

    # ── Internal ─────────────────────────────────────────────────────────

    async def _close_session_tools(
        self, session_id: str, message: str,
    ) -> None:
        keys = [k for k, e in self.tool_events.items()
                if e.session_id == session_id]
        for k in keys:
            event = self.tool_events.get(k)
            if not event:
                continue
            resp = await self.sender.close_event(
                event,
                output={"hook_phase": "session_close", "tool_use_id": k,
                        "message": message},
            )
            if resp.ok:
                self.tool_events.pop(k, None)

    @staticmethod
    def _tool_meta(
        data: dict[str, Any], tool_name: str, tool_use_key: str,
    ) -> dict[str, Any]:
        extra: dict[str, str] = {
            "tool_name": tool_name, "tool_use_id": tool_use_key,
        }
        for f in ("agent_id", "agent_type"):
            val = _get(data, f)
            if val:
                extra[f] = val
        return _metadata(data, **extra)
