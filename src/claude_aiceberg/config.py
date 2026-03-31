from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CLAUDE_AICEBERG_WORKSPACE_ENV = "CLAUDE_AICEBERG_WORKSPACE"
CLAUDE_AICEBERG_DEBUG_ENV = "CLAUDE_AICEBERG_DEBUG"
AICEBERG_HOOK_DEBUG_ENV = "AICEBERG_HOOK_DEBUG"
DEFAULT_HOOK_COMMAND = "claude-aiceberg-hook"
WEB_SEARCH_PERMISSION = "WebSearch"
MANAGED_CLAUDE_CODE_HOOKS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
)
MATCH_ALL_HOOKS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
# Hooks that never return a decision and can safely run in the background.
ASYNC_HOOKS = {"PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "StopFailure"}


@dataclass(frozen=True)
class WorkspacePaths:
    workspace_root: Path
    claude_dir: Path
    env_path: Path
    state_db_path: Path
    debug_log_path: Path
    settings_path: Path


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolve_workspace_root(workspace: str | Path | None = None) -> Path:
    candidate = workspace or os.getenv(CLAUDE_AICEBERG_WORKSPACE_ENV) or os.getcwd()
    return Path(candidate).expanduser().resolve()


def workspace_paths(workspace: str | Path | None = None) -> WorkspacePaths:
    root = resolve_workspace_root(workspace)
    claude_dir = root / ".claude"
    return WorkspacePaths(
        workspace_root=root,
        claude_dir=claude_dir,
        env_path=root / ".env",
        state_db_path=claude_dir / "aiceberg_state.sqlite3",
        debug_log_path=claude_dir / "aiceberg_hook_debug.jsonl",
        settings_path=claude_dir / "settings.local.json",
    )


DEFAULT_HTTP_PORT = 8932
DEFAULT_HTTP_HOST = "127.0.0.1"


def build_hook_command(workspace: str | Path | None = None, *, debug: bool = False) -> str:
    resolved = resolve_workspace_root(workspace)
    parts = [DEFAULT_HOOK_COMMAND, "--workspace", str(resolved)]
    if debug:
        parts.append("--debug")
    return " ".join(shlex.quote(part) for part in parts)


def build_managed_hook_settings(
    workspace: str | Path | None = None,
    *,
    debug: bool = False,
    mode: str = "command",
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
) -> dict[str, list[dict[str, Any]]]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for hook_name in MANAGED_CLAUDE_CODE_HOOKS:
        if mode == "http":
            hook_entry: dict[str, Any] = {
                "type": "http",
                "url": f"http://{http_host}:{http_port}/{hook_name}",
            }
        else:
            command = build_hook_command(workspace, debug=debug)
            hook_entry = {
                "type": "command",
                "command": command,
            }
            if hook_name in ASYNC_HOOKS:
                hook_entry["async"] = True
        matcher_entry: dict[str, Any] = {
            "hooks": [hook_entry]
        }
        if hook_name in MATCH_ALL_HOOKS:
            matcher_entry["matcher"] = "*"
        hooks[hook_name] = [matcher_entry]
    return hooks


def merge_settings(
    existing: dict[str, Any] | None,
    *,
    workspace: str | Path | None = None,
    allow_web_search: bool = False,
    debug: bool = False,
    mode: str = "command",
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
) -> dict[str, Any]:
    merged = dict(existing or {})
    existing_hooks = merged.get("hooks")
    hooks = dict(existing_hooks) if isinstance(existing_hooks, dict) else {}
    hooks.update(build_managed_hook_settings(
        workspace, debug=debug, mode=mode,
        http_host=http_host, http_port=http_port,
    ))
    merged["hooks"] = hooks

    if allow_web_search:
        existing_permissions = merged.get("permissions")
        permissions = dict(existing_permissions) if isinstance(existing_permissions, dict) else {}
        allow = permissions.get("allow")
        allow_list = [str(item) for item in allow] if isinstance(allow, list) else []
        if WEB_SEARCH_PERMISSION not in allow_list:
            allow_list.append(WEB_SEARCH_PERMISSION)
        permissions["allow"] = allow_list
        merged["permissions"] = permissions

    return merged


def read_settings_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Expected {path} to contain a top-level JSON object.")
    return data


def write_settings_file(
    *,
    workspace: str | Path | None = None,
    allow_web_search: bool = False,
    debug: bool = False,
    mode: str = "command",
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
) -> WorkspacePaths:
    paths = workspace_paths(workspace)
    paths.claude_dir.mkdir(parents=True, exist_ok=True)
    merged = merge_settings(
        read_settings_file(paths.settings_path),
        workspace=paths.workspace_root,
        allow_web_search=allow_web_search,
        debug=debug,
        mode=mode,
        http_host=http_host,
        http_port=http_port,
    )
    paths.settings_path.write_text(json.dumps(merged, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return paths
