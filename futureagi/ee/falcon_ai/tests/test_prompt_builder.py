"""The system prompt anchors relative time windows.

Tool time filters are absolute. Without the current date in the prompt the
model cannot resolve "the last seven days" and guesses at relative syntax
('7D', an invented 'period' field) that the schemas reject.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ee.falcon_ai.prompt_builder import PromptBuilder

FIXED_NOW = datetime(2026, 10, 1, 9, 30, 15, tzinfo=UTC)


@pytest.fixture
def prompt(monkeypatch):
    monkeypatch.setattr("ee.falcon_ai.prompt_builder.timezone.now", lambda: FIXED_NOW)
    return PromptBuilder().build(
        mode="general",
        skill=None,
        memories=[],
        tools=[],
        context=None,
        workspace_name="Default Workspace",
        user_email="qa@futureagi.com",
    )


def test_current_time_is_present_and_exact(prompt):
    assert "2026-10-01T09:30:15Z" in prompt


def test_iso_8601_is_the_stated_format_for_datetime_filters(prompt):
    assert "ISO 8601" in prompt
    assert "started_after" in prompt


@pytest.mark.parametrize("available", [False, True])
def test_usage_overview_is_only_described_when_available(available):
    tools = [SimpleNamespace(name="get_usage_overview")] if available else []
    section = PromptBuilder()._current_time(tools)
    assert ("get_usage_overview" in section) is available
    assert ("YYYY-MM" in section) is available


def test_cost_workflow_uses_portable_tools():
    section = PromptBuilder()._tools([SimpleNamespace(name="list_dashboard_metrics")])
    assert "list_dashboard_metrics → query_dashboard_metrics" in section
    assert "get_usage_overview" not in section


def test_rejected_shorthand_is_named_explicitly(prompt):
    """The three forms seen failing against the live catalog."""
    assert "7d" in prompt or "7D" in prompt
    assert "period" in prompt


def test_time_section_tracks_the_clock(monkeypatch):
    later = datetime(2027, 3, 14, 1, 2, 3, tzinfo=UTC)
    monkeypatch.setattr("ee.falcon_ai.prompt_builder.timezone.now", lambda: later)
    built = PromptBuilder().build(
        mode="general",
        skill=None,
        memories=[],
        tools=[],
        context=None,
        workspace_name="W",
        user_email="e@f.com",
    )
    assert "2027-03-14T01:02:03Z" in built
    assert "2026-10-01" not in built
