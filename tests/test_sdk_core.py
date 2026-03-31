"""Tests for the SDK path: sender, workflow, hooks, registry."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from claude_aiceberg.sender import AicebergResponse, AicebergSender, serialize_content
from claude_aiceberg.workflow import (
    ClaudeAicebergWorkflow,
    SubagentRecord,
    classify_tool_event,
)
from claude_aiceberg.hooks import (
    SUPPORTED_HOOK_EVENTS,
    build_block_output,
    build_hook_registry,
    dispatch_hook,
)

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from helpers import FakeSender


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Sender ───────────────────────────────────────────────────────────────────

class SenderTests(unittest.TestCase):

    def test_serialize_string(self):
        self.assertEqual(serialize_content("hello"), "hello")

    def test_serialize_dict(self):
        result = serialize_content({"b": 2, "a": 1})
        self.assertIn('"a": 1', result)

    def test_dry_run_explicit(self):
        s = AicebergSender(dry_run=True)
        self.assertTrue(s.dry_run)
        s2 = AicebergSender(dry_run=False)
        self.assertFalse(s2.dry_run)

    def test_create_close_dry_run(self):
        s = AicebergSender(dry_run=True, api_key="k", use_case_id="u")
        resp, event = run(s.create_event(
            label="test", event_type="user_agt",
            content="hi", session_id="s1",
        ))
        self.assertTrue(resp.ok)
        self.assertIsNotNone(event)
        close_resp = run(s.close_event(event, output="bye"))
        self.assertTrue(close_resp.ok)


# ── Workflow ─────────────────────────────────────────────────────────────────

class WorkflowTests(unittest.TestCase):

    def _wf(self):
        sender = FakeSender()
        wf = ClaudeAicebergWorkflow(sender)
        return wf, sender

    def test_prompt_opens_event(self):
        wf, sender = self._wf()
        resp = run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "hello",
            "hook_event_name": "UserPromptSubmit",
        }))
        self.assertTrue(resp.ok)
        self.assertIn("s1", wf.user_events)
        self.assertEqual(len(sender.created), 1)

    def test_empty_prompt_skips(self):
        wf, sender = self._wf()
        resp = run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "",
        }))
        self.assertEqual(resp.message, "skipped_missing_prompt")
        self.assertEqual(len(sender.created), 0)

    def test_tool_open_close(self):
        wf, _ = self._wf()
        resp = run(wf.handle_pre_tool_use(
            {"session_id": "s1", "tool_name": "Read",
             "hook_event_name": "PreToolUse"},
            tool_use_id="t1",
        ))
        self.assertTrue(resp.ok)
        self.assertIn("t1", wf.tool_events)

        run(wf.handle_post_tool_use(
            {"session_id": "s1", "tool_name": "Read",
             "tool_response": "content"},
            tool_use_id="t1",
        ))
        self.assertNotIn("t1", wf.tool_events)

    def test_tool_failure_close(self):
        wf, _ = self._wf()
        run(wf.handle_pre_tool_use(
            {"session_id": "s1", "tool_name": "Bash",
             "hook_event_name": "PreToolUse"},
            tool_use_id="t2",
        ))
        run(wf.handle_post_tool_use_failure(
            {"session_id": "s1", "tool_name": "Bash", "error": "boom"},
            tool_use_id="t2",
        ))
        self.assertNotIn("t2", wf.tool_events)

    def test_blocked_prompt(self):
        wf, sender = self._wf()
        sender.create_responses.append(
            AicebergResponse(ok=True, event_result="blocked", event_id="b1"),
        )
        resp = run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "bad",
            "hook_event_name": "UserPromptSubmit",
        }))
        self.assertTrue(resp.blocked)
        # Blocked events are closed immediately, not stored as open.
        self.assertNotIn("s1", wf.user_events)
        self.assertEqual(len(sender.closed), 1)

    def test_complete_turn(self):
        wf, _ = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "go",
            "hook_event_name": "UserPromptSubmit",
        }))
        run(wf.handle_pre_tool_use(
            {"session_id": "s1", "tool_name": "Read",
             "hook_event_name": "PreToolUse"},
            tool_use_id="t1",
        ))
        run(wf.complete_user_turn("s1", "done"))
        self.assertNotIn("s1", wf.user_events)
        self.assertNotIn("t1", wf.tool_events)

    def test_fail_session(self):
        wf, _ = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "go",
            "hook_event_name": "UserPromptSubmit",
        }))
        run(wf.fail_session("s1", "timeout"))
        self.assertNotIn("s1", wf.user_events)

    def test_noop(self):
        wf, _ = self._wf()
        resp = run(wf.handle_noop({
            "hook_event_name": "SessionStart", "session_id": "s1",
        }))
        self.assertIn("skipped", resp.message)

    def test_stop_closes_user_event(self):
        wf, sender = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "what is 2+2?",
            "hook_event_name": "UserPromptSubmit",
        }))
        self.assertIn("s1", wf.user_events)
        resp = run(wf.handle_stop({
            "session_id": "s1",
            "last_assistant_message": "4",
            "hook_event_name": "Stop",
        }))
        self.assertTrue(resp.ok)
        self.assertNotIn("s1", wf.user_events)
        # Verify close was called with the real assistant text.
        self.assertTrue(len(sender.closed) >= 1)
        self.assertEqual(sender.closed[-1]["output"], "4")

    def test_stop_without_text_skips(self):
        wf, _ = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "go",
            "hook_event_name": "UserPromptSubmit",
        }))
        resp = run(wf.handle_stop({
            "session_id": "s1", "hook_event_name": "Stop",
        }))
        self.assertIn("no_text", resp.message)
        # User event should still be open (runner will close via complete_user_turn).
        self.assertIn("s1", wf.user_events)

    def test_stop_failure_closes_user_event(self):
        wf, sender = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "go",
            "hook_event_name": "UserPromptSubmit",
        }))
        resp = run(wf.handle_stop_failure({
            "session_id": "s1",
            "error": "rate_limit",
            "error_details": "429 Too Many Requests",
            "hook_event_name": "StopFailure",
        }))
        self.assertTrue(resp.ok)
        self.assertNotIn("s1", wf.user_events)
        self.assertTrue(len(sender.closed) >= 1)
        self.assertIn("rate_limit", sender.closed[-1]["output"])

    def test_stop_failure_with_last_message(self):
        wf, sender = self._wf()
        run(wf.handle_user_prompt_submit({
            "session_id": "s1", "prompt": "go",
            "hook_event_name": "UserPromptSubmit",
        }))
        resp = run(wf.handle_stop_failure({
            "session_id": "s1",
            "error": "rate_limit",
            "last_assistant_message": "I was interrupted",
            "hook_event_name": "StopFailure",
        }))
        self.assertTrue(resp.ok)
        self.assertNotIn("s1", wf.user_events)
        # When last_assistant_message is present, use it as output.
        self.assertIn("I was interrupted", sender.closed[-1]["output"])


# ── Subagents ────────────────────────────────────────────────────────────────

class SubagentTests(unittest.TestCase):

    def _wf(self):
        return ClaudeAicebergWorkflow(FakeSender())

    def test_register(self):
        wf = self._wf()
        resp = run(wf.handle_subagent_start({
            "session_id": "s1", "agent_id": "a1", "agent_type": "inner",
        }))
        self.assertIn("registered", resp.message)
        self.assertEqual(len(wf.list_subagents()), 1)

    def test_stop(self):
        wf = self._wf()
        run(wf.handle_subagent_start({
            "session_id": "s1", "agent_id": "a1", "agent_type": "inner",
        }))
        run(wf.handle_subagent_stop({
            "session_id": "s1", "agent_id": "a1",
            "agent_transcript_path": "/tmp/t.json",
        }))
        rec = wf.subagents["a1"]
        self.assertIsNotNone(rec.stopped_at)
        self.assertEqual(rec.transcript_path, "/tmp/t.json")

    def test_missing_id_skips(self):
        wf = self._wf()
        resp = run(wf.handle_subagent_start({"session_id": "s1"}))
        self.assertIn("skipped", resp.message)

    def test_tool_in_subagent_metadata(self):
        wf = self._wf()
        run(wf.handle_subagent_start({
            "session_id": "s1", "agent_id": "a1", "agent_type": "inner",
        }))
        run(wf.handle_pre_tool_use(
            {"session_id": "s1", "tool_name": "Read",
             "agent_id": "a1", "agent_type": "inner",
             "hook_event_name": "PreToolUse"},
            tool_use_id="t1",
        ))
        event = wf.tool_events["t1"]
        self.assertEqual(event.metadata.get("agent_id"), "a1")


# ── Classification ───────────────────────────────────────────────────────────

class ClassificationTests(unittest.TestCase):

    def test_agent_tool(self):
        self.assertEqual(classify_tool_event("Agent"), "agt_agt")
        self.assertEqual(classify_tool_event("Task"), "agt_agt")

    def test_regular_tool(self):
        self.assertEqual(classify_tool_event("Read"), "agt_tool")


# ── Hooks / Registry ────────────────────────────────────────────────────────

class HooksTests(unittest.TestCase):

    def test_event_count(self):
        self.assertEqual(len(SUPPORTED_HOOK_EVENTS), 13)

    def test_block_output_tool(self):
        out = build_block_output("PreToolUse", "nope")
        self.assertEqual(out["decision"], "block")
        self.assertIn("hookSpecificOutput", out)

    def test_block_output_prompt(self):
        out = build_block_output("UserPromptSubmit", "nope")
        self.assertNotIn("hookSpecificOutput", out)

    def test_registry_builds(self):
        wf = ClaudeAicebergWorkflow(FakeSender())
        registry = build_hook_registry(wf)
        self.assertEqual(len(registry), len(SUPPORTED_HOOK_EVENTS))

    def test_dispatch_prompt(self):
        wf = ClaudeAicebergWorkflow(FakeSender())
        resp = run(dispatch_hook(wf, "UserPromptSubmit", {
            "session_id": "s1", "prompt": "hi",
            "hook_event_name": "UserPromptSubmit",
        }))
        self.assertTrue(resp.ok)

    def test_dispatch_noop(self):
        wf = ClaudeAicebergWorkflow(FakeSender())
        resp = run(dispatch_hook(wf, "SessionStart", {
            "session_id": "s1", "hook_event_name": "SessionStart",
        }))
        self.assertIn("skipped", resp.message)


if __name__ == "__main__":
    unittest.main()
