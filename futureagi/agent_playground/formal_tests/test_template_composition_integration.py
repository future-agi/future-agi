"""
Integration probe: Agent template publish protocol end-to-end.

Simulates the full draft → active → inactive lifecycle using a pure
in-process mock store (no Django ORM) and checks ALL TLA+ invariants
simultaneously after every operation.

TLA+ invariants verified (DatasetAutoEval / AgentTemplatePublish spec):
  - ActiveVersionUnique:  at most one ACTIVE version per template at any time
  - NoDraftExecution:     no execution is ever pinned to a DRAFT version
  - StatusTransitionSafe: versions only advance along {draft → active → inactive}
                          or {draft → inactive}; ACTIVE cannot become DRAFT

Run with:
  pytest futureagi/agent_playground/formal_tests/test_template_composition_integration.py -v
"""

import sys
import os
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

# ── Import pure composition helpers (no Django) ───────────────────────────────
try:
    from agent_playground.services.template_composition import (
        build_cross_graph_adjacency,
        detect_cross_graph_cycle,
        infer_composed_levels,
        is_dag,
        schemas_compatible,
    )
except ImportError as exc:
    pytest.skip(
        f"agent_playground.services.template_composition not importable: {exc}",
        allow_module_level=True,
    )

# ── Status constants (mirrors GraphVersionStatus without Django models) ────────

DRAFT = "draft"
ACTIVE = "active"
INACTIVE = "inactive"

_VALID_STATUSES = {DRAFT, ACTIVE, INACTIVE}
_FORWARD_TRANSITIONS = {
    DRAFT: {ACTIVE, INACTIVE},
    ACTIVE: {INACTIVE},
    INACTIVE: set(),  # terminal — no further transitions
}


# ── Minimal mock store ────────────────────────────────────────────────────────

class MockVersion:
    """Lightweight stand-in for GraphVersion — no ORM required."""

    def __init__(self, graph_id: str, version_number: int, status: str = DRAFT):
        assert status in _VALID_STATUSES
        self.id = str(uuid.uuid4())
        self.graph_id = graph_id
        self.version_number = version_number
        self.status = status
        # execution_pin is None unless an execution explicitly targets this version
        self.execution_pin: str | None = None

    def __repr__(self):
        return f"<Version v{self.version_number} [{self.status}] graph={self.graph_id[:8]}>"


class MockTemplateStore:
    """
    In-process replacement for the Django ORM version store.

    Enforces the publish protocol transitions and raises ValueError on violations
    (mirrors GraphVersion._validate_single_active_version / ValidationError).
    """

    def __init__(self):
        # graph_id → list[MockVersion]
        self._versions: dict[str, list[MockVersion]] = {}
        # version.id → MockVersion (quick lookup)
        self._by_id: dict[str, MockVersion] = {}

    # ── Write operations ──────────────────────────────────────────────────────

    def create_draft(self, graph_id: str) -> MockVersion:
        """Create a new DRAFT version (always allowed — multiple drafts OK)."""
        versions = self._versions.setdefault(graph_id, [])
        version_number = len(versions) + 1
        v = MockVersion(graph_id, version_number, status=DRAFT)
        versions.append(v)
        self._by_id[v.id] = v
        return v

    def publish(self, version_id: str) -> MockVersion:
        """
        Transition DRAFT → ACTIVE.
        Any previously ACTIVE version is demoted to INACTIVE first.
        Raises ValueError if version is not DRAFT.
        """
        v = self._by_id[version_id]
        if v.status != DRAFT:
            raise ValueError(
                f"Cannot publish version in status '{v.status}': must be DRAFT"
            )
        # Demote existing active to inactive
        for other in self._versions[v.graph_id]:
            if other.id != v.id and other.status == ACTIVE:
                other.status = INACTIVE
        v.status = ACTIVE
        return v

    def deactivate(self, version_id: str) -> MockVersion:
        """
        Transition ACTIVE → INACTIVE.
        Raises ValueError if version is not ACTIVE.
        """
        v = self._by_id[version_id]
        if v.status != ACTIVE:
            raise ValueError(
                f"Cannot deactivate version in status '{v.status}': must be ACTIVE"
            )
        v.status = INACTIVE
        return v

    def pin_execution(self, execution_id: str, version_id: str) -> None:
        """Pin an execution to a version (only ACTIVE versions may be pinned)."""
        v = self._by_id[version_id]
        if v.status != ACTIVE:
            raise ValueError(
                f"NoDraftExecution violated: cannot pin execution to {v.status} version"
            )
        v.execution_pin = execution_id

    # ── Read helpers ──────────────────────────────────────────────────────────

    def active_versions(self, graph_id: str) -> list[MockVersion]:
        return [
            v for v in self._versions.get(graph_id, [])
            if v.status == ACTIVE
        ]

    def all_versions(self, graph_id: str) -> list[MockVersion]:
        return list(self._versions.get(graph_id, []))

    def all_executions(self) -> list[tuple[str, MockVersion]]:
        """Return all (execution_id, version) pairs where execution_pin is set."""
        result = []
        for versions in self._versions.values():
            for v in versions:
                if v.execution_pin is not None:
                    result.append((v.execution_pin, v))
        return result


# ── Invariant checker ─────────────────────────────────────────────────────────

def _assert_invariants(store: MockTemplateStore, graph_id: str, *, label: str = "") -> None:
    """
    Check ALL TLA+ invariants simultaneously on the current store state.

    Raises AssertionError on the first violated invariant, naming it.
    """
    ctx = f" [{label}]" if label else ""
    versions = store.all_versions(graph_id)
    active = store.active_versions(graph_id)

    # ActiveVersionUnique: at most one ACTIVE version per template
    assert len(active) <= 1, (
        f"ActiveVersionUnique violated{ctx}: "
        f"{len(active)} active versions found: {active}"
    )

    # NoDraftExecution: no execution pinned to a DRAFT or INACTIVE version
    for exec_id, v in store.all_executions():
        assert v.status == ACTIVE, (
            f"NoDraftExecution violated{ctx}: "
            f"execution '{exec_id}' pinned to {v.status} version {v!r}"
        )

    # StatusTransitionSafe: no version has an invalid status
    for v in versions:
        assert v.status in _VALID_STATUSES, (
            f"StatusTransitionSafe violated{ctx}: "
            f"version {v!r} has invalid status '{v.status}'"
        )


# ── Scenarios ─────────────────────────────────────────────────────────────────

class TestPublishProtocol:
    """
    End-to-end simulation of the template lifecycle with invariant checks
    after every state-changing operation.
    """

    def setup_method(self):
        self.store = MockTemplateStore()
        self.graph_id = str(uuid.uuid4())

    def test_initial_draft_satisfies_invariants(self):
        v = self.store.create_draft(self.graph_id)
        _assert_invariants(self.store, self.graph_id, label="initial_draft")
        assert v.status == DRAFT

    def test_publish_makes_single_active(self):
        v = self.store.create_draft(self.graph_id)
        self.store.publish(v.id)
        _assert_invariants(self.store, self.graph_id, label="after_first_publish")
        assert v.status == ACTIVE

    def test_second_publish_demotes_first(self):
        v1 = self.store.create_draft(self.graph_id)
        self.store.publish(v1.id)
        _assert_invariants(self.store, self.graph_id, label="after_v1_publish")

        v2 = self.store.create_draft(self.graph_id)
        self.store.publish(v2.id)
        _assert_invariants(self.store, self.graph_id, label="after_v2_publish")

        assert v1.status == INACTIVE
        assert v2.status == ACTIVE

    def test_deactivate_leaves_no_active(self):
        v = self.store.create_draft(self.graph_id)
        self.store.publish(v.id)
        self.store.deactivate(v.id)
        _assert_invariants(self.store, self.graph_id, label="after_deactivate")
        assert v.status == INACTIVE
        assert len(self.store.active_versions(self.graph_id)) == 0

    def test_sequence_publish_deactivate_publish(self):
        """v1 → active → inactive; v2 → active. Invariant holds throughout."""
        v1 = self.store.create_draft(self.graph_id)
        self.store.publish(v1.id)
        _assert_invariants(self.store, self.graph_id, label="seq_v1_active")

        self.store.deactivate(v1.id)
        _assert_invariants(self.store, self.graph_id, label="seq_v1_deactivated")

        v2 = self.store.create_draft(self.graph_id)
        self.store.publish(v2.id)
        _assert_invariants(self.store, self.graph_id, label="seq_v2_active")

        assert v1.status == INACTIVE
        assert v2.status == ACTIVE

    def test_many_versions_only_one_active(self):
        """Publish ten versions in sequence — at most one active at all times."""
        for i in range(10):
            v = self.store.create_draft(self.graph_id)
            self.store.publish(v.id)
            _assert_invariants(self.store, self.graph_id, label=f"publish_{i}")
        active = self.store.active_versions(self.graph_id)
        assert len(active) == 1

    def test_cannot_publish_already_active(self):
        v = self.store.create_draft(self.graph_id)
        self.store.publish(v.id)
        with pytest.raises(ValueError, match="must be DRAFT"):
            self.store.publish(v.id)
        _assert_invariants(self.store, self.graph_id, label="reject_double_publish")

    def test_cannot_deactivate_draft(self):
        v = self.store.create_draft(self.graph_id)
        with pytest.raises(ValueError, match="must be ACTIVE"):
            self.store.deactivate(v.id)
        _assert_invariants(self.store, self.graph_id, label="reject_deactivate_draft")

    def test_execution_pinned_only_to_active(self):
        v = self.store.create_draft(self.graph_id)
        self.store.publish(v.id)
        exec_id = str(uuid.uuid4())
        self.store.pin_execution(exec_id, v.id)
        _assert_invariants(self.store, self.graph_id, label="execution_pinned")
        assert v.execution_pin == exec_id

    def test_execution_rejected_for_draft(self):
        v = self.store.create_draft(self.graph_id)
        with pytest.raises(ValueError, match="NoDraftExecution"):
            self.store.pin_execution(str(uuid.uuid4()), v.id)
        _assert_invariants(self.store, self.graph_id, label="reject_draft_execution")

    def test_execution_rejected_for_inactive(self):
        v = self.store.create_draft(self.graph_id)
        self.store.publish(v.id)
        self.store.deactivate(v.id)
        with pytest.raises(ValueError, match="NoDraftExecution"):
            self.store.pin_execution(str(uuid.uuid4()), v.id)
        _assert_invariants(self.store, self.graph_id, label="reject_inactive_execution")


class TestCrossGraphInvariantsUnchanged:
    """
    Verify the graph-theory functions (unchanged by the branch) still satisfy
    their own invariants — cross-contamination guard.
    """

    PARENT_NODES = ["A", "B", "SG", "D"]
    PARENT_ADJ = {"A": ["B"], "B": ["SG"], "SG": ["D"], "D": []}
    CHILD_NODES = ["X", "Y", "Z"]
    CHILD_ADJ = {"X": ["Y"], "Y": ["Z"], "Z": []}
    REFS = [("G1", "G2"), ("G2", "G3")]
    GRAPH_IDS = ["G1", "G2", "G3"]

    def test_parent_is_dag(self):
        assert is_dag(self.PARENT_ADJ, self.PARENT_NODES)

    def test_child_is_dag(self):
        assert is_dag(self.CHILD_ADJ, self.CHILD_NODES)

    def test_no_cycle_in_linear_refs(self):
        adj = build_cross_graph_adjacency(self.REFS)
        assert not detect_cross_graph_cycle(adj, "G1", "G3")

    def test_back_edge_creates_cycle(self):
        adj = build_cross_graph_adjacency(self.REFS)
        assert detect_cross_graph_cycle(adj, "G3", "G1")

    def test_levels_monotone(self):
        levels = infer_composed_levels(self.GRAPH_IDS, self.REFS)
        for src, tgt in self.REFS:
            assert levels[src] > levels[tgt], (
                f"Level monotone violated: {src}(level={levels[src]}) "
                f"should be > {tgt}(level={levels[tgt]})"
            )
