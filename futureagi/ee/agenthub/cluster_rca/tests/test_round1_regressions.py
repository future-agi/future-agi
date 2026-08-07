"""Round-1 correctness regression tests for the cluster-RCA agent.

These pin four design fixes baked into ``agent.py``. Each test is written so
it FAILS if the corresponding fix is reverted:

  (A) alias filters         — _resolve_filter_aliases maps id-valued labels
                              (T01/Sess01/Ver01) back to UUIDs before the
                              canonical CH filter; non-id keys pass through.
  (B) cross-scoping rollup  — _agg_trace_count(eval_metric) counts eval ROWS
                              for the denominator, not trace count (a trace
                              can carry many eval results → pct would exceed
                              100% with a trace-count denominator).
  (C) session-cluster       — _cluster_trace_uuids fans session members out to
                              their traces; _eval_scope_q ORs in session-target
                              eval rows (which have NO trace FK).
  (D) dead-air backstop     — _fallback_synthesis always yields a non-null
                              LOW-confidence synthesis with no LLM call;
                              submit_synthesis rejects empty synthesis text.

All tests run without a DB: the agent is built via ``__new__`` (bypassing the
DB/gateway __init__) and any ORM access is mocked.
"""

from unittest.mock import MagicMock, patch

from django.db.models import Q

from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent
from ee.agenthub.cluster_rca.constants import (
    CLUSTER_RCA_COST_CEILING_USD,
    CLUSTER_RCA_MAX_TURNS,
    Confidence,
    FindingType,
)
from ee.agenthub.cluster_rca.types import ClusterFinding


def _bare_agent() -> ClusterAnalysisAgent:
    """An agent with only the attributes these unit tests touch.

    ``__init__`` needs a DB lookup + a live gateway client, so bypass it and
    wire up just the alias map / cluster identity the methods under test read.
    """
    agent = ClusterAnalysisAgent.__new__(ClusterAnalysisAgent)
    agent.project_id = "proj-1"
    agent.cluster_uuid = "cluster-uuid-1"
    agent.cluster_label = "E-CLUSTER1"
    agent.cluster_id = "E-CLUSTER1"
    agent._alias_to_uuid = {}
    agent._uuid_to_alias = {}
    agent._cluster_session_uuids_cache = {}
    agent._cluster_trace_uuids_cache = {}
    agent.findings = []
    agent.synthesis = None
    agent._investigation_complete = False
    agent.on_event = lambda *a, **k: None
    return agent


# ---------------------------------------------------------------------------
# (A) ALIAS FILTERS — _resolve_filter_aliases
# ---------------------------------------------------------------------------
class TestAliasFilterResolution:
    SESSION_UUID = "11111111-1111-1111-1111-111111111111"
    TRACE_UUID = "22222222-2222-2222-2222-222222222222"

    def _agent(self) -> ClusterAnalysisAgent:
        agent = _bare_agent()
        agent._alias_to_uuid = {
            "Sess01": self.SESSION_UUID,
            "T01": self.TRACE_UUID,
        }
        return agent

    def test_scalar_id_label_resolves_to_uuid(self):
        out = self._agent()._resolve_filter_aliases({"session_id": "Sess01"})
        # Revert (identity passthrough) leaves the label; CH would match nothing.
        assert out["session_id"] == self.SESSION_UUID

    def test_list_id_labels_resolve(self):
        out = self._agent()._resolve_filter_aliases(
            {"trace_id": ["T01", self.TRACE_UUID]}
        )
        assert out["trace_id"] == [self.TRACE_UUID, self.TRACE_UUID]

    def test_dsl_dict_op_resolves(self):
        out = self._agent()._resolve_filter_aliases(
            {"session_id": {"in": ["Sess01"]}}
        )
        assert out["session_id"] == {"in": [self.SESSION_UUID]}

    def test_eq_op_resolves(self):
        out = self._agent()._resolve_filter_aliases(
            {"trace_id": {"eq": "T01"}}
        )
        assert out["trace_id"] == {"eq": self.TRACE_UUID}

    def test_non_id_key_passes_through_untouched(self):
        # An attribute filter is NOT an id-valued key — must not be rewritten.
        flt = {"attr.model": "gpt-4o", "status": {"eq": "ERROR"}}
        out = self._agent()._resolve_filter_aliases(flt)
        assert out == flt

    def test_unknown_label_left_as_is(self):
        # Unknown, non-UUID label survives verbatim → CH returns empty, no crash.
        out = self._agent()._resolve_filter_aliases({"session_id": "Sess99"})
        assert out["session_id"] == "Sess99"


# ---------------------------------------------------------------------------
# (B) CROSS-SCOPING ROLLUP — _agg_trace_count(eval_metric)
# ---------------------------------------------------------------------------
class TestEvalMetricDenominator:
    def test_denominator_is_eval_row_count_not_trace_count(self):
        agent = _bare_agent()
        # One trace carries TWO eval results → 2 eval rows, 1 trace.
        trace_uuids = ["trace-A"]
        # Post-selector bucket form (count_cluster_eval_metrics).
        rows = [
            {"key": "toxicity", "count": 1},
            {"key": "groundedness", "count": 1},
        ]
        agent._cluster_session_uuids = lambda _c: []  # no session fan-out here

        with patch(
            "ee.agenthub.cluster_rca.agent.selectors.count_cluster_eval_metrics",
            return_value=rows,
        ):
            buckets, total = agent._agg_trace_count(
                trace_uuids, agent.cluster_uuid, "eval_metric"
            )

        # Reverting to a trace-count denominator (len(trace_uuids) == 1) would
        # make total=1 and push each bucket's pct above 100%.
        assert total == 2  # eval-row count, not the single trace
        assert total == sum(b["count"] for b in buckets)
        assert total != len(trace_uuids)
        # Each bucket stays a valid percentage under the eval-row denominator.
        for b in buckets:
            assert b["count"] / total * 100 <= 100.0


# ---------------------------------------------------------------------------
# (C) SESSION-CLUSTER EXPANSION — _eval_scope_q + _cluster_trace_uuids
# ---------------------------------------------------------------------------
class TestEvalScopeQ:
    def test_session_cluster_ors_in_session_eval_rows(self):
        agent = _bare_agent()
        session_uuids = ["sess-1", "sess-2"]
        agent._cluster_session_uuids = lambda _c: session_uuids

        q = agent._eval_scope_q("cluster-uuid-1", ["trace-1"])

        # Reverting to a trace-only filter drops the session-target eval rows
        # (trace FK NULL) — the single most important signal for a session
        # cluster. Pin both legs of the OR.
        expected = Q(trace_id__in=["trace-1"]) | Q(
            trace_session_id__in=session_uuids
        )
        assert str(q) == str(expected)

    def test_non_session_cluster_is_trace_only(self):
        agent = _bare_agent()
        agent._cluster_session_uuids = lambda _c: []

        q = agent._eval_scope_q("cluster-uuid-1", ["trace-1"])
        assert str(q) == str(Q(trace_id__in=["trace-1"]))


class TestClusterTraceFanout:
    def test_session_members_fan_out_to_their_traces(self):
        agent = _bare_agent()
        # Junction rows for a session cluster carry trace=NULL — the direct
        # trace pull is empty; the blast radius must come from the sessions'
        # member traces, resolved through ClickHouse (collector sessions have no
        # PG Trace row, so a PG session_id join would resolve nothing).
        agent._cluster_session_uuids = lambda _c: ["sess-1"]

        # get_reader().session_trace_ids(project_id, sid) → the session's traces.
        member_traces = ["trace-x", "trace-y", "trace-z"]
        reader = MagicMock()
        reader.session_trace_ids.return_value = member_traces
        cm = MagicMock()
        cm.__enter__.return_value = reader
        cm.__exit__.return_value = False

        with (
            # ErrorClusterTraces junction → no direct trace members.
            patch(
                "ee.agenthub.cluster_rca.agent.selectors.cluster_member_trace_ids",
                return_value=[],
            ),
            patch("ee.agenthub.cluster_rca.agent.get_reader", return_value=cm),
        ):
            out = agent._cluster_trace_uuids("cluster-uuid-1")

        # Session members fan out to their traces via CH (NOT a PG Trace walk).
        reader.session_trace_ids.assert_called_once_with("proj-1", "sess-1")
        assert set(out) == set(member_traces)


# ---------------------------------------------------------------------------
# (D) DEAD-AIR BACKSTOP — _fallback_synthesis + submit_synthesis empty reject
# ---------------------------------------------------------------------------
class TestDeadAirBackstop:
    def test_fallback_with_findings_is_low_confidence_non_null(self):
        agent = _bare_agent()
        agent.findings = [
            ClusterFinding(
                finding_type=FindingType.FAILURE_MODE,
                title="Tool call returns 500 on retry",
                description="Repeated 500s after the second retry.",
                confidence=Confidence.MEDIUM,
                evidence_trace_ids=["trace-1", "trace-2"],
            )
        ]
        agent._fallback_synthesis()

        # Reverting the backstop leaves synthesis None → stream dead-air.
        assert agent.synthesis is not None
        assert agent.synthesis.confidence == Confidence.LOW
        assert agent.synthesis.synthesis.strip()
        assert agent.synthesis.evidence_trace_ids == ["trace-1", "trace-2"]
        assert agent._investigation_complete is True

    def test_fallback_with_no_findings_still_non_null(self):
        agent = _bare_agent()
        agent.findings = []
        agent._fallback_synthesis()

        assert agent.synthesis is not None
        assert agent.synthesis.confidence == Confidence.LOW
        assert agent.synthesis.synthesis.strip()
        assert agent._investigation_complete is True

    def test_submit_synthesis_rejects_empty_text(self):
        agent = _bare_agent()
        result = agent._tool_submit_synthesis(synthesis="   ", confidence="H")

        # Reverting the empty-text guard would end the run on a blank answer.
        assert result.get("is_error") is True
        assert agent.synthesis is None
        assert agent._investigation_complete is False

    def test_submit_synthesis_rejects_missing_text(self):
        agent = _bare_agent()
        result = agent._tool_submit_synthesis(confidence="H")
        assert result.get("is_error") is True
        assert agent.synthesis is None
        assert agent._investigation_complete is False

    def test_force_synthesis_falls_back_when_llm_emits_nothing(self):
        # When the forced submit_synthesis LLM call returns no tool call, the
        # run must STILL end on a deterministic LOW-confidence synthesis.
        agent = _bare_agent()
        agent.findings = []
        agent.model = "m"
        agent.temperature = 0.0
        agent.max_tokens = 256
        agent.thinking_budget = None
        agent.total_cost_usd = 0.0
        agent._gateway_client = MagicMock()

        fake_choice = MagicMock()
        fake_choice.message.tool_calls = []  # model declined to call the tool
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_raw = MagicMock()
        fake_raw.cost_usd = 0.0
        fake_raw.response = fake_response

        with patch(
            "ee.agenthub.cluster_rca.agent.call_llm_raw", return_value=fake_raw
        ):
            agent._force_synthesis(messages=[], turn=5)

        assert agent.synthesis is not None
        assert agent.synthesis.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# TENANT SCOPE — _resolve_scoped_trace_uuids pins to self.cluster_uuid
# ---------------------------------------------------------------------------
class TestTenantPinnedScope:
    """The per-call ``filter.cluster_id`` is required by the tool schema but
    NEVER honored for scope — a single-cluster agent always scopes to its own
    init-validated cluster, so a foreign id pasted into a filter can't widen the
    blast radius. Reverting the pin (resolving filter.cluster_id via the alias
    map, which passes raw UUIDs through) fails these."""

    def test_llm_supplied_cluster_id_is_ignored_and_pinned_to_self(self):
        agent = _bare_agent()  # cluster_uuid = "cluster-uuid-1"
        seen = {}
        agent._cluster_trace_uuids = lambda cu: seen.setdefault("cu", cu) or []

        agent._resolve_scoped_trace_uuids({"cluster_id": "FOREIGN-UUID-9999"})

        # Scoped to the agent's own cluster, NOT the model-supplied foreign id.
        assert seen["cu"] == "cluster-uuid-1"

    def test_internal_caller_may_scope_to_an_explicit_cluster(self):
        # Internal callers (the read(cluster) telemetry manifest) pass an already
        # project-scoped cluster_uuid explicitly — that path is preserved.
        agent = _bare_agent()
        seen = {}
        agent._cluster_trace_uuids = lambda cu: seen.setdefault("cu", cu) or []

        agent._resolve_scoped_trace_uuids(
            {"cluster_id": "X"}, cluster_uuid="other-in-project"
        )

        assert seen["cu"] == "other-in-project"

    def test_missing_cluster_id_still_rejected(self):
        agent = _bare_agent()
        _uuids, err = agent._resolve_scoped_trace_uuids({})
        assert err is not None and err.get("is_error") is True


# ---------------------------------------------------------------------------
# COST CEILING — the loop is bounded by spend, not only turns
# ---------------------------------------------------------------------------
class TestCostCeiling:
    """A run already past the cost ceiling stops on turn 0 (before any LLM
    round-trip) and routes to the force-synthesis backstop — so a pathological
    cluster can't burn all 18 turns at unbounded per-turn cost."""

    def test_loop_stops_at_ceiling_before_any_turn(self):
        agent = _bare_agent()
        agent.max_turns = CLUSTER_RCA_MAX_TURNS
        agent._stop_event = None
        agent.question = None
        agent.total_cost_usd = CLUSTER_RCA_COST_CEILING_USD + 1.0  # already over
        agent._initial_messages = lambda: []  # skip the DB/CH context read

        forced = {}
        # Record the backstop call without salvaging a synthesis, so
        # terminated_reason stays "cost_ceiling" (vs. "synthesis_forced").
        agent._force_synthesis = lambda messages, turn: forced.setdefault("turn", turn)

        result = agent.run()

        # Broke on the FIRST turn (cost gate), not after exhausting all 18.
        assert forced.get("turn") == 1
        assert result.terminated_reason == "cost_ceiling"
        assert result.cost_usd >= CLUSTER_RCA_COST_CEILING_USD
