#!/usr/bin/env python3
"""
Claude Code HTTP hook server for Aiceberg.

A lightweight HTTP server that replaces the subprocess-per-event CLI hook.
Claude Code posts hook events directly here via `type: "http"` hooks.

Usage:
    claude-aiceberg-server --workspace . --port 8932
    claude-aiceberg-server --workspace . --port 8932 --debug

The server reuses the same dispatch logic as the CLI hook but avoids
~200-500ms Python startup overhead per event.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from claude_code_aiceberg_hook import (
    ClaudeCodeStateStore,
    dispatch_claude_code_hook,
    init_debug,
    debug,
)
from claude_aiceberg.config import (
    AICEBERG_HOOK_DEBUG_ENV,
    workspace_paths,
)
from claude_aiceberg.sender import AicebergSender

DEFAULT_PORT = 8932


class AicebergHookHandler(BaseHTTPRequestHandler):
    """Handles POST /<HookEventName> from Claude Code HTTP hooks."""

    sender: AicebergSender
    store: ClaudeCodeStateStore

    def do_POST(self) -> None:  # noqa: N802
        # Hook name from URL path: /PreToolUse -> PreToolUse
        hook_name = self.path.strip("/").split("/")[0].split("?")[0]
        if not hook_name:
            self._respond(400, {"error": "missing hook name in URL path"})
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._respond(400, {"error": "empty request body"})
            return

        try:
            body = self.rfile.read(content_length)
            data: dict[str, Any] = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._respond(400, {"error": f"invalid JSON: {exc}"})
            return

        # Dispatch
        data["hook_event_name"] = hook_name
        debug(hook_name, "http_received")

        try:
            result = dispatch_claude_code_hook(
                hook_name, data, self.sender, self.store,
            )
        except Exception as exc:
            debug(hook_name, "http_error", error=str(exc))
            self._respond(500, {"error": str(exc)})
            return

        if result is not None:
            self._respond(200, result)
        else:
            self._respond(200, {})

    def do_GET(self) -> None:  # noqa: N802
        """Health check."""
        self._respond(200, {"status": "ok", "service": "claude-aiceberg-hook"})

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging unless debug is on."""
        if os.getenv(AICEBERG_HOOK_DEBUG_ENV):
            super().log_message(format, *args)


def run_server(
    workspace: str | None = None,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
) -> None:
    init_debug(workspace)
    paths = workspace_paths(workspace)

    sender = AicebergSender()
    store = ClaudeCodeStateStore(paths.state_db_path)

    # Inject shared sender/store into the handler class
    AicebergHookHandler.sender = sender
    AicebergHookHandler.store = store

    server = HTTPServer((host, port), AicebergHookHandler)

    def shutdown(signum: int, frame: Any) -> None:
        print("\n[aiceberg-server] shutting down...")
        store.close()
        server.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[aiceberg-server] listening on http://{host}:{port}")
    print(f"[aiceberg-server] workspace={paths.workspace_root}")
    print(f"[aiceberg-server] dry_run={sender.dry_run}")
    print(f"[aiceberg-server] use Claude Code hooks with:")
    print(f'  "type": "http", "url": "http://{host}:{port}/<HookEventName>"')

    try:
        server.serve_forever()
    finally:
        store.close()


def server_main() -> None:
    """Entrypoint for claude-aiceberg-server console script."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="claude-aiceberg-server",
        description="HTTP hook server for Claude Code + Aiceberg.",
    )
    parser.add_argument("--workspace", default=None,
                        help="Workspace root (default: current directory)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        os.environ[AICEBERG_HOOK_DEBUG_ENV] = "1"

    run_server(workspace=args.workspace, port=args.port, host=args.host)
