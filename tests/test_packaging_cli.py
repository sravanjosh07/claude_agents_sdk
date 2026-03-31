"""Tests for packaging, CLI init, and workspace resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_aiceberg.config import (
    ASYNC_HOOKS,
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    MANAGED_CLAUDE_CODE_HOOKS,
    WorkspacePaths,
    build_managed_hook_settings,
    merge_settings,
    resolve_workspace_root,
    workspace_paths,
    write_settings_file,
)
from claude_aiceberg.cli import build_init_parser, init_main


class PackagingTests(unittest.TestCase):

    def test_managed_hooks_include_subagent(self):
        self.assertIn("SubagentStart", MANAGED_CLAUDE_CODE_HOOKS)
        self.assertIn("SubagentStop", MANAGED_CLAUDE_CODE_HOOKS)

    def test_hook_settings_has_matchers(self):
        hooks = build_managed_hook_settings("/tmp/test")
        for name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            entries = hooks[name]
            self.assertTrue(any(e.get("matcher") == "*" for e in entries))

    def test_async_hooks_are_background(self):
        hooks = build_managed_hook_settings("/tmp/test")
        for name in ASYNC_HOOKS:
            entries = hooks[name]
            for group in entries:
                for h in group["hooks"]:
                    self.assertTrue(h.get("async"), f"{name} should be async")

    def test_blocking_hooks_are_sync(self):
        hooks = build_managed_hook_settings("/tmp/test")
        sync_hooks = {"UserPromptSubmit", "PreToolUse", "Stop"}
        for name in sync_hooks:
            entries = hooks[name]
            for group in entries:
                for h in group["hooks"]:
                    self.assertFalse(h.get("async"), f"{name} should be sync")

    def test_stop_failure_registered(self):
        self.assertIn("StopFailure", MANAGED_CLAUDE_CODE_HOOKS)

    def test_session_hooks_not_registered(self):
        """SessionStart/SessionEnd don't need CLI hooks (no-ops)."""
        self.assertNotIn("SessionStart", MANAGED_CLAUDE_CODE_HOOKS)
        self.assertNotIn("SessionEnd", MANAGED_CLAUDE_CODE_HOOKS)


class InitCommandTests(unittest.TestCase):

    def test_writes_settings_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_settings_file(workspace=tmpdir)
            self.assertTrue(paths.settings_path.is_file())
            data = json.loads(paths.settings_path.read_text())
            self.assertIn("hooks", data)

    def test_cli_parser(self):
        parser = build_init_parser()
        args = parser.parse_args(["--workspace", "/tmp", "--debug"])
        self.assertEqual(args.workspace, "/tmp")
        self.assertTrue(args.debug)


class WorkspaceResolutionTests(unittest.TestCase):

    def test_explicit_path(self):
        root = resolve_workspace_root("/tmp/myproject")
        self.assertEqual(root, Path("/tmp/myproject").resolve())

    def test_env_fallback(self):
        with patch.dict("os.environ",
                        {"CLAUDE_AICEBERG_WORKSPACE": "/tmp/envproject"}):
            root = resolve_workspace_root()
            self.assertEqual(root, Path("/tmp/envproject").resolve())

    def test_paths_structure(self):
        paths = workspace_paths("/tmp/myproject")
        self.assertIsInstance(paths, WorkspacePaths)
        self.assertEqual(paths.workspace_root, Path("/tmp/myproject").resolve())
        self.assertTrue(str(paths.state_db_path).endswith(".sqlite3"))

    def test_merge_preserves_existing(self):
        existing = {
            "custom_key": "value",
            "hooks": {"MyHook": [{"custom": True}]},
        }
        merged = merge_settings(existing, workspace="/tmp")
        self.assertEqual(merged["custom_key"], "value")
        self.assertIn("MyHook", merged["hooks"])
        self.assertIn("UserPromptSubmit", merged["hooks"])


class HttpModeTests(unittest.TestCase):
    """Tests for HTTP hook mode configuration."""

    def test_http_mode_uses_http_type(self):
        hooks = build_managed_hook_settings("/tmp/test", mode="http")
        for name in MANAGED_CLAUDE_CODE_HOOKS:
            entries = hooks[name]
            for group in entries:
                for h in group["hooks"]:
                    self.assertEqual(h["type"], "http", f"{name} should use http type")
                    self.assertIn("url", h, f"{name} should have url")
                    self.assertNotIn("command", h, f"{name} should not have command")

    def test_http_urls_contain_hook_name(self):
        hooks = build_managed_hook_settings("/tmp/test", mode="http")
        for name in MANAGED_CLAUDE_CODE_HOOKS:
            url = hooks[name][0]["hooks"][0]["url"]
            self.assertIn(f"/{name}", url, f"URL should contain /{name}")

    def test_http_mode_uses_configured_port(self):
        hooks = build_managed_hook_settings("/tmp/test", mode="http", http_port=9999)
        url = hooks["PreToolUse"][0]["hooks"][0]["url"]
        self.assertIn(":9999/", url)

    def test_http_mode_no_async_flag(self):
        """HTTP hooks don't need async flag — Claude handles it natively."""
        hooks = build_managed_hook_settings("/tmp/test", mode="http")
        for name in MANAGED_CLAUDE_CODE_HOOKS:
            for group in hooks[name]:
                for h in group["hooks"]:
                    self.assertNotIn("async", h, f"{name} http hook should not have async")

    def test_command_mode_still_default(self):
        hooks = build_managed_hook_settings("/tmp/test")
        h = hooks["PreToolUse"][0]["hooks"][0]
        self.assertEqual(h["type"], "command")

    def test_cli_parser_http_mode(self):
        parser = build_init_parser()
        args = parser.parse_args(["--mode", "http", "--http-port", "9999"])
        self.assertEqual(args.mode, "http")
        self.assertEqual(args.http_port, 9999)

    def test_write_settings_http_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_settings_file(workspace=tmpdir, mode="http")
            data = json.loads(paths.settings_path.read_text())
            h = data["hooks"]["PreToolUse"][0]["hooks"][0]
            self.assertEqual(h["type"], "http")
            self.assertIn("url", h)


if __name__ == "__main__":
    unittest.main()
