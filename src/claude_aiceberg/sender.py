#!/usr/bin/env python3
"""
Minimal Aiceberg transport for Claude hook monitoring.

This file stays focused on one job:
- build an input or output payload
- send it to Aiceberg or simulate it in dry-run mode
- return a small response object the workflow can reason about
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import workspace_paths


DEFAULT_API_URL = "https://api.test1.aiceberg.ai/eap/v1/event"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MIN_EVENT_GAP_SECONDS = 25.0


def load_env_file(path: str | Path, *, overwrite: bool = True) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if overwrite or key not in os.environ:
            os.environ[key] = value


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes"}


def serialize_content(content: str | dict[str, Any] | list[Any]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=True, sort_keys=True)


@dataclass(frozen=True)
class AicebergResponse:
    ok: bool
    event_result: str = "passed"
    event_id: str | None = None
    message: str | None = None
    status_code: int | None = None
    raw: dict[str, Any] | None = None
    error_body: str | None = None

    @property
    def blocked(self) -> bool:
        return str(self.event_result).lower() in {"block", "blocked", "rejected"}


@dataclass(frozen=True)
class OpenAicebergEvent:
    event_id: str
    event_type: str
    session_id: str
    input_text: str
    metadata: dict[str, Any]
    label: str


class AicebergSender:
    """Small async-friendly wrapper around the Aiceberg event API."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        use_case_id: str | None = None,
        fail_open: bool | None = None,
        timeout_seconds: int | None = None,
        min_event_gap_seconds: float | None = None,
        debug: bool | None = None,
        retry_update_with_input: bool | None = None,
        dry_run: bool | None = None,
    ) -> None:
        load_env_file(workspace_paths().env_path, overwrite=True)
        self.api_url = api_url if api_url is not None else os.getenv("AICEBERG_API_URL", DEFAULT_API_URL)
        self.api_key = api_key if api_key is not None else os.getenv("AICEBERG_API_KEY", "")
        self.use_case_id = use_case_id if use_case_id is not None else os.getenv("USE_CASE_ID", "")
        self.fail_open = fail_open if fail_open is not None else env_flag("AICEBERG_FAIL_OPEN", True)
        self.timeout_seconds = int(
            timeout_seconds if timeout_seconds is not None else os.getenv("AICEBERG_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        self.min_event_gap_seconds = float(
            min_event_gap_seconds
            if min_event_gap_seconds is not None
            else os.getenv("AICEBERG_MIN_EVENT_GAP_SECONDS", str(DEFAULT_MIN_EVENT_GAP_SECONDS))
        )
        self.debug = debug if debug is not None else env_flag("AICEBERG_DEBUG", True)
        self.retry_update_with_input = (
            retry_update_with_input
            if retry_update_with_input is not None
            else env_flag("AICEBERG_RETRY_UPDATE_WITH_INPUT", True)
        )
        self.dry_run = dry_run if dry_run is not None else env_flag("AICEBERG_DRY_RUN", True)
        self._send_lock = asyncio.Lock()
        self._last_send_completed_at: float | None = None

    @property
    def live_enabled(self) -> bool:
        return bool(self.api_key and self.api_url and self.use_case_id)

    def is_configured(self) -> bool:
        return self.dry_run or self.live_enabled

    async def create_event(
        self,
        *,
        label: str,
        event_type: str,
        content: str | dict[str, Any] | list[Any],
        session_id: str,
        metadata: dict[str, Any] | None = None,
        session_start: bool = False,
    ) -> tuple[AicebergResponse, OpenAicebergEvent | None]:
        input_text = serialize_content(content)
        if self.debug:
            print(f"[aiceberg-sender] sending {label} (event_type={event_type}, session_id={session_id})")

        payload = self._payload(
            event_type=event_type,
            session_id=session_id,
            content_key="input",
            content_text=input_text,
            metadata=metadata,
            session_start=session_start and event_type == "user_agt",
        )
        response = await self._send(payload)
        if not response.event_id:
            return response, None

        return response, OpenAicebergEvent(
            event_id=response.event_id,
            event_type=event_type,
            session_id=session_id,
            input_text=input_text,
            metadata=dict(metadata or {}),
            label=label,
        )

    async def close_event(
        self,
        event: OpenAicebergEvent,
        *,
        output: str | dict[str, Any] | list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> AicebergResponse:
        if not self.is_configured():
            return self._skipped_response()

        if self.debug:
            print(
                f"[aiceberg-sender] closing {event.label} "
                f"(event_id={event.event_id}, session_id={event.session_id})"
            )

        close_metadata = dict(event.metadata if metadata is None else metadata)
        output_text = serialize_content(output)
        payload = self._payload(
            event_type=event.event_type,
            session_id=event.session_id,
            content_key="output",
            content_text=output_text,
            metadata=close_metadata,
            event_id=event.event_id,
        )
        response = await self._send(payload)

        if self._should_retry_close_with_input(response):
            retry_payload = dict(payload)
            retry_payload["input"] = event.input_text
            if self.debug:
                print(f"[aiceberg-sender] retrying close for {event.label} with input included")
            response = await self._send(retry_payload)

        return response

    async def _send(self, payload: dict[str, Any]) -> AicebergResponse:
        if not self.is_configured():
            return self._skipped_response()

        async with self._send_lock:
            await self._wait_for_send_gap()
            response = await asyncio.to_thread(self._post_payload, payload)
            if response.ok:
                self._last_send_completed_at = time.monotonic()
            return response

    def _payload(
        self,
        *,
        event_type: str,
        session_id: str,
        content_key: str,
        content_text: str,
        metadata: dict[str, Any] | None,
        event_id: str | None = None,
        session_start: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "use_case_id": self.use_case_id,
            "session_id": session_id,
            "event_type": event_type,
            content_key: content_text,
            "forward_to_llm": False,
        }
        if event_id:
            payload["event_id"] = event_id
        if metadata:
            payload["metadata"] = metadata
        if session_start:
            payload["session_start"] = True
        return payload

    async def _wait_for_send_gap(self) -> None:
        if self._last_send_completed_at is None:
            return

        remaining = self.min_event_gap_seconds - (time.monotonic() - self._last_send_completed_at)
        if remaining > 0:
            if self.debug:
                print(f"[aiceberg-sender] waiting {remaining:.1f}s before next event")
            await asyncio.sleep(remaining)

    def _post_payload(self, payload: dict[str, Any]) -> AicebergResponse:
        if self.debug:
            print("[aiceberg-sender] payload:")
            print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))

        if self.dry_run:
            response = self._dry_run_response(payload)
            if self.debug:
                print("[aiceberg-sender] dry run enabled, payload not sent")
                print("[aiceberg-sender] simulated response:")
                print(json.dumps(response.raw or {}, indent=2, ensure_ascii=True, sort_keys=True))
            return response

        request = urllib_request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "claude-aiceberg-hooks/0.1",
            },
        )

        try:
            with urllib_request.urlopen(
                request,
                timeout=self.timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                raw_body = response.read().decode("utf-8").strip()
            data = self._parse_json(raw_body)
            if self.debug:
                print("[aiceberg-sender] response:")
                print(json.dumps(data, indent=2, ensure_ascii=True, sort_keys=True))
            return AicebergResponse(
                ok=True,
                event_result=str(data.get("event_result", "passed")),
                event_id=data.get("event_id"),
                message=data.get("message") or data.get("reason"),
                status_code=200,
                raw=data,
            )
        except urllib_error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return self._failure_response(str(exc), raw_body=raw_body, status_code=exc.code)
        except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return self._failure_response(str(exc), raw_body="")

    def _failure_response(
        self,
        error_text: str,
        *,
        raw_body: str,
        status_code: int | None = None,
    ) -> AicebergResponse:
        data = self._parse_json(raw_body)
        message_parts = [
            str(data.get("error", "")).strip(),
            str(data.get("details", "")).strip(),
            str(data.get("message", "")).strip(),
        ]
        message = " | ".join(part for part in message_parts if part) or raw_body.strip() or error_text

        if self.debug:
            if status_code is None:
                print(f"[aiceberg-sender] request failed: {error_text}")
            else:
                print(f"[aiceberg-sender] request failed: status={status_code}")
            if raw_body:
                print("[aiceberg-sender] error body:")
                print(raw_body)

        return AicebergResponse(
            ok=False,
            event_result="passed" if self.fail_open else "blocked",
            event_id=data.get("event_id"),
            message=message,
            status_code=status_code,
            raw=data or {"error": raw_body or error_text},
            error_body=raw_body or None,
        )

    def _should_retry_close_with_input(self, response: AicebergResponse) -> bool:
        if not self.retry_update_with_input or response.ok:
            return False
        body = (response.error_body or "").lower()
        return "input" in body and any(token in body for token in ("required", "empty", "invalid", "missing"))

    @staticmethod
    def _parse_json(raw_body: str) -> dict[str, Any]:
        if not raw_body.strip():
            return {}
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            return {"raw_body": raw_body}
        return data if isinstance(data, dict) else {"raw_body": raw_body}

    @staticmethod
    def _dry_run_response(payload: dict[str, Any]) -> AicebergResponse:
        event_id = str(payload.get("event_id") or f"dryrun-{uuid.uuid4().hex[:12]}")
        raw = {
            "mode": "dry_run",
            "event_id": event_id,
            "event_type": payload.get("event_type"),
            "session_id": payload.get("session_id"),
            "event_result": "passed",
            "payload": payload,
        }
        return AicebergResponse(
            ok=True,
            event_result="passed",
            event_id=event_id,
            message="dry_run",
            status_code=200,
            raw=raw,
        )

    @staticmethod
    def _skipped_response() -> AicebergResponse:
        return AicebergResponse(ok=False, message="missing_api_config", raw={"skipped": "missing_api_config"})
