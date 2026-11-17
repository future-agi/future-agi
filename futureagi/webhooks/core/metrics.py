"""Structured metric lines the core emits (DESIGN.md section 13.2). FA has no Prometheus client: metrics are log
events with a fixed ``metric`` field, which the log pipeline ships; the gauges are SQL the health pass computes."""

from __future__ import annotations

import logging

from .attempt import AttemptRecord
from .ports import Claim

log = logging.getLogger("webhooks.metrics")


def status_class(status: int | None) -> str:
    return "none" if status is None else f"{status // 100}xx"


def attempt_metrics(claim: Claim, rec: AttemptRecord, lane: str, won: bool) -> None:
    """The per-attempt metrics of section 13.2 as structured log lines: ``webhooks.attempts`` (a counter, labelled
    outcome, status class and lane, carrying ``duration_ms`` for the histogram), ``webhooks.ssrf_blocked`` per org,
    and ``webhooks.delivery.first_success_seconds`` (event time to the first 2xx). A superseded settle is not counted here:
    it is an attempt row with outcome ``superseded`` (alert WA8), and a lost renewal logs ``webhooks.lease.superseded``."""
    if not won:
        return
    log.info(
        "webhooks attempt",
        extra={
            "metric": "webhooks.attempts",
            "outcome": rec.outcome,
            "status_class": status_class(rec.response_status),
            "lane": lane,
            "duration_ms": rec.duration_ms,
            "test": claim.is_test,
        },
    )
    if rec.outcome == "ssrf_blocked":
        log.info(
            "webhooks address refused",
            extra={
                "metric": "webhooks.ssrf_blocked",
                "org": claim.endpoint.organization_id,
            },
        )
    if (
        rec.outcome == "success"
        and claim.trigger == "auto"
        and claim.occurred_at_ms is not None
        and rec.settled_at_ms is not None
    ):
        log.info(
            "webhooks first success",
            extra={
                "metric": "webhooks.delivery.first_success_seconds",
                "type": claim.event_type,
                "seconds": max(0, rec.settled_at_ms - claim.occurred_at_ms) / 1000,
            },
        )
