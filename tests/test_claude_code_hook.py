"""Tests for the Claude Code CLI hook handler."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from claude_aiceberg.sender import AicebergResponse, OpenAicebergEvent
import claude_code_aiceberg_hook as hook


class FakeCLISender:
    """Minimal sender for CLI hook tests."""

    def __init__(self) -> None:
        self.dry_run = True
        self.calls: list[dict[str, Any]] = []
        self.create_response = AicebergResponse(
            ok=True, event_id="e1", message="ok",
        )
        self.close_response = AicebergResponse(ok=True, message="ok")

    async def create_event(self, **kw: Any):
        self.calls.append({"action": "create", **kw})
        event = OpenAicebergEvent(
            event_id=self.create_response.event_id or "e1",
            event_type=kw.get("event_type", "user_agt"),
            session_id=kw.get("session_id", ""),
            input_text=str(kw.get("content", "")),
            metadata=kw.get("metadata", {}),
            label=kw.get("label", ""),
        )
        return self.create_response, event

    async def close_event(self, event, **kw: Any):
        self.calls.append({"action": "close", "event": event, **kw})
        return self.close_response


# ── State store ──────────────────────────────────────────────────────────────

class StateStoreTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite3"
        self.store = hook.ClaudeCodeStateStore(self.db_path)

    def tearDown(self):
        self.store.close()

    def test_event_roundtrip(self):
        event = OpenAicebergEvent(
            event_id="e1", event_type="user_agt", session_id="s1",
            input_text="hi", metadata={"k": "v"}, label="test",
        )
        self.store.store_event("user:s1", event)
        loaded = self.store.load_event("user:s1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.event_id, "e1")
        self.assertEqual(loaded.metadata, {"k": "v"})

    def test_event_delete(self):
        event = OpenAicebergEvent(
            event_id="e1", event_type="agt_tool", session_id="s1",
            input_text="x", metadata={}, label="t",
        )
        self.store.store_event("tool:t1", event)
        self.store.delete_event("tool:t1")
        self.assertIsNone(self.store.load_event("tool:t1"))

    def test_turn_roundtrip(self):
        self.store.store_turn("s1", "e1")
        self.assertEqual(self.store.load_turn_event_id("s1"), "e1")
        self.store.delete_turn("s1")
        self.assertIsNone(self.store.load_turn_event_id("s1"))

    def test_subagent_lifecycle(self):
        self.store.store_subagent("a1", "inner", "s1")
        self.store.stop_subagent("a1", "/tmp/t.json")
        row = self.store.conn.execute(
            "SELECT stopped_at, transcript_path FROM subagents "
            "WHERE agent_id = ?", ("a1",),
        ).fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], "/tmp/t.json")


# ── Dispatch ─────────────────────────────────────────────────────────────────

class DispatchTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite3"
        self.store = hook.ClaudeCodeStateStore(self.db_path)
        self.sender = FakeCLISender()

    def tearDown(self):
        self.store.close()

    def test_prompt_creates_event(self):
        result = hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "hello",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        self.assertIsNone(result)
        self.assertIsNotNone(self.store.load_event("user:s1"))

    def test_tool_open_close(self):
        hook.dispatch_claude_code_hook(
            "PreToolUse",
            {"session_id": "s1", "tool_name": "Read", "tool_use_id": "t1",
             "hook_event_name": "PreToolUse"},
            self.sender, self.store,
        )
        self.assertIsNotNone(self.store.load_event("tool:t1"))

        hook.dispatch_claude_code_hook(
            "PostToolUse",
            {"session_id": "s1", "tool_name": "Read", "tool_use_id": "t1",
             "tool_response": "ok", "hook_event_name": "PostToolUse"},
            self.sender, self.store,
        )
        self.assertIsNone(self.store.load_event("tool:t1"))

    def test_stop_closes_user(self):
        hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "go",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        hook.dispatch_claude_code_hook(
            "Stop",
            {"session_id": "s1",
             "last_assistant_message": "the answer is 42",
             "hook_event_name": "Stop"},
            self.sender, self.store,
        )
        self.assertIsNone(self.store.load_event("user:s1"))
        # Verify the close call used the real assistant response.
        close_calls = [c for c in self.sender.calls if c["action"] == "close"]
        self.assertTrue(len(close_calls) >= 1)
        self.assertEqual(close_calls[-1]["output"], "the answer is 42")

    def test_stop_without_assistant_message_uses_fallback(self):
        hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "go",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        hook.dispatch_claude_code_hook(
            "Stop",
            {"session_id": "s1", "hook_event_name": "Stop"},
            self.sender, self.store,
        )
        self.assertIsNone(self.store.load_event("user:s1"))
        close_calls = [c for c in self.sender.calls if c["action"] == "close"]
        self.assertTrue(len(close_calls) >= 1)
        self.assertEqual(close_calls[-1]["output"], "session ended")

    def test_stop_failure_closes_user(self):
        hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "go",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        hook.dispatch_claude_code_hook(
            "StopFailure",
            {"session_id": "s1", "error": "rate_limit",
             "error_details": "429 Too Many Requests",
             "hook_event_name": "StopFailure"},
            self.sender, self.store,
        )
        self.assertIsNone(self.store.load_event("user:s1"))
        close_calls = [c for c in self.sender.calls if c["action"] == "close"]
        self.assertTrue(len(close_calls) >= 1)
        self.assertIn("rate_limit", close_calls[-1]["output"])

    def test_stop_failure_prefers_last_assistant_message(self):
        hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "go",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        hook.dispatch_claude_code_hook(
            "StopFailure",
            {"session_id": "s1", "error": "rate_limit",
             "last_assistant_message": "API Error: Rate limit reached",
             "hook_event_name": "StopFailure"},
            self.sender, self.store,
        )
        self.assertIsNone(self.store.load_event("user:s1"))
        close_calls = [c for c in self.sender.calls if c["action"] == "close"]
        self.assertIn("API Error: Rate limit reached", close_calls[-1]["output"])

    def test_subagent_start_stop(self):
        hook.dispatch_claude_code_hook(
            "SubagentStart",
            {"session_id": "s1", "agent_id": "a1", "agent_type": "inner",
             "hook_event_name": "SubagentStart"},
            self.sender, self.store,
        )
        hook.dispatch_claude_code_hook(
            "SubagentStop",
            {"session_id": "s1", "agent_id": "a1",
             "agent_transcript_path": "/tmp/t.json",
             "hook_event_name": "SubagentStop"},
            self.sender, self.store,
        )
        row = self.store.conn.execute(
            "SELECT stopped_at FROM subagents WHERE agent_id = ?",
            ("a1",),
        ).fetchone()
        self.assertIsNotNone(row[0])

    def test_unknown_hook_skipped(self):
        result = hook.dispatch_claude_code_hook(
            "Notification",
            {"hook_event_name": "Notification"},
            self.sender, self.store,
        )
        self.assertIsNone(result)

    def test_blocked_returns_decision(self):
        self.sender.create_response = AicebergResponse(
            ok=True, event_result="blocked", event_id="b1", message="nope",
        )
        result = hook.dispatch_claude_code_hook(
            "UserPromptSubmit",
            {"session_id": "s1", "prompt": "bad",
             "hook_event_name": "UserPromptSubmit"},
            self.sender, self.store,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")
        # Blocked event should be closed immediately, not left open.
        self.assertIsNone(self.store.load_event("user:s1"))
        close_calls = [c for c in self.sender.calls if c["action"] == "close"]
        self.assertEqual(len(close_calls), 1)


if __name__ == "__main__":
    unittest.main()
