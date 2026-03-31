"""
SDK hook wiring -- connects Claude Agent SDK hooks to the workflow.

Uses a dispatch table instead of one method per hook. The only special
logic is build_block_output which formats block responses differently
for tool hooks vs prompt hooks.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import HookMatcher

from .workflow import ClaudeAicebergWorkflow

# Every hook the SDK can fire.
SUPPORTED_HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "Notification",
    "PermissionRequest",
)

_TOOL_HOOKS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}


def _dispatch_table(wf: ClaudeAicebergWorkflow) -> dict[str, Any]:
    return {
        "UserPromptSubmit":   wf.handle_user_prompt_submit,
        "PreToolUse":         wf.handle_pre_tool_use,
        "PostToolUse":        wf.handle_post_tool_use,
        "PostToolUseFailure": wf.handle_post_tool_use_failure,
        "Stop":               wf.handle_stop,
        "StopFailure":        wf.handle_stop_failure,
        "SubagentStart":      wf.handle_subagent_start,
        "SubagentStop":       wf.handle_subagent_stop,
    }


async def dispatch_hook(
    wf: ClaudeAicebergWorkflow,
    hook_name: str,
    data: dict[str, Any],
    *,
    tool_use_id: str | None = None,
) -> Any:
    """Route a hook event to the right workflow handler."""
    table = _dispatch_table(wf)
    handler = table.get(hook_name)
    if handler is None:
        return await wf.handle_noop(data)
    if hook_name in _TOOL_HOOKS:
        return await handler(data, tool_use_id)
    return await handler(data)


def build_block_output(hook_name: str, reason: str) -> dict[str, Any]:
    """Format a block decision for the SDK hook return value."""
    base: dict[str, Any] = {"decision": "block", "reason": reason}
    if hook_name in ("PreToolUse", "PermissionRequest"):
        base["hookSpecificOutput"] = {"suppressToolOutput": True}
    return base


def build_hook_registry(
    wf: ClaudeAicebergWorkflow,
) -> dict[str, list[dict[str, Any]]]:
    """Build the hooks dict for ClaudeAgentOptions.

    For each supported hook, registers a callback that normalises
    the SDK kwargs into a flat dict and calls dispatch_hook.
    """
    registry: dict[str, list[dict[str, Any]]] = {}

    for hook_name in SUPPORTED_HOOK_EVENTS:
        is_tool = hook_name in _TOOL_HOOKS

        def _make_cb(name: str, is_tool_hook: bool):
            async def callback(**kwargs: Any) -> Any:
                data = dict(kwargs)
                data["hook_event_name"] = name
                tid = None
                if is_tool_hook:
                    tid = str(data.pop("tool_use_id", "") or "").strip() or None
                return await dispatch_hook(wf, name, data, tool_use_id=tid)
            return callback

        entry: dict[str, Any] = {
            "matcher": HookMatcher(matcher="*") if is_tool else {},
            "callback": _make_cb(hook_name, is_tool),
        }
        registry[hook_name] = [entry]

    return registry
