"""Pure, versioned finding-volume policy; no database or request context."""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

ALGORITHM_VERSION = "finding-volume-v1"
CADENCE = timedelta(minutes=5)
PROCESSING_ALLOWANCE = timedelta(minutes=10)
WINDOW = timedelta(hours=1)
BASELINE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Policy:
    min_findings: int = 20
    entry_multiplier: Decimal = Decimal("3.0")
    entry_delta: int = 10
    entry_confirmation_minutes: int = 15
    recovery_min: int = 5
    recovery_multiplier: Decimal = Decimal("1.5")
    recovery_delta: int = 3
    recovery_confirmation_minutes: int = 10
    revision: int = 0

    def __post_init__(self):
        for name, lower, upper in (
            ("min_findings", 2, 1_000_000),
            ("entry_delta", 1, 1_000_000),
            ("recovery_min", 0, 999_999),
            ("recovery_delta", 0, 999_999),
            ("entry_confirmation_minutes", 5, 60),
            ("recovery_confirmation_minutes", 5, 60),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer from {lower} to {upper}")
            if name.endswith("minutes") and value % 5:
                raise ValueError(f"{name} must be a multiple of five")
        for name, lower in (
            ("entry_multiplier", Decimal("1.1")),
            ("recovery_multiplier", Decimal("1.0")),
        ):
            raw = getattr(self, name)
            try:
                value = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                raise ValueError(f"{name} must be a finite decimal") from None
            if not value.is_finite() or not lower <= value <= 20:
                raise ValueError(f"{name} must be between {lower} and 20")
            if value * 10 != (value * 10).to_integral_value():
                raise ValueError(f"{name} must use increments of 0.1")
            object.__setattr__(self, name, value.quantize(Decimal("0.1")))
        for recovery, entry in (
            ("recovery_min", "min_findings"),
            ("recovery_multiplier", "entry_multiplier"),
            ("recovery_delta", "entry_delta"),
        ):
            if getattr(self, recovery) >= getattr(self, entry):
                raise ValueError(f"{recovery} must be lower than {entry}")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")

    @classmethod
    def from_values(cls, values: dict, *, current: "Policy | None" = None):
        unknown = values.keys() - POLICY_FIELDS
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        merged = asdict(current or cls())
        merged.update(values)
        return cls(**merged)

    def values(self) -> dict:
        return {
            name: str(getattr(self, name))
            if "multiplier" in name
            else getattr(self, name)
            for name in sorted(POLICY_FIELDS)
        }

    def thresholds(self, baseline: Fraction) -> tuple[Fraction, Fraction]:
        return (
            max(
                Fraction(self.min_findings),
                Fraction(self.entry_multiplier) * baseline,
                baseline + self.entry_delta,
            ),
            max(
                Fraction(self.recovery_min),
                Fraction(self.recovery_multiplier) * baseline,
                baseline + self.recovery_delta,
            ),
        )


POLICY_FIELDS = frozenset(
    field.name for field in fields(Policy) if field.name != "revision"
)


@dataclass(frozen=True)
class Counts:
    current: int
    historical: int
    affected_traces: int = 0
    unit: str = "findings"
    next_occurrence_at: datetime | None = None

    def __post_init__(self):
        for value in (self.current, self.historical, self.affected_traces):
            if type(value) is not int or value < 0:
                raise ValueError("Occurrence counts must be nonnegative integers")
        if self.unit not in {"findings", "failed_evaluations"}:
            raise ValueError("Unknown occurrence unit")
        if (
            self.next_occurrence_at is not None
            and self.next_occurrence_at.utcoffset() is None
        ):
            raise ValueError("Occurrence time must be timezone-aware")


@dataclass(frozen=True)
class Decision:
    status: str
    state: dict[str, Any]
    baseline: Fraction
    entry_threshold: Fraction
    recovery_threshold: Fraction
    transitioned: bool = False


def settled_boundary(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("Evaluation time must be timezone-aware")
    settled = now - PROCESSING_ALLOWANCE
    return settled.replace(
        minute=settled.minute - settled.minute % 5, second=0, microsecond=0
    )


def _validate_state(state: dict) -> None:
    allowed = {
        "last_boundary",
        "entry_checks",
        "recovery_checks",
        "frozen_baseline",
        "policy_revision",
        "input_revision",
    }
    if not isinstance(state, dict) or state.keys() - allowed:
        raise ValueError("Invalid escalation progress")
    for name in (
        "entry_checks",
        "recovery_checks",
        "policy_revision",
        "input_revision",
    ):
        value = state.get(name, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid {name}")
        if name.endswith("checks") and value > 13:
            raise ValueError(f"Invalid {name}")
    baseline = state.get("frozen_baseline")
    if baseline is not None and (
        not isinstance(baseline, str) or Fraction(baseline) < 0
    ):
        raise ValueError("Invalid frozen baseline")
    if state.get("last_boundary"):
        if datetime.fromisoformat(state["last_boundary"]).utcoffset() is None:
            raise ValueError("Invalid last boundary")


def evaluate(
    *,
    status: str,
    origin: str,
    state: dict,
    counts: Counts,
    policy: Policy,
    boundary: datetime,
    input_revision: int,
) -> Decision:
    """One settled boundary counts at most once, including after a policy edit."""
    if (
        boundary.utcoffset() is None
        or boundary.minute % 5
        or boundary.second
        or boundary.microsecond
    ):
        raise ValueError("Expected an aware five-minute evaluation boundary")
    if type(input_revision) is not int or input_revision < 0:
        raise ValueError("Invalid input revision")
    _validate_state(state)
    frozen = state.get("frozen_baseline")
    baseline = (
        Fraction(frozen) if frozen is not None else Fraction(counts.historical, 24)
    )
    entry, recovery = policy.thresholds(baseline)
    if origin != "system" or status not in {"for_review", "escalating"}:
        return Decision(status, dict(state), baseline, entry, recovery)

    previous = (
        datetime.fromisoformat(state["last_boundary"])
        if state.get("last_boundary")
        else None
    )
    if previous is not None and boundary < previous:
        return Decision(status, dict(state), baseline, entry, recovery)
    same_boundary = previous == boundary
    changed_policy = bool(state) and state.get("policy_revision") != policy.revision
    consecutive = previous is not None and boundary - previous == CADENCE
    entry_checks = state.get("entry_checks", 0)
    recovery_checks = state.get("recovery_checks", 0)
    reset = changed_policy or (not consecutive and not same_boundary)
    if reset:
        entry_checks = recovery_checks = 0
    if reset and status == "for_review":
        frozen = None
        baseline = Fraction(counts.historical, 24)
        entry, recovery = policy.thresholds(baseline)

    new_status = status
    if status == "for_review":
        recovery_checks = 0
        if counts.current >= entry:
            if not same_boundary:
                entry_checks = min(
                    entry_checks + 1, policy.entry_confirmation_minutes // 5 + 1
                )
                if frozen is None:
                    frozen = str(baseline)
                if entry_checks >= policy.entry_confirmation_minutes // 5 + 1:
                    new_status = "escalating"
        else:
            entry_checks = 0
            frozen = None
    else:
        entry_checks = 0
        if frozen is None:
            # Existing validated episodes always supply this; fail closed.
            raise ValueError("Escalating episode has no frozen baseline")
        if counts.current <= recovery:
            if not same_boundary:
                recovery_checks = min(
                    recovery_checks + 1, policy.recovery_confirmation_minutes // 5 + 1
                )
                if recovery_checks >= policy.recovery_confirmation_minutes // 5 + 1:
                    new_status = "for_review"
                    frozen = None
                    recovery_checks = 0
        else:
            recovery_checks = 0

    progress = {
        "last_boundary": boundary.isoformat(),
        "entry_checks": entry_checks,
        "recovery_checks": recovery_checks,
        "frozen_baseline": frozen,
        "policy_revision": policy.revision,
        "input_revision": input_revision,
    }
    return Decision(
        new_status, progress, baseline, entry, recovery, new_status != status
    )
