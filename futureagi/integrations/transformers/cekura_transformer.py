"""Transform Cekura chat-test transcripts into FutureAGI trace records."""

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from integrations.transformers.base import BaseTraceTransformer, register_transformer


class CekuraTransformer(BaseTraceTransformer):
    """Map a Cekura run and its transcript to the common trace shape.

    Cekura can deliver transcript fields in either snake_case or camelCase.
    The importer deliberately accepts both forms, while producing stable,
    namespaced span IDs so webhook retries update rows instead of duplicating
    them or colliding with spans from another source.
    """

    def transform_trace(
        self, raw_trace: dict[str, Any], project_id: str
    ) -> dict[str, Any]:
        run_id = self._run_id(raw_trace)
        metadata = raw_trace.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {"original_metadata": metadata}
        metadata = {**metadata, "integration_source": "cekura"}

        tags = raw_trace.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        if "cekura" not in tags:
            tags.append("cekura")

        return {
            "external_id": run_id,
            "project_id": project_id,
            "name": (
                raw_trace.get("name")
                or raw_trace.get("test_name")
                or raw_trace.get("testName")
                or raw_trace.get("scenario_name")
                or raw_trace.get("scenarioName")
                or "Cekura chat test"
            ),
            "input": raw_trace.get("input"),
            "output": raw_trace.get("output"),
            "metadata": metadata,
            "tags": tags,
            "user_id": raw_trace.get("user_id") or raw_trace.get("userId"),
            "session_id": raw_trace.get("session_id") or raw_trace.get("sessionId"),
        }

    def transform_observations(
        self, raw_trace: dict[str, Any], trace_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        run_id = self._run_id(raw_trace)
        observations: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(self._turns(raw_trace)):
            if not isinstance(turn, dict):
                continue

            turn_id = self._span_id(
                "turn", run_id, self._source_id(turn, turn_index)
            )
            start_time = self._timestamp(
                turn.get("start_time")
                or turn.get("startTime")
                or turn.get("timestamp")
            )
            end_time = self._timestamp(turn.get("end_time") or turn.get("endTime"))
            role = turn.get("role") or turn.get("speaker") or "unknown"
            content = turn.get("content", turn.get("message", turn.get("text")))
            turn_input = turn.get("input")
            turn_output = turn.get("output") or turn.get("response")
            if turn_input is None and role in ("user", "customer", "system"):
                turn_input = {"role": role, "content": content}
            if turn_output is None and role in ("assistant", "agent", "bot"):
                turn_output = {"role": role, "content": content}

            observations.append(
                {
                    "id": turn_id,
                    "trace_id": trace_id,
                    "project_id": project_id,
                    "parent_span_id": None,
                    "observation_type": "conversation",
                    "name": turn.get("name") or f"{str(role).title()} turn",
                    "start_time": start_time,
                    "end_time": end_time,
                    "input": turn_input,
                    "output": turn_output,
                    "metadata": turn.get("metadata") or {},
                    "model": "",
                    "provider": "",
                    "model_parameters": {},
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": self._latency_ms(turn, start_time, end_time),
                    "cost": 0,
                    "status": self._status(turn.get("status")),
                    "span_attributes": {"fi.span.kind": "CONVERSATION"},
                    "eval_attributes": {"fi.span.kind": "CONVERSATION"},
                }
            )

            for tool_index, tool in enumerate(self._tools(turn)):
                if not isinstance(tool, dict):
                    continue
                tool_start = self._timestamp(
                    tool.get("start_time")
                    or tool.get("startTime")
                    or tool.get("timestamp")
                )
                tool_end = self._timestamp(tool.get("end_time") or tool.get("endTime"))
                tool_id = self._span_id(
                    "tool",
                    run_id,
                    f"{self._source_id(turn, turn_index)}:"
                    f"{self._source_id(tool, tool_index)}",
                )
                tool_input = (
                    tool.get("input")
                    or tool.get("arguments")
                    or tool.get("parameters")
                )
                tool_output = (
                    tool.get("output") or tool.get("result") or tool.get("response")
                )
                observations.append(
                    {
                        "id": tool_id,
                        "trace_id": trace_id,
                        "project_id": project_id,
                        "parent_span_id": turn_id,
                        "observation_type": "tool",
                        "name": tool.get("name") or tool.get("tool_name") or "Tool call",
                        "start_time": tool_start,
                        "end_time": tool_end,
                        "input": tool_input,
                        "output": tool_output,
                        "metadata": tool.get("metadata") or {},
                        "model": "",
                        "provider": "",
                        "model_parameters": {},
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "latency_ms": self._latency_ms(tool, tool_start, tool_end),
                        "cost": 0,
                        "status": self._status(tool.get("status")),
                        "span_attributes": {"fi.span.kind": "TOOL"},
                        "eval_attributes": {"fi.span.kind": "TOOL"},
                    }
                )
        return observations

    def transform_scores(self, raw_trace: dict[str, Any], trace_id: str) -> list[dict]:
        """Scores belong to the separate Cekura evaluation ingestion feature."""
        return []

    @staticmethod
    def _turns(raw_trace: dict[str, Any]) -> list[Any]:
        transcript = (
            raw_trace.get("turns")
            or raw_trace.get("transcript")
            or raw_trace.get("conversation")
            or []
        )
        if isinstance(transcript, dict):
            transcript = transcript.get("turns") or transcript.get("messages") or []
        return transcript if isinstance(transcript, list) else []

    @staticmethod
    def _tools(turn: dict[str, Any]) -> list[Any]:
        tools = turn.get("tool_calls") or turn.get("toolCalls") or turn.get("tools") or []
        return tools if isinstance(tools, list) else []

    @staticmethod
    def _source_id(value: dict[str, Any], index: int) -> str:
        return str(
            value.get("id") or value.get("turn_id") or value.get("turnId") or index
        )

    @staticmethod
    def _span_id(kind: str, run_id: str, source_id: str) -> str:
        digest = sha256(f"{run_id}:{source_id}".encode()).hexdigest()[:40]
        return f"cekura-{kind}-{digest}"

    @staticmethod
    def _run_id(raw_trace: dict[str, Any]) -> str:
        run_id = raw_trace.get("id") or raw_trace.get("run_id") or raw_trace.get("runId")
        if not run_id:
            raise ValueError("Cekura run missing required 'id' field")
        run_id = str(run_id)
        if len(run_id) > 255:
            raise ValueError("Cekura run id exceeds 255 characters")
        return run_id

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            if value > 10_000_000_000:
                value /= 1000
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return value if isinstance(value, datetime) else None

    @staticmethod
    def _latency_ms(
        raw: dict[str, Any], start: datetime | None, end: datetime | None
    ) -> int:
        value = (
            raw.get("latency_ms")
            or raw.get("latencyMs")
            or raw.get("duration_ms")
            or raw.get("durationMs")
        )
        if value is not None:
            return int(value)
        if start and end:
            return int((end - start).total_seconds() * 1000)
        return 0

    @staticmethod
    def _status(status: Any) -> str:
        return "ERROR" if str(status).lower() in {"error", "failed", "failure"} else "OK"


register_transformer("cekura", CekuraTransformer())
