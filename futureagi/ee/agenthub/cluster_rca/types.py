"""Typed result containers for the cluster RCA agent.

No raw dicts at function boundaries — dataclasses everywhere.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, TypedDict

from ee.agenthub.cluster_rca.constants import Confidence, FindingType


class CountBucket(TypedDict):
    """One ``{key, count}`` row from a selector aggregate (group-by rollup).

    The typed contract crossing the selector→agent boundary: a key rename is a
    type error, not a silent ``KeyError`` at read time. ``key`` is nullable — a
    group-by over a nullable column (e.g. an eval-config name) yields a ``None``
    bucket.
    """

    key: str | None
    count: int


@dataclass
class ClusterFinding:
    """A single observation the agent surfaced during investigation.

    Multiple findings can accumulate per run — distinct failure modes, evidence
    pointers, behavioral deltas. The synthesis ties them together.
    """

    finding_type: FindingType
    title: str
    description: str
    confidence: Confidence
    evidence_trace_ids: list[str] = field(default_factory=list)
    evidence_span_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ClusterSynthesis:
    """The terminal output — 2-sentence synthesis + 1-sentence fix.

    Written by the agent via the submit_synthesis terminal tool, which ends the
    investigation loop.
    """

    synthesis: str  # 2 sentences: what's broken
    fix: str  # 1 sentence: what to do
    confidence: Confidence
    evidence_trace_ids: list[str] = field(default_factory=list)
    # 2-3 grounded follow-up questions for the Analyze tab's "Try asking" set.
    suggested_questions: list[str] = field(default_factory=list)


@dataclass
class ClusterAnalysisResult:
    """Full output of a ClusterAnalysisAgent.run() invocation."""

    cluster_id: str
    synthesis: Optional[ClusterSynthesis] = None
    findings: list[ClusterFinding] = field(default_factory=list)
    turn_count: int = 0
    terminated_reason: str = ""  # "synthesis_submitted" | "max_turns" | "error" | "stop"
    error: Optional[str] = None
    cost_usd: float = 0.0


# --- WebSocket event contract --------------------------------------------------
# §10 of the contract: the bridge streams a fixed set of frames to the Analyze
# tab over Falcon's socket. The wire shape is always ``{"type": <str>, "data":
# {...}}``. Each frame below is a frozen dataclass whose fields are exactly the
# ``data`` payload; ``to_frame()`` renders the full envelope. Constructing these
# at the bridge's emit sites (instead of hand-rolled dicts) makes the contract a
# single source of truth the FE and a contract test can both pin.


@dataclass(frozen=True)
class _WsEvent:
    """Base for streamed WS frames. Subclasses set ``TYPE`` and add data fields."""

    TYPE: str = field(init=False, default="")

    def to_frame(self) -> dict:
        """Render the ``{"type", "data"}`` envelope sent over the socket."""
        data = {k: v for k, v in asdict(self).items() if k != "TYPE"}
        return {"type": self.TYPE, "data": data}


@dataclass(frozen=True)
class RcaStatusEvent(_WsEvent):
    """Lightweight pre-LLM progress ping (not persisted to the trail)."""

    message_id: str
    phase: Optional[str] = None
    detail: Optional[str] = None
    TYPE: str = field(init=False, default="rca_status")


@dataclass(frozen=True)
class RcaReasoningEvent(_WsEvent):
    """Per-turn native thinking + verbalized text."""

    message_id: str
    turn: Optional[int] = None
    reasoning: Optional[str] = None
    content: Optional[str] = None
    TYPE: str = field(init=False, default="rca_reasoning")


@dataclass(frozen=True)
class RcaStepStartEvent(_WsEvent):
    """A tool call is about to run. ``args`` is an open tool-dependent map."""

    message_id: str
    call_id: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None
    turn: Optional[int] = None
    TYPE: str = field(init=False, default="rca_step_start")


@dataclass(frozen=True)
class RcaStepResultEvent(_WsEvent):
    """A tool call finished. ``result`` is the raw tool handler return."""

    message_id: str
    call_id: Optional[str] = None
    tool: Optional[str] = None
    result: Any = None
    turn: Optional[int] = None
    TYPE: str = field(init=False, default="rca_step_result")


@dataclass(frozen=True)
class RcaSynthesisEvent(_WsEvent):
    """Terminal synthesis: root cause + fix + grounding."""

    message_id: str
    synthesis: Optional[str] = None
    fix: Optional[str] = None
    confidence: Optional[str] = None
    evidence_trace_ids: list[str] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)
    TYPE: str = field(init=False, default="rca_synthesis")


@dataclass(frozen=True)
class RcaErrorEvent(_WsEvent):
    """A mid-run or run-failed error surfaced to the FE."""

    message_id: str
    error: str = "Cluster analysis failed"
    TYPE: str = field(init=False, default="error")
