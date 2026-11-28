"""Run Epoch from an Error Feed issue: the sample of the issue's traces, what a person asks
for at Run Epoch, and the phases a job goes through."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from simulate.pydantic_schemas.agent_fix import (
    ALLOWED_MODELS_MAX,
    BUDGET_CREDITS_MAX,
    MARGIN_PCT_MAX,
    MAX_MINUTES_MAX,
    Surface,
)

# The traces a sample picks by default, and the alternates offered to swap one for. The
# import's characters are shared by its sources, so more picks cut each one shorter.
SAMPLE_PICKS = 12
SAMPLE_ALTERNATES = 8
# The members read to pick from: enough for the picks and the alternates once repeats of
# a session and traces of another unit are left out.
SAMPLE_POOL = 60

# What the import reads a trace as: its call (a conversation root), its session, or the
# trace alone. A trace's unit never changes, so its source is the same across imports.
SampleUnit = Literal["call", "session", "trace"]
# Why a trace was picked: one of the feed's own examples of the issue, a trace its
# root-cause analysis cited, or a recent member.
PickReason = Literal["example", "cited", "recent"]


class SamplePick(BaseModel):
    # The member of the issue: a trace, or a session for an issue that groups sessions.
    member_id: str
    unit: SampleUnit
    # The id the import reads: the trace's, or its session's.
    source_id: str
    reason: PickReason
    started_at: str | None = None


class IssueSample(BaseModel):
    unit: SampleUnit | None
    picks: list[SamplePick]
    alternates: list[SamplePick]


class IssueLaunch(BaseModel):
    """What the person asked for at Run Epoch; the rest of the launch comes from the
    baseline, as it comes from a run on the launch form."""

    surfaces: list[Surface] = Field(min_length=1)
    locked_paths: list[str] = Field(default_factory=list)
    budget_credits: int = Field(ge=1, le=BUDGET_CREDITS_MAX)
    # Left out, sized from the suite the epoch can select from.
    max_minutes: int | None = Field(default=None, ge=1, le=MAX_MINUTES_MAX)
    # Left out, the models the baseline's spans show.
    allowed_models: list[str] | None = Field(
        default=None, max_length=ALLOWED_MODELS_MAX
    )
    reply_latency_margin_pct: int | None = Field(default=None, ge=0, le=MARGIN_PCT_MAX)
    cost_margin_pct: int | None = Field(default=None, ge=0, le=MARGIN_PCT_MAX)
    cost_limit_usd: float | None = Field(default=None, gt=0)


class IssueJobPhase(StrEnum):
    """Where a job is. Derived from what it holds, never stored."""

    # The builder drafts the picked traces into scenarios.
    DRAFTING = "drafting"
    # Drafts wait for the person's review; Run Epoch goes on from here.
    REVIEW = "review"
    # Drafting ended with no scenario that can run.
    BLOCKED = "blocked"
    # The current version runs on the kept scenarios.
    MEASURING = "measuring"
    # The epoch started; its own state says the rest.
    LAUNCHED = "launched"
    # The job ended before an epoch started (``ending`` says why).
    ENDED = "ended"


class IssueJobEnding(BaseModel):
    code: str
    message: str
