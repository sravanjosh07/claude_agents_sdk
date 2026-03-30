from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claude_aiceberg import (  # noqa: E402
    AicebergResponse,
    AicebergSender,
    ClaudeAicebergWorkflow,
    ClaudeHookCallbacks,
    SUPPORTED_HOOK_EVENTS,
    build_hook_registry,
)
from claude_aiceberg.workflow import AGENT_EVENT_TYPE, TOOL_EVENT_TYPE, classify_tool_event  # noqa: E402

from tests.helpers import FakeSender  # noqa: E402


class SenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_create_returns_event_id(self) -> None:
        sender = AicebergSender(dry_run=True, debug=False, min_event_gap_seconds=0)
        response, open_event = await sender.create_event(
            label="user prompt",
            event_type="user_agt",
            content="hello",
            session_id="s1",
            metadata={"hook_event_name": "UserPromptSubmit"},
            session_start=True,
        )
        self.assertTrue(response.ok)
        self.assertIsNotNone(response.event_id)
        self.assertIsNotNone(open_event)

    async def test_dry_run_close_reuses_linked_event_id(self) -> None:
        sender = AicebergSender(dry_run=True, debug=False, min_event_gap_seconds=0)
        _, open_event = await sender.create_event(
            label="user prompt",
            event_type="user_agt",
            content="hello",
            session_id="s1",
            metadata={"hook_event_name": "UserPromptSubmit"},
            session_start=True,
        )
        assert open_event is not None
        response = await sender.close_event(open_event, output="world")
        self.assertEqual(response.event_id, open_event.event_id)

    async def test_wait_gap_is_honored(self) -> None:
        sender = AicebergSender(dry_run=True, debug=False, min_event_gap_seconds=0.05)
        await sender.create_event(
            label="first",
            event_type="user_agt",
            content="one",
            session_id="s1",
            metadata={"hook_event_name": "UserPromptSubmit"},
        )
        start = time.monotonic()
        await sender.create_event(
            label="second",
            event_type="user_agt",
            content="two",
            session_id="s1",
            metadata={"hook_event_name": "UserPromptSubmit"},
        )
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.04)

    async def test_missing_config_is_harmless_in_dry_run(self) -> None:
        sender = AicebergSender(
            api_url="",
            api_key="",
            use_case_id="",
            dry_run=True,
            debug=False,
            min_event_gap_seconds=0,
        )
        response, open_event = await sender.create_event(
            label="user prompt",
            event_type="user_agt",
            content="hello",
            session_id="s1",
            metadata={"hook_event_name": "UserPromptSubmit"},
        )
        self.assertTrue(response.ok)
        self.assertIsNotNone(open_event)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sender = FakeSender()
        self.workflow = ClaudeAicebergWorkflow(sender=self.sender)

    async def test_blocked_prompt_does_not_leave_open_event(self) -> None:
        self.sender.create_responses = [
            AicebergResponse(ok=True, event_result="rejected", event_id="evt-user", message="blocked")
        ]
        response = await self.workflow.handle_user_prompt_submit(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "prompt": "bad prompt",
            }
        )
        self.assertTrue(response.blocked)
        self.assertEqual(self.workflow.user_events, {})
        self.assertEqual(len(self.workflow.blocked_events), 1)
        self.assertEqual(len(self.sender.closed), 0)

        await self.workflow.flush_blocked_events("s1")

        self.assertEqual(self.workflow.blocked_events, [])
        self.assertEqual(len(self.sender.closed), 1)
        self.assertEqual(
            self.sender.closed[0]["output"],
            "This request was blocked by Aiceberg safety policy.",
        )

    async def test_passed_prompt_stores_one_user_event(self) -> None:
        await self.workflow.handle_user_prompt_submit(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "prompt": "hello",
            }
        )
        self.assertIn("s1", self.workflow.user_events)

    async def test_pre_and_post_tool_use_close_matching_event(self) -> None:
        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "tool_use_id": "tool-1",
        }
        await self.workflow.handle_pre_tool_use(input_data, None)
        self.assertIn("tool-1", self.workflow.tool_events)

        await self.workflow.handle_post_tool_use(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "tool_name": "Read",
                "tool_input": {"file_path": "README.md"},
                "tool_response": "content",
                "tool_use_id": "tool-1",
            },
            None,
        )
        self.assertNotIn("tool-1", self.workflow.tool_events)
        self.assertEqual(len(self.sender.closed), 1)

    async def test_post_tool_use_failure_closes_matching_event(self) -> None:
        await self.workflow.handle_pre_tool_use(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                "tool_use_id": "tool-2",
            },
            None,
        )
        await self.workflow.handle_post_tool_use_failure(
            {
                "hook_event_name": "PostToolUseFailure",
                "session_id": "s1",
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                "tool_use_id": "tool-2",
                "error": "boom",
            },
            None,
        )
        self.assertNotIn("tool-2", self.workflow.tool_events)
        self.assertEqual(self.sender.closed[0]["output"]["hook_phase"], "post_tool_use_failure")

    async def test_blocked_tool_is_closed_immediately(self) -> None:
        self.sender.create_responses = [
            AicebergResponse(ok=True, event_result="rejected", event_id="evt-tool", message="blocked")
        ]
        response = await self.workflow.handle_pre_tool_use(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
                "tool_name": "WebSearch",
                "tool_input": {"query": "top fiercest raptors birds of prey"},
                "tool_use_id": "tool-blocked",
            },
            None,
        )
        self.assertTrue(response.blocked)
        self.assertNotIn("tool-blocked", self.workflow.tool_events)
        self.assertEqual(len(self.workflow.blocked_events), 1)
        self.assertEqual(len(self.sender.closed), 0)

        await self.workflow.flush_blocked_events("s1")

        self.assertEqual(self.workflow.blocked_events, [])
        self.assertEqual(len(self.sender.closed), 1)
        self.assertEqual(
            self.sender.closed[0]["output"],
            "This request was blocked by Aiceberg safety policy.",
        )

    async def test_fail_session_closes_pending_prompt_and_tool_events(self) -> None:
        await self.workflow.handle_user_prompt_submit(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "prompt": "hello",
            }
        )
        await self.workflow.handle_pre_tool_use(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
                "tool_name": "Read",
                "tool_input": {"file_path": "README.md"},
                "tool_use_id": "tool-3",
            },
            None,
        )
        await self.workflow.fail_session("s1", "runtime failure")
        self.assertEqual(len(self.sender.closed), 2)

    async def test_complete_user_turn_closes_orphan_tool_events(self) -> None:
        await self.workflow.handle_user_prompt_submit(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "prompt": "hello",
            }
        )
        await self.workflow.handle_pre_tool_use(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
                "tool_name": "WebSearch",
                "tool_input": {"query": "houston weather forecast this weekend"},
                "tool_use_id": "tool-4",
            },
            None,
        )

        await self.workflow.complete_user_turn("s1", "I do not have permission to use WebSearch.")

        self.assertNotIn("tool-4", self.workflow.tool_events)
        self.assertEqual(len(self.sender.closed), 2)
        self.assertEqual(self.sender.closed[0]["output"]["hook_phase"], "session_close")

    async def test_failed_close_leaves_event_visible_in_state(self) -> None:
        await self.workflow.handle_user_prompt_submit(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "prompt": "hello",
            }
        )
        self.sender.close_responses = [
            AicebergResponse(ok=False, event_result="passed", event_id="evt-1", message="backend failed")
        ]
        await self.workflow.complete_user_turn("s1", "answer")
        self.assertIn("s1", self.workflow.user_events)

    async def test_stop_hook_is_skipped_for_live_aiceberg_traffic(self) -> None:
        response = await self.workflow.handle_stop(
            {
                "hook_event_name": "Stop",
                "session_id": "s1",
                "stop_hook_active": False,
            }
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.message, "skipped_non_live_hook:Stop")
        self.assertEqual(self.sender.created, [])

    async def test_permission_request_is_skipped_for_live_aiceberg_traffic(self) -> None:
        response = await self.workflow.handle_permission_request(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "s1",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.message, "skipped_non_live_hook:PermissionRequest")
        self.assertEqual(self.sender.created, [])

    async def test_session_start_and_end_are_observation_only(self) -> None:
        start_response = await self.workflow.handle_session_start(
            {
                "hook_event_name": "SessionStart",
                "session_id": "s1",
            }
        )
        end_response = await self.workflow.handle_session_end(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "s1",
            }
        )
        self.assertEqual(start_response.message, "skipped_non_live_hook:SessionStart")
        self.assertEqual(end_response.message, "skipped_non_live_hook:SessionEnd")
        self.assertEqual(self.sender.created, [])


class ToolClassificationTests(unittest.TestCase):
    def test_agent_and_task_are_classified_as_agent_events(self) -> None:
        self.assertEqual(classify_tool_event("Agent"), AGENT_EVENT_TYPE)
        self.assertEqual(classify_tool_event("Task"), AGENT_EVENT_TYPE)

    def test_other_tools_are_classified_as_regular_tool_events(self) -> None:
        self.assertEqual(classify_tool_event("WebSearch"), TOOL_EVENT_TYPE)
        self.assertEqual(classify_tool_event("mcp__memory__create_entities"), TOOL_EVENT_TYPE)


class RegistryTests(unittest.TestCase):
    def test_registry_contains_all_supported_hooks(self) -> None:
        workflow = ClaudeAicebergWorkflow(sender=FakeSender())
        callbacks = ClaudeHookCallbacks(workflow)
        registry = build_hook_registry(callbacks)

        self.assertEqual(list(registry.keys()), list(SUPPORTED_HOOK_EVENTS))
        for matchers in registry.values():
            self.assertEqual(len(matchers), 1)
            self.assertEqual(len(matchers[0].hooks), 1)


if __name__ == "__main__":
    unittest.main()
