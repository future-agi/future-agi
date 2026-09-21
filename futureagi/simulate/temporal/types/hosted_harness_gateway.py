from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HostedHarnessGatewayInput:
    job_id: str
    endpoint_base_url: str
    max_infrastructure_attempts: int
    initial_backoff_seconds: float
    max_backoff_seconds: float


@dataclass(frozen=True)
class HostedHarnessGatewayOutput:
    job_id: str
    state: str


@dataclass(frozen=True)
class HostedHarnessAuthoringOutput:
    ready: bool
    state: str
    detail: str | None = None


@dataclass(frozen=True)
class HostedHarnessAttemptInput:
    attempt_id: str


@dataclass(frozen=True)
class HostedHarnessLaunchOutput:
    attempt_id: str


@dataclass(frozen=True)
class HostedHarnessPollOutput:
    done: bool
    state: str
    retryable: bool = False


@dataclass(frozen=True)
class HostedHarnessAmendInput:
    """One change-set to apply to a job's authored suite.

    Carries the document rather than a job-side marker so the activity is a pure function of its
    input: retried, it re-applies the same changes to the same suite, and the harness refuses a
    change whose scenario is already gone rather than half-applying a batch.
    """

    job_id: str
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class HostedHarnessAmendOutput:
    """One receipt per change, in the harness's own words."""

    job_id: str
    receipts: list[dict[str, Any]] = field(default_factory=list)
