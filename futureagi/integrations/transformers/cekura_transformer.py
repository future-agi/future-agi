"""Cekura chat-test payloads → FutureAGI eval shapes.

Kept separate from the webhook view and from the persistence path, the same
three-piece split the Langfuse integration uses (view / transformer / upsert),
so adjusting to a Cekura payload change touches this file only.
"""

import hashlib
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_EVAL_ID_PREFIX = "cekura:"
_EVAL_ID_LIMIT = 255
# EvalLogger.eval_type_id is a CharField(255) and the name comes from Cekura.
_METRIC_NAME_LIMIT = 255


class CekuraTransformer:
    """Map a Cekura run-completed payload onto ``EvalLogger`` field dicts.

    Deliberately not a ``BaseTraceTransformer``: that contract converts a
    third-party *trace* (trace + observations + scores) and is resolved
    through the platform registry. Cekura's run-completed webhook carries
    already-computed scores and no transcript, so only the eval half exists
    here. Importing Cekura transcripts as traces is a separate piece of work.
    """

    # Run states where scores are not final yet. Anything else (including an
    # absent status, or a terminal "failed") carries results worth ingesting:
    # a regression run that fails is exactly the one whose scores matter.
    IN_FLIGHT_STATUSES = frozenset(
        {
            "queued",
            "pending",
            "running",
            "in_progress",
            "in-progress",
            "started",
            "cancelled",
            "canceled",
            "aborted",
        }
    )

    def is_ingestible(self, payload: dict[str, Any]) -> bool:
        """Whether the run reached a state with final per-metric scores."""
        status = (payload.get("status") or "").strip().lower()
        return status not in self.IN_FLIGHT_STATUSES

    def to_eval_logger_fields(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn each per-run metric into ``EvalLogger`` field kwargs.

        ``eval_id`` is derived from the run id and the metric name so a
        redelivered webhook updates the same row instead of adding one.
        Metrics without a name are dropped: the name is the only stable part
        of that key.
        """
        run_id = str(payload.get("run_id") or "")
        scores = []

        for metric in payload.get("metrics") or []:
            if not isinstance(metric, dict):
                continue

            name = str(metric.get("name") or "").strip()
            if not name:
                logger.warning("cekura_metric_without_name", run_id=run_id)
                continue

            # Dropping the one metric beats letting the model layer raise:
            # an unhandled save error would 500 the whole delivery and cost
            # the run its other scores on every redelivery.
            if len(name) > _METRIC_NAME_LIMIT:
                logger.warning(
                    "cekura_metric_name_too_long",
                    run_id=run_id,
                    name_length=len(name),
                )
                continue

            scores.append(
                {
                    "eval_id": _eval_id(run_id, name),
                    "eval_type_id": name,
                    "output_float": _as_float(metric.get("score")),
                    "output_bool": _as_bool(metric.get("passed")),
                    "output_str": str(metric.get("label") or ""),
                    "eval_explanation": str(metric.get("explanation") or ""),
                }
            )

        return scores

    def run_window(
        self, payload: dict[str, Any]
    ) -> tuple[datetime | None, datetime | None]:
        """Start/end of the run, when Cekura reports them."""
        return (
            _parse_iso8601(payload.get("started_at")),
            _parse_iso8601(payload.get("completed_at")),
        )


def _eval_id(run_id: str, metric_name: str) -> str:
    """Stable idempotency key for one metric of one run.

    ``EvalLogger.eval_id`` is 255 chars and both halves are attacker-shaped
    input, so a long run id or metric name would raise on save. Past the
    limit the pair is hashed instead of truncated: truncation would collide
    two metrics of the same run onto one row and silently drop a score.
    """
    readable = f"{_EVAL_ID_PREFIX}{run_id}:{metric_name}"
    if len(readable) <= _EVAL_ID_LIMIT:
        return readable
    digest = hashlib.sha256(f"{run_id}:{metric_name}".encode()).hexdigest()
    return f"{_EVAL_ID_PREFIX}{digest}"


def _as_float(value: Any) -> float | None:
    """Coerce a reported score to float, keeping ``None`` for absent scores.

    Booleans are rejected on purpose: ``bool`` is an ``int`` subclass in
    Python, so a ``passed``-style value landing in ``score`` would otherwise
    be stored as 1.0 and read as a real number.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """Coerce a pass/fail flag, keeping ``None`` when the run omits it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "pass", "passed"}:
            return True
        if lowered in {"false", "fail", "failed"}:
            return False
    return None


def _parse_iso8601(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp, tolerating the ``Z`` suffix."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None
