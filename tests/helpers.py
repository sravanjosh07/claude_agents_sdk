"""Shared test helpers."""

from __future__ import annotations

import uuid
from collections import deque
from typing import Any

from claude_aiceberg.sender import AicebergResponse, OpenAicebergEvent


class FakeSender:
    """In-memory stand-in for AicebergSender."""

    def __init__(self) -> None:
        self.dry_run = True
        self.created: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.create_responses: deque[AicebergResponse] = deque()
        self.close_responses: deque[AicebergResponse] = deque()

    def _next_create(self) -> AicebergResponse:
        if self.create_responses:
            return self.create_responses.popleft()
        return AicebergResponse(
            ok=True, event_id=f"fake-{uuid.uuid4().hex[:8]}", message="ok",
        )

    def _next_close(self) -> AicebergResponse:
        if self.close_responses:
            return self.close_responses.popleft()
        return AicebergResponse(ok=True, message="ok")

    async def create_event(self, **kwargs: Any) -> tuple[AicebergResponse, OpenAicebergEvent | None]:
        self.created.append(kwargs)
        resp = self._next_create()
        event = None
        if resp.event_id:
            event = OpenAicebergEvent(
                event_id=resp.event_id,
                event_type=kwargs.get("event_type", "user_agt"),
                session_id=kwargs.get("session_id", ""),
                input_text=str(kwargs.get("content", "")),
                metadata=kwargs.get("metadata", {}),
                label=kwargs.get("label", ""),
            )
        return resp, event

    async def close_event(self, event: OpenAicebergEvent, **kwargs: Any) -> AicebergResponse:
        self.closed.append({"event": event, **kwargs})
        return self._next_close()
