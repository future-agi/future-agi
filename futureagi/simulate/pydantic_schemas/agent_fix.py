"""The "Fix my agent" contract: what the user asks for, and what the platform and the
improvement engine (in its sandbox) say to each other."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class Surface(StrEnum):
    PROMPT = "prompt"
    TOOLS = "tools"
    SETTINGS = "settings"
    CODE = "code"


class Ending(StrEnum):
    READY = "ready"
    NO_IMPROVEMENT = "no_improvement"
    NOT_REPRODUCED = "not_reproduced"
    OUT_OF_BUDGET = "out_of_budget"
    FAILED = "failed"


class CandidateOutcome(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    FINALIST = "finalist"
    WINNER = "winner"


class FixRequest(BaseModel):
    """What the user asked for. Stored on the run as it was asked."""

    target_scenario_keys: list[str] = Field(min_length=1, max_length=200)
    # The sub-goals that failed on the targets; the engine proves the fix on them.
    failing_sub_goals: list[str] = Field(min_length=1)
    surfaces: list[Surface] = Field(min_length=1)
    locked_paths: list[str] = Field(default_factory=list)
    budget_usd: float = Field(gt=0, le=500)
    max_minutes: int = Field(gt=0, le=24 * 60)


class InventoryScenario(BaseModel):
    key: str
    name: str
    tests: str
    use_case: str
    sub_goals: list[str]


class EngineBrief(BaseModel):
    """Everything the engine needs to start or resume. Held-back scenarios are part of the
    inventory: the engine's verdict keeps them from the coder."""

    fix_run_id: str
    environment_name: str
    base_agent_version_id: str
    base_commit: str
    request: FixRequest
    inventory: list[InventoryScenario]
    has_checkpoint: bool
    deadline_at: str


class CandidateCreated(BaseModel):
    candidate_id: str
    agent_version_id: str
    preflight_status: str


class CheckRunRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    # The base version or one of this run's candidates.
    agent_version_id: UUID
    scenario_keys: list[str] = Field(min_length=1)
    trials: int = Field(ge=1, le=20)


class CheckRecord(BaseModel):
    """One scenario trial of a check run, in the engine's record shape."""

    scenario_key: str
    trial: int
    # "ok" when every sub-goal was judged; "error" when the trial says nothing about the agent.
    status: str
    error: str
    sub_goals: dict[str, bool]
    reasons: dict[str, str]
    transcript: str


class CheckRunStatus(BaseModel):
    run_id: str
    state: str
    finished: bool
    total: int
    records: list[CheckRecord]


class CandidateVerdict(BaseModel):
    accepted: bool
    reason: str
    target_pass_rate: tuple[float, float] | None = None
    held_back_pass_rate: tuple[float, float] | None = None
    must_pass_regressions: list[str] = Field(default_factory=list)
    # Must-pass checks that dropped within the baseline's own noise: shown, not counted.
    must_pass_within_noise: list[str] = Field(default_factory=list)


class CandidateUpdate(BaseModel):
    candidate_id: UUID
    outcome: CandidateOutcome
    verdict: CandidateVerdict | None = None


class EngineReport(BaseModel):
    """Progress, and at the end the run's ending. A report with an ending is the last."""

    stage: str = Field(max_length=32)
    spent_usd: float = Field(ge=0)
    candidates: list[CandidateUpdate] = Field(default_factory=list)
    ending: Ending | None = None
    summary: str = ""
    winner_candidate_id: UUID | None = None


class EngineModels(BaseModel):
    """The OpenAI-compatible model gateway the engine's planner and coder call."""

    base_url: str
    api_key: str


class EngineCapability(BaseModel):
    """/run/futureagi/agent-fix.json: how the engine in its sandbox reaches the platform.
    Credentials travel in this file only, never in the sandbox's environment."""

    fix_run_id: str
    api_base: str
    token: str
    fence: str
    expires_at: str
    models: EngineModels
