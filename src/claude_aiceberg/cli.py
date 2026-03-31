from __future__ import annotations

import argparse
import sys

from .config import write_settings_file


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
    return parser


def init_main(argv: list[str] | None = None) -> int:
    args = build_init_parser().parse_args(argv)
    try:
        paths = write_settings_file(
            workspace=args.workspace,
            allow_web_search=args.allow_web_search,
            debug=args.debug,
        )
    except (OSError, ValueError) as exc:
        print(f"[claude-aiceberg-init] failed: {exc}", file=sys.stderr)
        return 1

    print(f"[claude-aiceberg-init] wrote {paths.settings_path}")
    print(f"[claude-aiceberg-init] workspace={paths.workspace_root}")
    return 0
