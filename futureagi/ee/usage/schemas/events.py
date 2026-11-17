"""Pydantic schemas for usage events and pre-check results.

These are the core data contracts for the entire metering pipeline:
- UsageEvent: emitted by call sites, consumed by the consumer
- CheckResult: returned by check_usage() to tell callers if an action is allowed
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, Field, field_validator

# Every evaluation step's event_id is derived under this namespace. It never
# changes: a new one would give every step a new id, and a step a worker runs
# again would be billed again.
EVALUATION_EVENT_NAMESPACE = UUID("828aedcb-e4d1-481a-aa68-ea89f06ddb0e")


class UpgradeCTA(BaseModel):
    """Call-to-action for upgrade prompts in the UI.

    The frontend owns the destination route — we only tell it what to show
    (text) and which plan the user should upgrade to. The client resolves
    the navigation target itself.
    """

    model_config = {"frozen": True}

    text: str = Field(..., min_length=1, description="CTA text shown to user")
    plan: str = Field(
        ..., min_length=1, description="Target plan key (e.g., 'payg', 'boost')"
    )


class CheckResult(BaseModel):
    """Result of a usage pre-check. Tells the caller if an action is allowed.

    Used by check_usage() and check_entitlement() to gate billable actions.
    """

    model_config = {"frozen": True}

    allowed: bool = Field(..., description="Whether the action should proceed")
    reason: str = Field(
        default="", description="Human-readable explanation for error responses"
    )
    error_code: str = Field(
        default="",
        description=(
            "Machine-readable error code: FREE_TIER_LIMIT, RATE_LIMITED, "
            "BUDGET_PAUSED, ENTITLEMENT_LIMIT, ENTITLEMENT_DENIED, "
            "PAYMENT_REQUIRED, ACCOUNT_SUSPENDED, LICENSE_EXPIRED"
        ),
    )
    dimension: str = Field(default="", description="Billing dimension that was checked")
    current_usage: float = Field(
        default=0, description="Current usage at time of check (native units)"
    )
    limit: float = Field(
        default=0, description="The limit checked against (native units)"
    )
    upgrade_cta: UpgradeCTA | None = Field(
        default=None,
        description="Upgrade prompt for the UI. None if no upgrade path.",
    )


class UsageEvent(BaseModel):
    """A structured usage event — the universal billing primitive.

    Emitted by call sites via emit(), consumed by the billing consumer.
    Same schema pattern as Lago/Metronome/Orb.

    Every billable action in the system produces exactly one UsageEvent.
    """

    event_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Idempotency key. Consumer deduplicates on this.",
    )
    org_id: str = Field(
        ...,
        min_length=1,
        description="Organization ID. Must be non-empty.",
    )
    event_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="API call type key from billing.yaml (e.g., 'turing_large_evaluator'). Use BillingEventType enum.",
    )

    @field_validator("event_type", mode="before")
    @classmethod
    def coerce_event_type(cls, v):
        if hasattr(v, "value"):
            return v.value
        return v

    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the usage occurred. Set by emitter, not consumer.",
    )
    amount: float = Field(
        default=1,
        ge=0,
        description="Units consumed. Native units (bytes, credits, requests, etc.).",
    )
    properties: dict = Field(
        default_factory=dict,
        description="Flat k/v context: source, source_id, model, workspace_id, etc.",
    )


def stable_evaluation_event_id(
    *,
    evaluated: object | tuple[object, ...] | None,
    run: object | tuple[object, ...] | None,
    evaluator: object | tuple[object, ...] | None,
) -> str | None:
    """The event_id of one evaluation step's usage events, or None when one of
    its three parts is unknown.

    ``evaluated`` is what was evaluated (a row and its column, a span, a
    request), ``run`` the run that evaluated it and ``evaluator`` what
    evaluated it; each is a value or a tuple of values. The same three always
    give the same id. A step that a worker runs again after a restart or a
    retry emits the events it emitted before, and the consumer, which keeps one
    event per event_id, bills the step once. A run the customer starts again is
    a new run, so its events are new and are billed.

    When any of the three is unknown, the step has nothing to be the same as:
    its events are keyed on their own call and get fresh ids.
    """
    parts = [_identity(evaluated), _identity(run), _identity(evaluator)]
    if None in parts:
        return None
    name = json.dumps(parts, separators=(",", ":"))
    return str(uuid5(EVALUATION_EVENT_NAMESPACE, name))


def _identity(value) -> list[str] | None:
    """The value, or tuple of values, as strings; None when none is known."""
    values = value if isinstance(value, tuple | list) else (value,)
    rendered = ["" if item is None else str(item) for item in values]
    return rendered if any(rendered) else None
