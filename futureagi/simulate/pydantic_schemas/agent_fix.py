"""The "Fix my agent" contract: what the user asks for, and what the platform and the
improvement engine (in its sandbox) say to each other."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

# What one fix may ask for; the launch form shows the same limits.
SCENARIOS_MAX = 200
BUDGET_CREDITS_MAX = 50_000
MAX_MINUTES_MAX = 24 * 60
# Held-back cases a fix needs to count as verified. On a failure that fails every baseline
# run, 4 pass a large real fix 87% of the time and 2 only 56%.
HELD_BACK_VERIFIED_MIN = 4
# A third of the failing cases are held back, once there are enough for a third to tell.
HELD_BACK_SHARE = 3
# A run with too few failing cases gets this many variations of each written first.
VARIATIONS_PER_CASE = 3
# How much slower a candidate's replies, and how much more its own model calls per
# conversation, may be than the base version's, in percent, unless the user says.
MARGIN_PCT_DEFAULT = 25
MARGIN_PCT_MAX = 1000
ALLOWED_MODELS_MAX = 50


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


class FixStep(StrEnum):
    """Where a fix is, in the steps the user follows. Variations comes first, and only
    for a run with too few failing cases."""

    VARIATIONS = "variations"
    REPRODUCE = "reproduce"
    SEARCH = "search"
    VERIFY = "verify"
    DONE = "done"


class CandidateOutcome(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    FINALIST = "finalist"
    WINNER = "winner"


class FixRequest(BaseModel):
    """What the user asked for. Stored on the run as it was asked."""

    target_scenario_keys: list[str] = Field(min_length=1, max_length=SCENARIOS_MAX)
    # Cases that pass today and must still pass: every candidate shown is checked on them,
    # on top of what the engine itself guards.
    protected_scenario_keys: list[str] = Field(
        default_factory=list, max_length=SCENARIOS_MAX
    )
    # The failing cases among the targets that the worker never sees: the verdict proves the
    # fix holds on them. Chosen at launch; empty when there are too few to split.
    held_back_scenario_keys: list[str] = Field(default_factory=list)
    # The sub-goals that failed on the targets; the engine proves the fix on them.
    failing_sub_goals: list[str] = Field(min_length=1)
    surfaces: list[Surface] = Field(min_length=1)
    locked_paths: list[str] = Field(default_factory=list)
    budget_usd: float = Field(gt=0, le=BUDGET_CREDITS_MAX / 100)
    max_minutes: int = Field(gt=0, le=MAX_MINUTES_MAX)
    # Set at launch on a run with too few failing cases: the fix's first step writes
    # variations of them, and clears it once the variations that failed are targets.
    variations_pending: bool = False
    # Each variation that became a target, with the failing case it varies: it is held
    # back with that case, and counts as that case toward verified.
    variation_parents: dict[str, str] = Field(default_factory=dict)
    # The models the agent may call. Every model call a check run's spans show must name
    # one of them; with no spans, only the candidate's diff is checked.
    allowed_models: list[str] = Field(
        default_factory=list, max_length=ALLOWED_MODELS_MAX
    )
    # How much slower a candidate's median reply, and how much more its agent's median
    # spend per conversation, may be than the base version's, in percent; and a limit on
    # that spend in US dollars.
    reply_latency_margin_pct: int = Field(
        default=MARGIN_PCT_DEFAULT, ge=0, le=MARGIN_PCT_MAX
    )
    cost_margin_pct: int = Field(default=MARGIN_PCT_DEFAULT, ge=0, le=MARGIN_PCT_MAX)
    cost_limit_usd: float | None = Field(default=None, gt=0)
    # Set at launch from the source run's spans: whether they named the agent's models,
    # and whether the agent exported its model calls at all. Without them a check run's
    # models and agent cost cannot be read, and those checks read as not verified.
    observes_models: bool = False
    meters_agent_cost: bool = False


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
    # The models earlier launches stopped using in the last hour because their calls were
    # lost: a relaunch starts without them.
    lost_models: list[str] = Field(default_factory=list)
    # Where a relaunched engine picks up: its attempts are numbered after the last candidate
    # an earlier launch sent, and its budget starts from what the fix has committed.
    last_attempt: int
    committed_usd: float
    # What a check run reserves of the fix's budget for each execution: a screen or the
    # variations, which are never graded, and a run a winner's grading reads (the baseline
    # repeats and a final check), its billed evals included.
    worst_case_usd_per_execution: float
    worst_case_usd_per_graded_execution: float
    deadline_at: str
    # The source run's trials that failed a sub-goal on the targets the engine searches:
    # Diagnose reads them while Reproduce runs. Never a held-back target.
    source_failures: list["CheckRecord"] = Field(default_factory=list)


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
    # Which repeat of a check this run is: it sets the run's seed.
    repeat: int = Field(default=1, ge=1, le=20)


class FailureOwner(StrEnum):
    """Who can fix a failure: the platform, or the user (their credentials, source or plan)."""

    OURS = "ours"
    USER = "user"


class CheckFailure(BaseModel):
    """Why a check run, or one trial of it, failed to say anything about the agent."""

    owner: FailureOwner
    code: str
    message: str


class CheckRecord(BaseModel):
    """One scenario trial of a check run, in the engine's record shape."""

    scenario_key: str
    trial: int
    # "ok" when every sub-goal was judged; "error" when the trial says nothing about the agent.
    status: str
    error: str
    failure: CheckFailure | None = None
    sub_goals: dict[str, bool]
    reasons: dict[str, str]
    transcript: str
    # How long the conversation took; none for a trial that never had one.
    duration_s: float | None = None
    # The agent's own model calls, from the spans it exported: the models they named, how
    # many LLM spans arrived, and what they cost. The cost is none when no span arrived,
    # or when one counted tokens but no cost: an unpriced model is not free.
    models: list[str] = Field(default_factory=list)
    spans_seen: int = 0
    agent_cost_usd: float | None = None
    # The models of its spans that carried no cost: an unpriced model leaves the cost none.
    unpriced_models: list[str] = Field(default_factory=list)
    # The sandbox could not measure one of its model calls: its spans arrived, so none is
    # lost, and its cost is none.
    instrumentation_partial: bool = False
    # The median time from a user turn to the agent's first reply to it; for a voice call,
    # the agent's average latency. None when the conversation does not say.
    reply_latency_ms: float | None = None


class CheckRunStatus(BaseModel):
    run_id: str
    state: str
    finished: bool
    total: int
    # Set when the run itself failed, after its own retries.
    failure: CheckFailure | None = None
    records: list[CheckRecord]
    # The parallelism the run really had, once it has finished.
    parallelism: int | None = None
    # What the fix's limit is held against now, check runs included, so the engine's own
    # budget follows what the platform counts.
    committed_usd: float | None = None
    # The run's spans could not be read: its calls carry no model, cost or span count, and
    # the engine reads the run again before it fails the fix as ours.
    usage_unreadable: bool = False


class VerdictTierPart(BaseModel):
    """One check of a tier that holds several, with its own status."""

    name: str
    # passed | failed | skipped
    status: str
    detail: str = ""


class VerdictTier(BaseModel):
    """One check of the verdict, in the order the engine runs them."""

    name: str
    # passed | failed | pending | skipped
    status: str
    detail: str = ""
    # The checks inside the tier: the ceilings tier holds reply latency and agent cost,
    # so one measured and one not show apart.
    parts: list[VerdictTierPart] = Field(default_factory=list)


class CandidateVerdict(BaseModel):
    accepted: bool
    reason: str
    target_pass_rate: tuple[float, float] | None = None
    held_back_pass_rate: tuple[float, float] | None = None
    must_pass_regressions: list[str] = Field(default_factory=list)
    # Must-pass checks that dropped within the baseline's own noise: shown, not counted.
    must_pass_within_noise: list[str] = Field(default_factory=list)
    tiers: list[VerdictTier] = Field(default_factory=list)


class EvalWarning(BaseModel):
    """A platform eval the winner passes less often than the base version."""

    name: str
    pass_rate: float
    base_pass_rate: float
    trials: int
    base_trials: int


class EvalGrading(StrEnum):
    """How far the platform's evals of a fix's winner are. A fix grades only once it has a
    winner, and its winner ships only once grading is no longer pending."""

    NONE = "none"  # No winner, so nothing is graded.
    PENDING = "pending"
    DONE = "done"
    # A grading job ended without a verdict: the winner can ship, but the evals did not
    # check all of it.
    INCOMPLETE = "incomplete"


class EvalReview(BaseModel):
    grading: EvalGrading
    warnings: list[EvalWarning] = Field(default_factory=list)
    # The platform evals compared, so "none did worse" is said only when some ran.
    compared: int = 0


class CandidateUpdate(BaseModel):
    candidate_id: UUID
    outcome: CandidateOutcome
    verdict: CandidateVerdict | None = None


class FixPlan(BaseModel):
    """The comparison the engine froze: how many scenarios of each kind, and its noise."""

    target: int
    held_back: int
    must_pass: int
    protected: int = 0
    max_candidates: int
    repeats: int
    # How far the current version's pass rate moved between its repeats, in points.
    baseline_spread_pts: int


class FixDiagnosis(BaseModel):
    causes: list[str] = Field(default_factory=list)


class FlakyProtectedCase(BaseModel):
    """A protected case the current version does not pass on every baseline run, so the fix
    could not be held to it: one sub-goal of it, with its baseline passes."""

    key: str
    name: str | None = None
    sub_goal: str
    passed: int = Field(ge=0)
    runs: int = Field(ge=1)


class EngineReport(BaseModel):
    """Progress, and at the end the run's ending. A report with an ending is the last."""

    stage: str = Field(max_length=32)
    step: FixStep | None = None
    plan: FixPlan | None = None
    diagnosis: FixDiagnosis | None = None
    candidates: list[CandidateUpdate] = Field(default_factory=list)
    ending: Ending | None = None
    summary: str = ""
    # With the failed ending: what failed and who can fix it.
    failure: CheckFailure | None = None
    winner_candidate_id: UUID | None = None
    # With the ending: the protected cases the fix could not be held to. An engine from
    # before this field sends none.
    flaky_protected: list[FlakyProtectedCase] = Field(default_factory=list)
    # The models this launch stopped using because their calls were lost, and the last
    # error it could not recover from. The fix keeps both across launches.
    lost_models: list[str] = Field(default_factory=list)
    # The user reads last_error in the failure message: the engine sends one of its
    # fixed sentences (lastErrorOf, workers/agent-fix/util.mjs), never its own text.
    last_error: str | None = None


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
    expires_at: str
    models: EngineModels


class AgentIdentity(BaseModel):
    """The agent version a fix or a run is about, as the user names it."""

    name: str
    # github | archive
    kind: str
    version_id: str
    version_number: int | None
    # Branch and commit of a GitHub agent, e.g. "main @ 0487e25"; None otherwise.
    ref: str | None


class LaunchSourceRun(BaseModel):
    id: str
    number: int | None
    state: str
    finished_at: str | None


class LaunchEnvironment(BaseModel):
    id: str
    name: str
    version: int | None


class LaunchScenario(BaseModel):
    key: str
    name: str
    # True when every judged sub-goal held in every trial; None when nothing was judged.
    passed: bool | None
    failing_sub_goals: list[str]


class LaunchSuggested(BaseModel):
    target_scenario_keys: list[str]
    protected_scenario_keys: list[str]


class LaunchSurface(BaseModel):
    id: Surface
    label: str
    allowed: bool
    reason: str | None = None


class LaunchDefaults(BaseModel):
    surfaces: list[Surface]
    budget_credits: int
    max_minutes: int
    # The models the run's spans show the agent called; empty when they named none.
    allowed_models: list[str]
    # Whether the run's spans exported the agent's model calls, so a fix can measure
    # their cost even when they name no model.
    agent_cost_metered: bool
    # Whether the sandbox could measure none of those calls (another SDK or a direct
    # HTTP request), so their cost is unknown however a fix runs.
    agent_calls_unmeasured: bool = False
    reply_latency_margin_pct: int
    cost_margin_pct: int


class LaunchLimits(BaseModel):
    budget_credits_max: int
    max_minutes_max: int
    # The fewest failing cases a launch takes: fewer could not reach cases_min even if
    # every variation failed.
    scenarios_min: int
    # The failing cases a fix needs; with fewer, it writes variations of each first.
    cases_min: int
    variations_per_case: int
    # The fewest failing cases a fix that writes variations can still end verified from.
    verified_cases_min: int
    scenarios_max: int


class LaunchBlocked(BaseModel):
    code: str
    message: str


class LaunchOptions(BaseModel):
    """Everything the launch form shows and prefills for one finished run."""

    source_run: LaunchSourceRun
    environment: LaunchEnvironment
    agent: AgentIdentity
    scenarios: list[LaunchScenario]
    suggested: LaunchSuggested
    surfaces: list[LaunchSurface]
    defaults: LaunchDefaults
    limits: LaunchLimits
    # Set when Start must be disabled, with the reason launch would give.
    blocked: LaunchBlocked | None = None
    # Set when a launch that writes variations would be refused, with the reason.
    variations_blocked: LaunchBlocked | None = None
