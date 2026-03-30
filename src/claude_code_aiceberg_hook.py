#!/usr/bin/env python3
"""
Project-local Claude Code hook entrypoint for Aiceberg monitoring.

This file keeps the Claude Code path readable in one place:
- parse hook input
- keep the tiny SQLite state needed across hook processes
- map one Claude conversation thread to one active Aiceberg turn session
- open/close Aiceberg prompt and tool events
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_aiceberg.hooks import build_block_output
from claude_aiceberg.sender import AicebergSender, OpenAicebergEvent
from claude_aiceberg.workflow import (
    DEFAULT_BLOCK_CLOSE_MESSAGE,
    INCOMPLETE_TOOL_MESSAGE,
    USER_EVENT_TYPE,
    build_hook_metadata,
    build_tool_payload,
    classify_tool_event,
)


STATE_DB_PATH = Path(__file__).resolve().parents[1] / ".claude" / "aiceberg_state.sqlite3"
HOOK_DEBUG_LOG_PATH = Path(__file__).resolve().parents[1] / ".claude" / "aiceberg_hook_debug.jsonl"


class ClaudeCodeStateStore:
    """Tiny SQLite store for the active turn and any open Aiceberg events."""

    def __init__(self, path: Path = STATE_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS open_events (
                    scope TEXT NOT NULL,
                    event_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    label TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS current_turns (
                    thread_id TEXT PRIMARY KEY,
                    turn_session_id TEXT NOT NULL
                )
                """
            )

    def save_event(self, scope: str, event_key: str, event: OpenAicebergEvent) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_events(
                    scope, event_key, session_id, event_id, event_type, input_text, metadata_json, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    event_key,
                    event.session_id,
                    event.event_id,
                    event.event_type,
                    event.input_text,
                    json.dumps(event.metadata, sort_keys=True),
                    event.label,
                ),
            )

    def get_event(self, scope: str, event_key: str) -> OpenAicebergEvent | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT event_id, event_type, session_id, input_text, metadata_json, label
                FROM open_events
                WHERE scope = ? AND event_key = ?
                """,
                (scope, event_key),
            ).fetchone()
        return self._row_to_event(row)

    def delete_event(self, scope: str, event_key: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM open_events WHERE scope = ? AND event_key = ?",
                (scope, event_key),
            )

    def list_tool_events(self, turn_session_id: str) -> list[tuple[str, OpenAicebergEvent]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT event_key, event_id, event_type, session_id, input_text, metadata_json, label
                FROM open_events
                WHERE scope = 'tool' AND session_id = ?
                """,
                (turn_session_id,),
            ).fetchall()
        events: list[tuple[str, OpenAicebergEvent]] = []
        for row in rows:
            event = self._row_to_event(row[1:])
            if event is not None:
                events.append((str(row[0]), event))
        return events

    def find_matching_tool_event(
        self,
        turn_session_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> tuple[str, OpenAicebergEvent] | None:
        target_fingerprint = fingerprint_tool_call(turn_session_id, tool_name, tool_input)
        for event_key, event in self.list_tool_events(turn_session_id):
            try:
                payload = json.loads(event.input_text)
            except json.JSONDecodeError:
                continue
            event_tool_name = str(payload.get("tool_name", "")).strip()
            event_tool_input = payload.get("tool_input", {}) or {}
            if event_tool_name != tool_name:
                continue
            if fingerprint_tool_call(turn_session_id, event_tool_name, event_tool_input) != target_fingerprint:
                continue
            return event_key, event
        return None

    def get_current_turn(self, thread_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT turn_session_id FROM current_turns WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def set_current_turn(self, thread_id: str, turn_session_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO current_turns(thread_id, turn_session_id) VALUES (?, ?)",
                (thread_id, turn_session_id),
            )

    def clear_current_turn(self, thread_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM current_turns WHERE thread_id = ?",
                (thread_id,),
            )

    @staticmethod
    def _row_to_event(row: tuple[Any, ...] | None) -> OpenAicebergEvent | None:
        if row is None:
            return None
        return OpenAicebergEvent(
            event_id=str(row[0]),
            event_type=str(row[1]),
            session_id=str(row[2]),
            input_text=str(row[3]),
            metadata=json.loads(str(row[4])),
            label=str(row[5]),
        )


def hook_debug_enabled() -> bool:
    return os.getenv("AICEBERG_HOOK_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def append_hook_debug(
    hook_name: str,
    input_data: dict[str, Any],
    *,
    note: str,
    session_id: str = "",
    turn_session_id: str = "",
    tool_name: str = "",
    tool_use_id: str = "",
    raw_tool_use_id: str = "",
) -> None:
    if not hook_debug_enabled():
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hook_name": hook_name,
        "note": note,
        "session_id": session_id,
        "turn_session_id": turn_session_id,
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "raw_tool_use_id": raw_tool_use_id,
        "keys": sorted(str(key) for key in input_data.keys()),
    }
    HOOK_DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HOOK_DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")


def extract_last_assistant_text(transcript_path: str) -> str:
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return ""

    last_text = ""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = entry.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        last_text = str(block["text"])
                        break
            elif isinstance(content, str) and content:
                last_text = content
    except OSError:
        return ""
    return last_text


def first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def extract_tool_name(input_data: dict[str, Any]) -> str:
    return first_non_empty_text(
        input_data.get("tool_name"),
        input_data.get("toolName"),
        input_data.get("tool"),
        input_data.get("name"),
    )


def extract_tool_use_id(input_data: dict[str, Any], tool_use_id: str | None = None) -> str:
    return first_non_empty_text(
        tool_use_id,
        input_data.get("tool_use_id"),
        input_data.get("toolUseId"),
        input_data.get("id"),
    )


def extract_tool_input(input_data: dict[str, Any]) -> Any:
    for key in ("tool_input", "toolInput", "input"):
        value = input_data.get(key)
        if value not in (None, "", []):
            return value
    return {}


def extract_tool_response(input_data: dict[str, Any]) -> Any:
    for key in ("tool_response", "toolResponse", "tool_result", "toolResult", "response", "result", "output"):
        if key in input_data:
            return input_data.get(key)
    return None


def extract_tool_error(input_data: dict[str, Any]) -> str:
    return first_non_empty_text(
        input_data.get("error"),
        input_data.get("message"),
        input_data.get("reason"),
    )


def fingerprint_tool_call(turn_session_id: str, tool_name: str, tool_input: Any) -> str:
    raw = json.dumps(
        {
            "turn_session_id": turn_session_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def synthetic_tool_key(turn_session_id: str, tool_name: str, tool_input: Any) -> str:
    return f"synthetic:{fingerprint_tool_call(turn_session_id, tool_name, tool_input)}"


def new_turn_session_id() -> str:
    return f"turn-{uuid.uuid4()}"


def thread_metadata(input_data: dict[str, Any], thread_id: str, **extra: str) -> dict[str, Any]:
    return build_hook_metadata(
        input_data,
        claude_session_id=thread_id,
        conversation_thread_id=thread_id,
        **extra,
    )


async def close_and_delete(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    *,
    scope: str,
    event_key: str,
    event: OpenAicebergEvent,
    output: str | dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    response = await sender.close_event(event, output=output, metadata=metadata)
    if response.ok:
        store.delete_event(scope, event_key)


async def close_turn(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    *,
    thread_id: str,
    turn_session_id: str,
    transcript_path: str = "",
    fallback_output: str = "Claude finished without a captured final assistant message.",
) -> None:
    for tool_key, event in store.list_tool_events(turn_session_id):
        await close_and_delete(
            sender,
            store,
            scope="tool",
            event_key=tool_key,
            event=event,
            output={
                "hook_phase": "session_close",
                "message": INCOMPLETE_TOOL_MESSAGE,
            },
        )

    user_event = store.get_event("user", turn_session_id)
    if user_event is not None:
        output_text = extract_last_assistant_text(transcript_path) if transcript_path else ""
        if not output_text:
            output_text = fallback_output
        await close_and_delete(
            sender,
            store,
            scope="user",
            event_key=turn_session_id,
            event=user_event,
            output=output_text,
        )

    if store.get_current_turn(thread_id) == turn_session_id:
        store.clear_current_turn(thread_id)


async def handle_user_prompt_submit(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    thread_id = str(input_data.get("session_id", "")).strip()
    prompt = str(input_data.get("prompt", "")).strip()
    append_hook_debug("UserPromptSubmit", input_data, note="received", session_id=thread_id)
    if not thread_id or not prompt:
        append_hook_debug("UserPromptSubmit", input_data, note="skipped_missing_prompt", session_id=thread_id)
        return {}

    existing_turn_id = store.get_current_turn(thread_id)
    turn_session_id = new_turn_session_id()
    store.set_current_turn(thread_id, turn_session_id)

    if existing_turn_id:
        await close_turn(
            sender,
            store,
            thread_id=thread_id,
            turn_session_id=existing_turn_id,
            transcript_path=str(input_data.get("transcript_path", "")),
            fallback_output="A new user turn started before the previous turn was explicitly finalized.",
        )

    response, open_event = await sender.create_event(
        label="user prompt",
        event_type=USER_EVENT_TYPE,
        content=prompt,
        session_id=turn_session_id,
        metadata=thread_metadata(input_data, thread_id),
        session_start=True,
    )
    if open_event is not None:
        if response.blocked:
            await sender.close_event(open_event, output=DEFAULT_BLOCK_CLOSE_MESSAGE)
            if store.get_current_turn(thread_id) == turn_session_id:
                store.clear_current_turn(thread_id)
        else:
            store.save_event("user", turn_session_id, open_event)
            append_hook_debug(
                "UserPromptSubmit",
                input_data,
                note="opened_turn_session",
                session_id=thread_id,
                turn_session_id=turn_session_id,
            )
    elif response.blocked:
        if store.get_current_turn(thread_id) == turn_session_id:
            store.clear_current_turn(thread_id)
    else:
        append_hook_debug(
            "UserPromptSubmit",
            input_data,
            note="turn_open_not_persisted",
            session_id=thread_id,
            turn_session_id=turn_session_id,
        )

    if response.blocked:
        return build_block_output(
            "UserPromptSubmit",
            response.message or "Prompt blocked by Aiceberg safety policy.",
        )
    return {}


async def handle_pre_tool_use(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
    tool_use_id: str | None,
) -> dict[str, Any]:
    thread_id = str(input_data.get("session_id", "")).strip()
    turn_session_id = store.get_current_turn(thread_id) or ""
    tool_name = extract_tool_name(input_data)
    tool_input = extract_tool_input(input_data)
    raw_tool_use_id = extract_tool_use_id(input_data, tool_use_id)
    tool_key = raw_tool_use_id
    if not tool_key and turn_session_id and tool_name:
        tool_key = synthetic_tool_key(turn_session_id, tool_name, tool_input)

    append_hook_debug(
        "PreToolUse",
        input_data,
        note="received",
        session_id=thread_id,
        turn_session_id=turn_session_id,
        tool_name=tool_name,
        tool_use_id=tool_key,
        raw_tool_use_id=raw_tool_use_id,
    )
    if not turn_session_id or not tool_name or not tool_key:
        append_hook_debug(
            "PreToolUse",
            input_data,
            note="skipped_missing_tool_context",
            session_id=thread_id,
            turn_session_id=turn_session_id,
            tool_name=tool_name,
            tool_use_id=tool_key,
            raw_tool_use_id=raw_tool_use_id,
        )
        return {}

    response, open_event = await sender.create_event(
        label=f"tool {tool_name}",
        event_type=classify_tool_event(tool_name),
        content=build_tool_payload(
            "pre_tool_use",
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=raw_tool_use_id,
        ),
        session_id=turn_session_id,
        metadata=thread_metadata(input_data, thread_id, tool_name=tool_name, tool_use_id=raw_tool_use_id),
    )
    if open_event is not None:
        if response.blocked:
            await sender.close_event(open_event, output=DEFAULT_BLOCK_CLOSE_MESSAGE)
        else:
            store.save_event("tool", tool_key, open_event)
            append_hook_debug(
                "PreToolUse",
                input_data,
                note="stored_open_tool_event",
                session_id=thread_id,
                turn_session_id=turn_session_id,
                tool_name=tool_name,
                tool_use_id=tool_key,
                raw_tool_use_id=raw_tool_use_id,
            )
    else:
        append_hook_debug(
            "PreToolUse",
            input_data,
            note="tool_open_not_persisted",
            session_id=thread_id,
            turn_session_id=turn_session_id,
            tool_name=tool_name,
            tool_use_id=tool_key,
            raw_tool_use_id=raw_tool_use_id,
        )

    if response.blocked:
        append_hook_debug(
            "PreToolUse",
            input_data,
            note="blocked",
            session_id=thread_id,
            turn_session_id=turn_session_id,
            tool_name=tool_name,
            tool_use_id=tool_key,
            raw_tool_use_id=raw_tool_use_id,
        )
        return build_block_output(
            "PreToolUse",
            response.message or f"{tool_name or 'tool'} blocked by Aiceberg safety policy.",
        )
    return {}


async def handle_post_tool_use(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
    tool_use_id: str | None,
) -> None:
    thread_id = str(input_data.get("session_id", "")).strip()
    turn_session_id = store.get_current_turn(thread_id) or ""
    tool_name = extract_tool_name(input_data)
    raw_tool_use_id = extract_tool_use_id(input_data, tool_use_id)
    tool_key = raw_tool_use_id
    tool_input = extract_tool_input(input_data)
    append_hook_debug(
        "PostToolUse",
        input_data,
        note="received",
        session_id=thread_id,
        turn_session_id=turn_session_id,
        tool_name=tool_name,
        tool_use_id=tool_key,
        raw_tool_use_id=raw_tool_use_id,
    )

    event_key = tool_key
    event = store.get_event("tool", tool_key) if tool_key else None
    if event is None and turn_session_id and tool_name:
        matched_event = store.find_matching_tool_event(turn_session_id, tool_name, tool_input)
        if matched_event is not None:
            event_key, event = matched_event
    if event is None:
        append_hook_debug(
            "PostToolUse",
            input_data,
            note="no_open_tool_event_found",
            session_id=thread_id,
            turn_session_id=turn_session_id,
            tool_name=tool_name,
            tool_use_id=tool_key,
            raw_tool_use_id=raw_tool_use_id,
        )
        return

    await close_and_delete(
        sender,
        store,
        scope="tool",
        event_key=event_key,
        event=event,
        output=build_tool_payload(
            "post_tool_use",
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=raw_tool_use_id,
            tool_response=extract_tool_response(input_data),
        ),
        metadata=thread_metadata(input_data, thread_id, tool_name=tool_name, tool_use_id=raw_tool_use_id),
    )
    append_hook_debug(
        "PostToolUse",
        input_data,
        note="closed_tool_event",
        session_id=thread_id,
        turn_session_id=turn_session_id,
        tool_name=tool_name,
        tool_use_id=event_key,
        raw_tool_use_id=raw_tool_use_id,
    )


async def handle_post_tool_use_failure(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
    tool_use_id: str | None,
) -> None:
    thread_id = str(input_data.get("session_id", "")).strip()
    turn_session_id = store.get_current_turn(thread_id) or ""
    tool_name = extract_tool_name(input_data)
    raw_tool_use_id = extract_tool_use_id(input_data, tool_use_id)
    tool_key = raw_tool_use_id
    tool_input = extract_tool_input(input_data)
    append_hook_debug(
        "PostToolUseFailure",
        input_data,
        note="received",
        session_id=thread_id,
        turn_session_id=turn_session_id,
        tool_name=tool_name,
        tool_use_id=tool_key,
        raw_tool_use_id=raw_tool_use_id,
    )

    event_key = tool_key
    event = store.get_event("tool", tool_key) if tool_key else None
    if event is None and turn_session_id and tool_name:
        matched_event = store.find_matching_tool_event(turn_session_id, tool_name, tool_input)
        if matched_event is not None:
            event_key, event = matched_event
    if event is None:
        append_hook_debug(
            "PostToolUseFailure",
            input_data,
            note="no_open_tool_event_found",
            session_id=thread_id,
            turn_session_id=turn_session_id,
            tool_name=tool_name,
            tool_use_id=tool_key,
            raw_tool_use_id=raw_tool_use_id,
        )
        return

    await close_and_delete(
        sender,
        store,
        scope="tool",
        event_key=event_key,
        event=event,
        output=build_tool_payload(
            "post_tool_use_failure",
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=raw_tool_use_id,
            error=extract_tool_error(input_data),
        ),
        metadata=thread_metadata(input_data, thread_id, tool_name=tool_name, tool_use_id=raw_tool_use_id),
    )
    append_hook_debug(
        "PostToolUseFailure",
        input_data,
        note="closed_tool_event",
        session_id=thread_id,
        turn_session_id=turn_session_id,
        tool_name=tool_name,
        tool_use_id=event_key,
        raw_tool_use_id=raw_tool_use_id,
    )


async def handle_stop(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
) -> None:
    if input_data.get("stop_hook_active"):
        return

    thread_id = str(input_data.get("session_id", "")).strip()
    turn_session_id = store.get_current_turn(thread_id) or ""
    append_hook_debug("Stop", input_data, note="received", session_id=thread_id, turn_session_id=turn_session_id)
    if not thread_id or not turn_session_id:
        return

    await close_turn(
        sender,
        store,
        thread_id=thread_id,
        turn_session_id=turn_session_id,
        transcript_path=str(input_data.get("transcript_path", "")),
        fallback_output="Claude finished without a captured final assistant message.",
    )


async def handle_session_start(input_data: dict[str, Any]) -> None:
    thread_id = str(input_data.get("session_id", "")).strip()
    append_hook_debug("SessionStart", input_data, note="observed", session_id=thread_id)


async def handle_session_end(input_data: dict[str, Any]) -> None:
    thread_id = str(input_data.get("session_id", "")).strip()
    append_hook_debug("SessionEnd", input_data, note="observed", session_id=thread_id)


async def handle_subagent_stop(input_data: dict[str, Any]) -> None:
    thread_id = str(input_data.get("session_id", "")).strip()
    append_hook_debug("SubagentStop", input_data, note="observed", session_id=thread_id)


async def dispatch_claude_code_hook(
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    hook_name = str(input_data.get("hook_event_name", "")).strip()
    tool_use_id = extract_tool_use_id(input_data) or None

    if hook_name == "SessionStart":
        await handle_session_start(input_data)
        return {}
    if hook_name == "UserPromptSubmit":
        return await handle_user_prompt_submit(sender, store, input_data)
    if hook_name == "PreToolUse":
        return await handle_pre_tool_use(sender, store, input_data, tool_use_id)
    if hook_name == "PostToolUse":
        await handle_post_tool_use(sender, store, input_data, tool_use_id)
        return {}
    if hook_name == "PostToolUseFailure":
        await handle_post_tool_use_failure(sender, store, input_data, tool_use_id)
        return {}
    if hook_name == "Stop":
        await handle_stop(sender, store, input_data)
        return {}
    if hook_name == "SessionEnd":
        await handle_session_end(input_data)
        return {}
    if hook_name == "SubagentStop":
        await handle_subagent_stop(input_data)
        return {}
    return {}


async def main() -> int:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("[claude-code-aiceberg] expected JSON hook input on stdin", file=sys.stderr)
        return 1

    sender = AicebergSender(debug=False)
    store = ClaudeCodeStateStore()
    output = await dispatch_claude_code_hook(sender, store, input_data)
    if output:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
