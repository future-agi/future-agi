from __future__ import annotations

import copy
from collections.abc import Mapping
from types import ModuleType
from typing import Any

from temporalio.exceptions import ApplicationError

from simulate.temporal import sdk_child
from simulate.temporal.sdk_child import SdkChildError

Answer = Mapping[str, Any] | SdkChildError | ApplicationError

_ENTRY_POINTS = ("run_sdk_child", "run_sdk_child_blocking")


class FakeSdkChild:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._answers: dict[str, list[Answer]] = {}

    def respond(self, operation: str, answer: Answer) -> None:
        self._answers.setdefault(operation, []).append(answer)

    def install(self, monkeypatch: Any, *modules: ModuleType) -> None:
        monkeypatch.setattr(sdk_child, "run_sdk_child", self.run_sdk_child)
        monkeypatch.setattr(
            sdk_child, "run_sdk_child_blocking", self.run_sdk_child_blocking
        )
        for module in modules:
            for name in _ENTRY_POINTS:
                if hasattr(module, name):
                    monkeypatch.setattr(module, name, getattr(self, name))

    async def run_sdk_child(
        self,
        operation: str,
        fields: Mapping[str, Any],
        *,
        credential_id: str | None = None,
        timeout_seconds: int,
        job_ref: str,
    ) -> dict[str, Any]:
        return self._answer(
            operation,
            fields,
            credential_id=credential_id,
            timeout_seconds=timeout_seconds,
            job_ref=job_ref,
        )

    def run_sdk_child_blocking(
        self,
        operation: str,
        fields: Mapping[str, Any],
        *,
        credential_id: str | None = None,
        timeout_seconds: int,
        job_ref: str,
    ) -> dict[str, Any]:
        sdk_child.ensure_off_event_loop()
        return self._answer(
            operation,
            fields,
            credential_id=credential_id,
            timeout_seconds=timeout_seconds,
            job_ref=job_ref,
        )

    def _answer(
        self,
        operation: str,
        fields: Mapping[str, Any],
        *,
        credential_id: str | None,
        timeout_seconds: int,
        job_ref: str,
    ) -> dict[str, Any]:
        sdk_child.validate_call(
            operation,
            fields,
            credential_id=credential_id,
            timeout_seconds=timeout_seconds,
            job_ref=job_ref,
        )
        self.calls.append(
            {
                "operation": operation,
                "fields": dict(fields),
                "credential_id": credential_id,
                "timeout_seconds": timeout_seconds,
                "job_ref": job_ref,
            }
        )
        queued = self._answers.get(operation)
        if not queued:
            raise AssertionError(f"no answer for {operation}")
        answer = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(answer, (SdkChildError, ApplicationError)):
            raise answer
        response = copy.deepcopy(dict(answer))
        response["request_id"] = job_ref
        if not response.get("sdk_version"):
            response["sdk_version"] = "fake"
        return response
