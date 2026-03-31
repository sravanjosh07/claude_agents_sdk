#!/usr/bin/env python3
"""
Claude Code CLI hook handler.

Claude Code runs this as a separate process for each hook event.
Input arrives as JSON on stdin; output goes to stdout.
State is persisted in SQLite so it survives across process invocations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_aiceberg.config import (
    AICEBERG_HOOK_DEBUG_ENV,
    env_flag,
    workspace_paths,
)
from claude_aiceberg.sender import (
    AicebergResponse,
    AicebergSender,
    OpenAicebergEvent,
    serialize_content,
)
from claude_aiceberg.workflow import (
    BLOCK_MESSAGE,
    INCOMPLETE_TOOL_MESSAGE,
    USER_EVENT_TYPE,
    classify_tool_event,
)

# ── Debug log ────────────────────────────────────────────────────────────────

_debug_log_path: Path | None = None
_debug_enabled: bool = False


def init_debug(workspace: str | None = None) -> None:
    global _debug_log_path, _debug_enabled
    paths = workspace_paths(workspace)
    _debug_log_path = paths.debug_log_path
    _debug_enabled = env_flag(AICEBERG_HOOK_DEBUG_ENV, False)


def debug(hook: str, phase: str, **extra: Any) -> None:
    """Append one JSONL line to the debug log."""
    if not _debug_enabled or not _debug_log_path:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": hook,
        "phase": phase,
        **{k: v for k, v in extra.items() if v is not None},
    }
    try:
        _debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_debug_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        pass


# ── SQLite state store ───────────────────────────────────────────────────────

class ClaudeCodeStateStore:
    """Cross-process state for open Aiceberg events, turns, and subagents."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS open_events (
            key TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            session_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            label TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS current_turns (
            session_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            started_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subagents (
            agent_id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL DEFAULT '',
            parent_session_id TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            stopped_at TEXT,
            transcript_path TEXT
        );
    """

    def __init__(self, db_path: str | Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), timeout=5)
        self.conn.executescript(self._SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ── open_events ──

    def store_event(self, key: str, event: OpenAicebergEvent) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO open_events VALUES (?,?,?,?,?,?,?)",
            (key, event.event_id, event.event_type, event.session_id,
             event.input_text, json.dumps(event.metadata), event.label),
        )
        self.conn.commit()

    def load_event(self, key: str) -> OpenAicebergEvent | None:
        row = self.conn.execute(
            "SELECT event_id, event_type, session_id, input_text, metadata, label "
            "FROM open_events WHERE key = ?", (key,),
        ).fetchone()
        if not row:
            return None
        return OpenAicebergEvent(
            event_id=row[0], event_type=row[1], session_id=row[2],
            input_text=row[3], metadata=json.loads(row[4]), label=row[5],
        )

    def delete_event(self, key: str) -> None:
        self.conn.execute("DELETE FROM open_events WHERE key = ?", (key,))
        self.conn.commit()

    def all_events(self) -> list[tuple[str, OpenAicebergEvent]]:
        rows = self.conn.execute(
            "SELECT key, event_id, event_type, session_id, input_text, metadata, label "
            "FROM open_events",
        ).fetchall()
        return [
            (r[0], OpenAicebergEvent(
                event_id=r[1], event_type=r[2], session_id=r[3],
                input_text=r[4], metadata=json.loads(r[5]), label=r[6]))
            for r in rows
        ]

    # ── current_turns ──

    def store_turn(self, session_id: str, event_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO current_turns VALUES (?,?,?)",
            (session_id, event_id, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def load_turn_event_id(self, session_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT event_id FROM current_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else None

    def delete_turn(self, session_id: str) -> None:
        self.conn.execute(
            "DELETE FROM current_turns WHERE session_id = ?", (session_id,),
        )
        self.conn.commit()

    # ── subagents ──

    def store_subagent(
        self, agent_id: str, agent_type: str, parent_session_id: str,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO subagents VALUES (?,?,?,?,?,?)",
            (agent_id, agent_type, parent_session_id,
             datetime.now(timezone.utc).isoformat(), None, None),
        )
        self.conn.commit()

    def stop_subagent(
        self, agent_id: str, transcript_path: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE subagents SET stopped_at = ?, transcript_path = ? "
            "WHERE agent_id = ?",
            (datetime.now(timezone.utc).isoformat(), transcript_path, agent_id),
        )
        self.conn.commit()


# ── Extraction helpers ───────────────────────────────────────────────────────

def _get(data: dict[str, Any], *keys: str) -> str:
    """Try multiple key names, return first non-empty string."""
    for k in keys:
        val = str(data.get(k, "")).strip()
        if val:
            return val
    return ""


def _tool_key(data: dict[str, Any]) -> str:
    """Stable key for matching a tool open event to its close event."""
    tid = _get(data, "tool_use_id")
    if tid:
        return f"tool:{tid}"
    name = _get(data, "tool_name")
    raw_input = data.get("tool_input", data.get("input", {}))
    digest = hashlib.sha256(
        json.dumps(raw_input, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()[:12]
    return f"tool:{name}:{digest}"


def _metadata(data: dict[str, Any], **extra: str) -> dict[str, Any]:
    md: dict[str, Any] = {
        "hook_event_name": _get(data, "hook_event_name"),
        "user_id": os.getenv("AICEBERG_USER_ID", "claudeagent").strip() or "claudeagent",
    }
    for k, v in extra.items():
        text = str(v).strip()
        if text:
            md[k] = text
    return md


def _extract_last_assistant_text(data: dict[str, Any]) -> str:
    """Pull the assistant's final response from hook data.

    Claude Code passes `last_assistant_message` in the Stop hook payload.
    """
    return str(data.get("last_assistant_message", "")).strip()


# ── Hook handlers ────────────────────────────────────────────────────────────

def _sync(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _block_result(hook: str, response: AicebergResponse) -> dict[str, Any]:
    reason = response.message or BLOCK_MESSAGE
    result: dict[str, Any] = {"decision": "block", "reason": reason}
    if hook in ("PreToolUse", "PermissionRequest"):
        result["hookSpecificOutput"] = {"suppressToolOutput": True}
    return result


def handle_user_prompt_submit(
    data: dict[str, Any], sender: AicebergSender, store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    sid = _get(data, "session_id")
    prompt = _get(data, "prompt", "user_prompt")
    if not sid or not prompt:
        return None

    response, event = _sync(sender.create_event(
        label="user prompt",
        event_type=USER_EVENT_TYPE,
        content=prompt,
        session_id=sid,
        metadata=_metadata(data),
        session_start=True,
    ))
    debug("UserPromptSubmit", "sent", sid=sid, event_id=response.event_id)

    if response.blocked and event:
        _sync(sender.close_event(event, output=BLOCK_MESSAGE))
        debug("UserPromptSubmit", "closed_blocked", sid=sid)
        return _block_result("UserPromptSubmit", response)
    if event:
        store.store_event(f"user:{sid}", event)
        store.store_turn(sid, event.event_id)
    return None


def handle_pre_tool_use(
    data: dict[str, Any], sender: AicebergSender, store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    sid = _get(data, "session_id")
    name = _get(data, "tool_name")
    key = _tool_key(data)
    if not sid or not name:
        return None

    tool_input = data.get("tool_input", data.get("input", {}))
    payload = {"hook_phase": "pre_tool_use", "tool_name": name, "tool_input": tool_input}

    response, event = _sync(sender.create_event(
        label=f"tool {name}",
        event_type=classify_tool_event(name),
        content=payload,
        session_id=sid,
        metadata=_metadata(data, tool_name=name),
    ))
    debug("PreToolUse", "sent", sid=sid, tool=name, event_id=response.event_id)

    if response.blocked and event:
        _sync(sender.close_event(event, output=BLOCK_MESSAGE))
        debug("PreToolUse", "closed_blocked", sid=sid, tool=name)
        return _block_result("PreToolUse", response)
    if event:
        store.store_event(key, event)
    return None


def handle_post_tool(
    data: dict[str, Any],
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    *,
    is_failure: bool = False,
) -> dict[str, Any] | None:
    """Handles both PostToolUse and PostToolUseFailure."""
    key = _tool_key(data)
    event = store.load_event(key)
    if not event:
        debug("PostTool", "no_open_event", key=key)
        return None

    name = _get(data, "tool_name")
    phase = "post_tool_use_failure" if is_failure else "post_tool_use"
    payload: dict[str, Any] = {
        "hook_phase": phase, "tool_name": name,
        "tool_input": data.get("tool_input", data.get("input", {})),
    }
    if is_failure:
        payload["error"] = str(data.get("error", ""))
    else:
        payload["tool_response"] = data.get(
            "tool_response", data.get("response", data.get("output")),
        )

    response = _sync(sender.close_event(event, output=payload))
    debug(phase, "closed", key=key, ok=response.ok)
    if response.ok:
        store.delete_event(key)
    return None


def _close_session(
    sid: str,
    output: str,
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
    *,
    hook_label: str = "Stop",
) -> None:
    """Close orphaned tool events and the user prompt for a session."""
    for key, evt in store.all_events():
        if key.startswith("tool:") and evt.session_id == sid:
            resp = _sync(sender.close_event(
                evt, output={"hook_phase": "session_close",
                             "message": INCOMPLETE_TOOL_MESSAGE},
            ))
            if resp.ok:
                store.delete_event(key)

    user_event = store.load_event(f"user:{sid}")
    if user_event:
        resp = _sync(sender.close_event(user_event, output=output))
        debug(hook_label, "closed_user", sid=sid, ok=resp.ok)
        if resp.ok:
            store.delete_event(f"user:{sid}")
            store.delete_turn(sid)


def handle_stop(
    data: dict[str, Any], sender: AicebergSender, store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    """Close the user-turn event with the assistant's final response."""
    sid = _get(data, "session_id")
    if not sid:
        return None

    output = _extract_last_assistant_text(data) or "session ended"
    debug("Stop", "payload", sid=sid, keys=sorted(data.keys()),
          has_last_assistant_message="last_assistant_message" in data,
          output_preview=output[:200])

    _close_session(sid, output, sender, store, hook_label="Stop")
    return None


def handle_stop_failure(
    data: dict[str, Any], sender: AicebergSender, store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    """Close the user-turn event when the turn ended due to an API error.

    StopFailure fires instead of Stop on rate limits, auth failures, etc.
    We close everything with the error info so events don't stay orphaned.
    """
    sid = _get(data, "session_id")
    if not sid:
        return None

    error_type = _get(data, "error") or "unknown"
    error_details = _get(data, "error_details")
    last_msg = _extract_last_assistant_text(data)
    output = last_msg or f"API error: {error_type}"
    if error_details:
        output = f"{output} ({error_details})"

    debug("StopFailure", "payload", sid=sid, error=error_type,
          error_details=error_details or None)

    _close_session(sid, output, sender, store, hook_label="StopFailure")
    return None


def handle_subagent_start(
    data: dict[str, Any], store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    agent_id = _get(data, "agent_id")
    if not agent_id:
        return None
    store.store_subagent(
        agent_id, _get(data, "agent_type"), _get(data, "session_id"),
    )
    debug("SubagentStart", "registered", agent_id=agent_id)
    return None


def handle_subagent_stop(
    data: dict[str, Any], store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    agent_id = _get(data, "agent_id")
    if not agent_id:
        return None
    store.stop_subagent(agent_id, _get(data, "agent_transcript_path") or None)
    debug("SubagentStop", "stopped", agent_id=agent_id)
    return None


# ── Dispatch ─────────────────────────────────────────────────────────────────

def dispatch_claude_code_hook(
    hook_name: str,
    data: dict[str, Any],
    sender: AicebergSender,
    store: ClaudeCodeStateStore,
) -> dict[str, Any] | None:
    if hook_name == "UserPromptSubmit":
        return handle_user_prompt_submit(data, sender, store)
    if hook_name == "PreToolUse":
        return handle_pre_tool_use(data, sender, store)
    if hook_name == "PostToolUse":
        return handle_post_tool(data, sender, store, is_failure=False)
    if hook_name == "PostToolUseFailure":
        return handle_post_tool(data, sender, store, is_failure=True)
    if hook_name == "Stop":
        return handle_stop(data, sender, store)
    if hook_name == "StopFailure":
        return handle_stop_failure(data, sender, store)
    if hook_name == "SubagentStart":
        return handle_subagent_start(data, store)
    if hook_name == "SubagentStop":
        return handle_subagent_stop(data, store)
    debug(hook_name, "skipped")
    return None


# ── CLI entrypoint ───────────────────────────────────────────────────────────

def run_cli(
    hook_name: str, input_data: dict[str, Any], workspace: str | None = None,
) -> dict[str, Any] | None:
    init_debug(workspace)
    paths = workspace_paths(workspace)
    sender = AicebergSender()
    store = ClaudeCodeStateStore(paths.state_db_path)
    try:
        input_data["hook_event_name"] = hook_name
        return dispatch_claude_code_hook(hook_name, input_data, sender, store)
    finally:
        store.close()


def cli_main() -> None:
    """Entrypoint for claude-aiceberg-hook console script."""
    import argparse

    parser = argparse.ArgumentParser(prog="claude-aiceberg-hook")
    parser.add_argument("hook_name", nargs="?")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        os.environ[AICEBERG_HOOK_DEBUG_ENV] = "1"

    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"error": "invalid JSON on stdin"}))
        return

    hook_name = args.hook_name or input_data.get("hook_event_name", "")
    if not hook_name:
        return

    result = run_cli(hook_name, input_data, workspace=args.workspace)
    if result is not None:
        print(json.dumps(result, ensure_ascii=True))
