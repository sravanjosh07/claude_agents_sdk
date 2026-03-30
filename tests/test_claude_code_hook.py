from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claude_code_aiceberg_hook import (  # noqa: E402
    ClaudeCodeStateStore,
    dispatch_claude_code_hook,
    extract_tool_input,
    extract_tool_name,
    extract_tool_response,
    extract_tool_use_id,
    handle_post_tool_use,
    handle_pre_tool_use,
    handle_stop,
    handle_user_prompt_submit,
    synthetic_tool_key,
)
from claude_aiceberg.sender import OpenAicebergEvent  # noqa: E402

from tests.helpers import FakeSender  # noqa: E402


class ClaudeCodeHookTests(unittest.TestCase):
    def test_tool_extractors_accept_alternate_cli_keys(self) -> None:
        payload = {
            "toolName": "WebSearch",
            "toolUseId": "tool-123",
            "input": {"query": "oklahoma weather weekend"},
            "result": {"answer": "sunny"},
        }
        self.assertEqual(extract_tool_name(payload), "WebSearch")
        self.assertEqual(extract_tool_use_id(payload), "tool-123")
        self.assertEqual(extract_tool_input(payload), {"query": "oklahoma weather weekend"})
        self.assertEqual(extract_tool_response(payload), {"answer": "sunny"})

    def test_tool_extractors_prefer_explicit_tool_fields(self) -> None:
        payload = {
            "tool_name": "Read",
            "tool_use_id": "tool-456",
            "tool_input": {"file_path": "README.md"},
            "tool_response": "file contents",
            "input": {"ignored": True},
        }
        self.assertEqual(extract_tool_name(payload), "Read")
        self.assertEqual(extract_tool_use_id(payload), "tool-456")
        self.assertEqual(extract_tool_input(payload), {"file_path": "README.md"})
        self.assertEqual(extract_tool_response(payload), "file contents")

    def test_synthetic_tool_key_is_stable_for_same_call(self) -> None:
        key_one = synthetic_tool_key("turn-1", "WebSearch", {"query": "oklahoma weather"})
        key_two = synthetic_tool_key("turn-1", "WebSearch", {"query": "oklahoma weather"})
        self.assertEqual(key_one, key_two)

    def test_store_can_match_tool_event_without_tool_use_id(self) -> None:
        temp_db = PROJECT_ROOT / ".claude" / "test_aiceberg_state.sqlite3"
        if temp_db.exists():
            temp_db.unlink()
        store = ClaudeCodeStateStore(path=temp_db)
        try:
            event = OpenAicebergEvent(
                event_id="evt-1",
                event_type="agt_tool",
                session_id="turn-1",
                input_text='{"hook_phase":"pre_tool_use","tool_input":{"query":"oklahoma weather"},"tool_name":"WebSearch"}',
                metadata={"tool_name": "WebSearch"},
                label="tool WebSearch",
            )
            store.save_event("tool", "synthetic:abc", event)
            matched = store.find_matching_tool_event("turn-1", "WebSearch", {"query": "oklahoma weather"})
            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertEqual(matched[0], "synthetic:abc")
        finally:
            if temp_db.exists():
                temp_db.unlink()


class ClaudeCodeTurnSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_prompt_creates_new_turn_session_and_maps_thread(self) -> None:
        temp_db = PROJECT_ROOT / ".claude" / "test_turn_state.sqlite3"
        if temp_db.exists():
            temp_db.unlink()
        store = ClaudeCodeStateStore(path=temp_db)
        sender = FakeSender()
        try:
            output = await handle_user_prompt_submit(
                sender,
                store,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-thread-1",
                    "prompt": "hello",
                },
            )
            self.assertEqual(output, {})
            self.assertEqual(len(sender.created), 1)
            created = sender.created[0]
            turn_session_id = str(created["session_id"])
            self.assertNotEqual(turn_session_id, "claude-thread-1")
            self.assertTrue(turn_session_id.startswith("turn-"))
            self.assertEqual(created["metadata"]["conversation_thread_id"], "claude-thread-1")
            self.assertEqual(store.get_current_turn("claude-thread-1"), turn_session_id)
        finally:
            if temp_db.exists():
                temp_db.unlink()

    async def test_session_and_subagent_hooks_are_observation_only(self) -> None:
        temp_db = PROJECT_ROOT / ".claude" / "test_observation_only.sqlite3"
        if temp_db.exists():
            temp_db.unlink()
        store = ClaudeCodeStateStore(path=temp_db)
        sender = FakeSender()
        try:
            for hook_name in ("SessionStart", "SessionEnd", "SubagentStop"):
                output = await dispatch_claude_code_hook(
                    sender,
                    store,
                    {
                        "hook_event_name": hook_name,
                        "session_id": "claude-thread-4",
                    },
                )
                self.assertEqual(output, {})
            self.assertEqual(sender.created, [])
            self.assertEqual(sender.closed, [])
        finally:
            if temp_db.exists():
                temp_db.unlink()

    async def test_tools_and_stop_use_active_turn_session(self) -> None:
        temp_db = PROJECT_ROOT / ".claude" / "test_turn_tool_state.sqlite3"
        if temp_db.exists():
            temp_db.unlink()
        store = ClaudeCodeStateStore(path=temp_db)
        sender = FakeSender()
        try:
            await handle_user_prompt_submit(
                sender,
                store,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-thread-2",
                    "prompt": "weather",
                },
            )
            turn_session_id = store.get_current_turn("claude-thread-2")
            assert turn_session_id is not None

            await handle_pre_tool_use(
                sender,
                store,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "claude-thread-2",
                    "tool_name": "WebSearch",
                    "tool_input": {"query": "weather"},
                },
                None,
            )
            self.assertEqual(len(sender.created), 2)
            self.assertEqual(sender.created[1]["session_id"], turn_session_id)
            self.assertEqual(sender.created[1]["metadata"]["conversation_thread_id"], "claude-thread-2")
            self.assertNotIn("tool_use_id", sender.created[1]["content"])

            await handle_post_tool_use(
                sender,
                store,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "claude-thread-2",
                    "tool_name": "WebSearch",
                    "tool_input": {"query": "weather"},
                    "tool_response": {"answer": "sunny"},
                },
                None,
            )
            self.assertNotIn("tool_use_id", sender.closed[0]["output"])

            await handle_stop(
                sender,
                store,
                {
                    "hook_event_name": "Stop",
                    "session_id": "claude-thread-2",
                    "transcript_path": "",
                },
            )
            self.assertIsNone(store.get_current_turn("claude-thread-2"))
            self.assertEqual(len(sender.closed), 2)
            self.assertEqual(sender.closed[1]["event"].session_id, turn_session_id)

        finally:
            if temp_db.exists():
                temp_db.unlink()

    async def test_new_prompt_closes_previous_turn_before_opening_next(self) -> None:
        temp_db = PROJECT_ROOT / ".claude" / "test_turn_rollover.sqlite3"
        if temp_db.exists():
            temp_db.unlink()
        store = ClaudeCodeStateStore(path=temp_db)
        sender = FakeSender()
        try:
            await handle_user_prompt_submit(
                sender,
                store,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-thread-3",
                    "prompt": "first",
                    "transcript_path": "",
                },
            )
            first_turn = store.get_current_turn("claude-thread-3")
            assert first_turn is not None

            await handle_user_prompt_submit(
                sender,
                store,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-thread-3",
                    "prompt": "second",
                    "transcript_path": "",
                },
            )
            second_turn = store.get_current_turn("claude-thread-3")
            assert second_turn is not None

            self.assertNotEqual(first_turn, second_turn)
            self.assertEqual(len(sender.closed), 1)
            self.assertEqual(sender.closed[0]["event"].session_id, first_turn)
        finally:
            if temp_db.exists():
                temp_db.unlink()


if __name__ == "__main__":
    unittest.main()
