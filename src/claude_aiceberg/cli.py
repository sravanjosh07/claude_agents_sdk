from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, write_settings_file


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-aiceberg-init",
        description="Generate project-local Claude Code hook settings for Claude Aiceberg.",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root where .claude/settings.local.json should be written. Defaults to the current directory.",
    )
    parser.add_argument(
        "--allow-web-search",
        action="store_true",
        help="Add WebSearch to Claude Code's allowed permissions list.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Generate the hook command with --debug enabled.",
    )
    parser.add_argument(
        "--mode",
        choices=["command", "http"],
        default="command",
        help="Hook type: 'command' (subprocess per event) or 'http' (local server). Default: command.",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help=f"Port for HTTP mode (default: {DEFAULT_HTTP_PORT}).",
    )
    parser.add_argument(
        "--http-host",
        default=DEFAULT_HTTP_HOST,
        help=f"Host for HTTP mode (default: {DEFAULT_HTTP_HOST}).",
    )
    return parser


def init_main(argv: list[str] | None = None) -> int:
    args = build_init_parser().parse_args(argv)
    try:
        paths = write_settings_file(
            workspace=args.workspace,
            allow_web_search=args.allow_web_search,
            debug=args.debug,
            mode=args.mode,
            http_host=args.http_host,
            http_port=args.http_port,
        )
    except (OSError, ValueError) as exc:
        print(f"[claude-aiceberg-init] failed: {exc}", file=sys.stderr)
        return 1

    print(f"[claude-aiceberg-init] wrote {paths.settings_path}")
    print(f"[claude-aiceberg-init] workspace={paths.workspace_root}")
    print(f"[claude-aiceberg-init] mode={args.mode}")
    if args.mode == "http":
        print(f"[claude-aiceberg-init] server URL: http://{args.http_host}:{args.http_port}")
        print(f"[claude-aiceberg-init] start the server with:")
        print(f"  claude-aiceberg-server --workspace {paths.workspace_root} --port {args.http_port}")
    return 0
