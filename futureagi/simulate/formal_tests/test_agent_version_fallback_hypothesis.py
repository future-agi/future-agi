"""
Hypothesis property tests for the agent_version fallback selection logic (issue #309).

Tests a pure-Python reference implementation of the selection + warning state
machine to ensure that:
  - Pinned path never logs
  - Active fallback always logs with correct payload keys
  - Latest fallback always logs with correct payload keys
  - No-version path never logs
  - Warning payload always contains non-empty resolved_version_id
"""

from dataclasses import dataclass, field
from typing import Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ── Reference implementation ──────────────────────────────────────────────────

@dataclass
class FakeVersion:
    id: str
    version_number: int
    status: str = "active"


@dataclass
class SelectionLog:
    event: str
    extra: dict = field(default_factory=dict)


def resolve_agent_version(
    pinned: Optional[FakeVersion],
    definition_id: Optional[str],
    active_version: Optional[FakeVersion],
    latest_version: Optional[FakeVersion],
) -> tuple[Optional[FakeVersion], list[SelectionLog]]:
    """
    Reference implementation of the fallback logic from test_execution.py.
    Returns (resolved_version, warnings_emitted).
    """
    logs: list[SelectionLog] = []

    if pinned:
        return pinned, logs

    if not definition_id:
        return None, logs

    agent_version = active_version
    if agent_version:
        logs.append(SelectionLog(
            event="simulate_agent_version_fallback_to_active",
            extra={
                "run_test_id": "run-1",
                "agent_definition_id": definition_id,
                "resolved_version_id": str(agent_version.id),
                "resolved_version_number": agent_version.version_number,
            },
        ))
    else:
        agent_version = latest_version
        if agent_version:
            logs.append(SelectionLog(
                event="simulate_agent_version_fallback_to_latest",
                extra={
                    "run_test_id": "run-1",
                    "agent_definition_id": definition_id,
                    "resolved_version_id": str(agent_version.id),
                    "resolved_version_number": agent_version.version_number,
                    "resolved_version_status": agent_version.status,
                },
            ))

    return agent_version, logs


# ── Strategies ────────────────────────────────────────────────────────────────

_version_id = st.text(min_size=1, max_size=36).filter(str.strip)

@st.composite
def fake_version(draw, statuses=None):
    statuses = statuses or ["active", "inactive", "draft"]
    return FakeVersion(
        id=draw(_version_id),
        version_number=draw(st.integers(min_value=1, max_value=999)),
        status=draw(st.sampled_from(statuses)),
    )

opt_version = st.one_of(st.none(), fake_version())
opt_str = st.one_of(st.none(), st.text(min_size=1, max_size=36).filter(str.strip))


# ── Properties ────────────────────────────────────────────────────────────────

@given(pinned=fake_version(), definition_id=opt_str, active=opt_version, latest=opt_version)
def test_pinned_path_never_logs(pinned, definition_id, active, latest):
    """Pinned version bypasses fallback — zero warnings regardless of DB state."""
    _, logs = resolve_agent_version(pinned, definition_id, active, latest)
    assert logs == []


@given(definition_id=opt_str, active=opt_version, latest=opt_version)
def test_no_pinned_no_definition_never_logs(definition_id, active, latest):
    """Without an agent_definition_id, the fallback block is never entered."""
    _, logs = resolve_agent_version(None, None, active, latest)
    assert logs == []


@given(definition_id=_version_id, active=fake_version(), latest=opt_version)
@settings(max_examples=100)
def test_active_fallback_emits_exactly_one_warning(definition_id, active, latest):
    """When ACTIVE version exists, exactly one 'active' warning is emitted."""
    _, logs = resolve_agent_version(None, definition_id, active, latest)
    assert len(logs) == 1
    assert logs[0].event == "simulate_agent_version_fallback_to_active"


@given(definition_id=_version_id, latest=fake_version())
@settings(max_examples=100)
def test_latest_fallback_emits_exactly_one_warning(definition_id, latest):
    """When no ACTIVE version but latest exists, exactly one 'latest' warning is emitted."""
    _, logs = resolve_agent_version(None, definition_id, None, latest)
    assert len(logs) == 1
    assert logs[0].event == "simulate_agent_version_fallback_to_latest"


@given(definition_id=_version_id)
def test_no_version_no_warning(definition_id):
    """No versions available → no warnings, no resolved version."""
    resolved, logs = resolve_agent_version(None, definition_id, None, None)
    assert logs == []
    assert resolved is None


@given(definition_id=_version_id, active=fake_version())
@settings(max_examples=100)
def test_active_warning_payload_has_required_keys(definition_id, active):
    """Active fallback warning payload contains all required diagnostic keys."""
    _, logs = resolve_agent_version(None, definition_id, active, None)
    extra = logs[0].extra
    assert "run_test_id" in extra
    assert "agent_definition_id" in extra
    assert "resolved_version_id" in extra
    assert "resolved_version_number" in extra
    assert extra["resolved_version_id"]  # non-empty


@given(definition_id=_version_id, latest=fake_version())
@settings(max_examples=100)
def test_latest_warning_payload_has_required_keys(definition_id, latest):
    """Latest fallback warning payload contains all required diagnostic keys including status."""
    _, logs = resolve_agent_version(None, definition_id, None, latest)
    extra = logs[0].extra
    assert "run_test_id" in extra
    assert "agent_definition_id" in extra
    assert "resolved_version_id" in extra
    assert "resolved_version_number" in extra
    assert "resolved_version_status" in extra
    assert extra["resolved_version_id"]  # non-empty


@given(definition_id=_version_id, active=fake_version(), latest=opt_version)
@settings(max_examples=100)
def test_active_takes_priority_over_latest(definition_id, active, latest):
    """When both active and latest are present, only the active warning fires."""
    _, logs = resolve_agent_version(None, definition_id, active, latest)
    events = [log.event for log in logs]
    assert "simulate_agent_version_fallback_to_latest" not in events


@given(
    definition_id=_version_id,
    active=opt_version,
    latest=opt_version,
)
@settings(max_examples=200)
def test_at_most_one_warning_ever(definition_id, active, latest):
    """The fallback logic emits at most one warning under all inputs."""
    _, logs = resolve_agent_version(None, definition_id, active, latest)
    assert len(logs) <= 1
