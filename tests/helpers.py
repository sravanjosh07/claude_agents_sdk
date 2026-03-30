from __future__ import annotations

from claude_aiceberg import AicebergResponse, OpenAicebergEvent
from claude_aiceberg.sender import serialize_content


class FakeSender:
    def __init__(self) -> None:
        self.dry_run = True
        self.created: list[dict[str, object]] = []
        self.closed: list[dict[str, object]] = []
        self.create_responses: list[AicebergResponse] = []
        self.close_responses: list[AicebergResponse] = []
        self._counter = 0

    async def create_event(
        self,
        *,
        label: str,
        event_type: str,
        content,
        session_id: str,
        metadata=None,
        session_start: bool = False,
    ):
        self.created.append(
            {
                "label": label,
                "event_type": event_type,
                "content": content,
                "session_id": session_id,
                "metadata": metadata or {},
                "session_start": session_start,
            }
        )
        self._counter += 1
        response = self.create_responses.pop(0) if self.create_responses else AicebergResponse(
            ok=True,
            event_result="passed",
            event_id=f"evt-{self._counter}",
        )
        open_event = None
        if response.event_id:
            input_text = serialize_content(content)
            open_event = OpenAicebergEvent(
                event_id=response.event_id,
                event_type=event_type,
                session_id=session_id,
                input_text=input_text,
                metadata=dict(metadata or {}),
                label=label,
            )
        return response, open_event

    async def close_event(self, event: OpenAicebergEvent, *, output, metadata=None):
        self.closed.append(
            {
                "event": event,
                "output": output,
                "metadata": metadata,
            }
        )
        return self.close_responses.pop(0) if self.close_responses else AicebergResponse(
            ok=True,
            event_result="passed",
            event_id=event.event_id,
        )
