"""Explicit-scope value types and frozen wire definitions for Skills v1.1."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NotRequired, TypedDict
from uuid import UUID


@dataclass(frozen=True)
class SkillScope:
    organization_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class SkillPrincipal:
    scope: SkillScope
    user_id: UUID | None
    api_key_id: UUID | None
    kind: Literal["human", "user_key", "mcp_key", "system_key"]


@dataclass(frozen=True)
class CompletionResult:
    assistant_message: dict
    finish_reason: str | None
    provider_request_id: str | None
    returned_model: str
    usage: dict | None
    latency_ms: int
    estimated_cost_usd: Decimal | None
    pricing_source: str


class ModelConfig(TypedDict):
    model_id: str
    temperature: float
    max_output_tokens: int


class ExecutionConfiguration(TypedDict):
    """Immutable nonsecret evidence, retained even after payload expiry."""

    model: ModelConfig
    judge: ModelConfig | None
    harness_version: str
    adapter_version: str
    evaluator_version: str


class ProfileLimits(TypedDict):
    max_cases: int
    max_variants: int
    repetitions: int
    max_attempts: int
    deadline_seconds: int
    max_task_turns_per_attempt: int
    max_tool_calls_per_attempt: int
    task_phase_seconds: int
    judge_phase_seconds: int
    provider_call_seconds: int
    max_task_output_tokens: int
    max_judge_output_tokens: int
    max_delivered_bytes_per_attempt: int
    max_total_tokens: int


class Profiles(TypedDict):
    quick: ProfileLimits
    repeated: ProfileLimits


class Limits(TypedDict):
    """Effective deployment limits, not requested run budgets.

    Counts/tokens are integers; *_seconds are whole seconds; *_bytes are UTF-8
    bytes; USD is a decimal string with at most eight fractional digits. The
    server applies deployment overrides before producing this shape. Consumers
    use this response rather than inferring units or hard-coding ceilings.
    """

    profiles: Profiles
    max_competitors: int
    max_active_attempts_per_run: int
    max_active_provider_calls_per_organization: int
    max_active_model_jobs_per_organization: int
    max_queue_wait_seconds: int
    max_estimated_cost_usd: str


class FindingLocation(TypedDict):
    kind: Literal["bundle_file", "case_prompt", "fixture", "check", "assist_input"]
    skill_id: NotRequired[str]
    digest: NotRequired[str]
    path: NotRequired[str]
    case_id: NotRequired[str]
    fixture_id: NotRequired[str]
    check_id: NotRequired[str]
    pointer: NotRequired[str]
    line: int | None


class OutboundFinding(TypedDict):
    """finding_id is stable for one plan; explanation/location never contain secrets."""

    finding_id: str
    code: str
    location: FindingLocation
    explanation: str


class SecretReview(TypedDict):
    fingerprint: str
    false_positive_secret_findings: list[dict[str, str]]


TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "interrupted"}
)
ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancelling"})
