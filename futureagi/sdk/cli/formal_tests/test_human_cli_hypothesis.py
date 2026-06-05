"""
Hypothesis property tests for the human-facing fi-simulate CLI (ADR-035).

Tests individual methods added for the human CLI:
  resolve_name()    — name → UUID resolution from a list of suites
  format_suite_row()— human-readable row from a suite dict
  format_failures() — per-scenario failure summary from results data
  parse_run_arg()   — detects whether argument is UUID or name query

Properties checked:
  1. resolve_name returns exactly one UUID when exactly one suite matches
  2. resolve_name raises on zero matches
  3. resolve_name raises on multiple matches, listing all names
  4. resolve_name is case-insensitive
  5. format_suite_row never raises on arbitrary suite dicts
  6. format_failures only returns items with pass_rate below threshold
  7. parse_run_arg correctly identifies UUIDs vs name strings
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Production functions under test
# (imported lazily so the file can be collected without the full Django stack)
# ---------------------------------------------------------------------------

try:
    from sdk.cli.poll import resolve_name, format_suite_row, format_failures, parse_run_arg
except ImportError:
    pytest.skip("sdk.cli.poll not importable — run with PYTHONPATH=futureagi", allow_module_level=True)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_ "),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip())

_uuid_st = st.uuids().map(str)

_suite_st = st.fixed_dictionaries({
    "id": _uuid_st,
    "name": _name_st,
    "scenario_count": st.integers(min_value=0, max_value=500),
    "last_run_at": st.one_of(st.none(), st.text(max_size=30)),
    "last_pass_rate": st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0, allow_nan=False)),
})

_suites_st = st.lists(_suite_st, min_size=0, max_size=20)

_metric_st = st.fixed_dictionaries({
    "name": _name_st,
    "pass_rate": st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0, allow_nan=False)),
    "score": st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
})


# ---------------------------------------------------------------------------
# Property 1: resolve_name returns one UUID on exactly one match
# ---------------------------------------------------------------------------

class TestResolveName:
    @given(suites=_suites_st, query=_name_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_single_match_returns_uuid(self, suites, query):
        """When exactly one suite name contains the query, resolve_name returns its UUID."""
        assume(query.strip())
        # Build a list where exactly one suite name contains the query.
        # Use .strip() to match resolve_name's normalisation.
        matching = [s for s in suites if query.lower().strip() in s["name"].lower()]
        if len(matching) != 1:
            return  # skip — not the right precondition

        result = resolve_name(suites, query)
        assert result == matching[0]["id"], (
            f"resolve_name returned {result!r}, expected {matching[0]['id']!r}"
        )

    @given(suites=_suites_st, query=_name_st)
    @settings(max_examples=200)
    def test_zero_matches_raises(self, suites, query):
        """Zero matches raises ValueError with helpful message."""
        assume(not any(query.lower().strip() in s["name"].lower() for s in suites))

        with pytest.raises(ValueError, match="no suites match"):
            resolve_name(suites, query)

    @given(suites=_suites_st, query=_name_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_matches_raises_listing_all(self, suites, query):
        """Multiple matches raises ValueError, listing each matching name."""
        matching = [s for s in suites if query.lower().strip() in s["name"].lower()]
        assume(len(matching) > 1)

        with pytest.raises(ValueError) as exc_info:
            resolve_name(suites, query)

        msg = str(exc_info.value)
        assert "ambiguous" in msg.lower() or "multiple" in msg.lower() or str(len(matching)) in msg
        for suite in matching:
            assert suite["name"] in msg, (
                f"Suite name {suite['name']!r} missing from error: {msg!r}"
            )

    @given(suite=_suite_st, query=_name_st)
    @settings(max_examples=150)
    def test_case_insensitive(self, suite, query):
        """resolve_name matches regardless of case difference."""
        assume(query.strip())
        upper_name_suite = {**suite, "name": suite["name"].upper()}
        lower_query = query.lower()

        if lower_query not in upper_name_suite["name"].lower():
            return  # won't match

        result = resolve_name([upper_name_suite], lower_query)
        assert result == upper_name_suite["id"]


# ---------------------------------------------------------------------------
# Property 2: format_suite_row never raises on arbitrary input
# ---------------------------------------------------------------------------

class TestFormatSuiteRow:
    @given(suite=_suite_st, index=st.integers(min_value=1, max_value=999))
    @settings(max_examples=300)
    def test_never_raises(self, suite, index):
        """format_suite_row handles any valid suite dict without raising."""
        result = format_suite_row(suite, index)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(suite=_suite_st, index=st.integers(min_value=1, max_value=999))
    @settings(max_examples=100)
    def test_contains_name(self, suite, index):
        """The formatted row includes the suite name."""
        result = format_suite_row(suite, index)
        assert suite["name"] in result

    @given(suite=_suite_st, index=st.integers(min_value=1, max_value=999))
    @settings(max_examples=100)
    def test_contains_index(self, suite, index):
        """The formatted row includes the numeric index."""
        result = format_suite_row(suite, index)
        assert str(index) in result


# ---------------------------------------------------------------------------
# Property 3: format_failures filters by threshold
# ---------------------------------------------------------------------------

class TestFormatFailures:
    @given(
        metrics=st.lists(_metric_st, min_size=0, max_size=30),
        threshold=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_only_returns_items_below_threshold(self, metrics, threshold):
        """format_failures only includes metrics where pass_rate < threshold."""
        failures = format_failures(metrics, threshold)
        for item in failures:
            pr = item.get("pass_rate")
            if pr is not None:
                assert pr < threshold, (
                    f"format_failures included pass_rate={pr} >= threshold={threshold}"
                )

    @given(
        metrics=st.lists(_metric_st, min_size=1, max_size=10),
        threshold=st.floats(min_value=50.0, max_value=100.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_all_passing_returns_empty(self, metrics, threshold):
        """When all metrics pass, format_failures returns empty list."""
        passing = [{**m, "pass_rate": threshold + 0.1} for m in metrics]
        failures = format_failures(passing, threshold)
        assert failures == [], f"Expected empty, got {failures}"

    @given(
        metrics=st.lists(
            st.fixed_dictionaries({
                "name": _name_st,
                "pass_rate": st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
            }),
            min_size=1, max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_zero_threshold_returns_empty(self, metrics):
        """With threshold=0.0, no metric has pass_rate < 0, so result is always empty."""
        failures = format_failures(metrics, 0.0)
        assert failures == [], f"Expected empty at threshold=0.0, got {failures}"


# ---------------------------------------------------------------------------
# Property 4: parse_run_arg distinguishes UUIDs from name queries
# ---------------------------------------------------------------------------

class TestParseRunArg:
    UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    @given(u=st.uuids())
    @settings(max_examples=200)
    def test_uuid_string_detected_as_uuid(self, u):
        """UUID-shaped strings are detected as direct UUIDs, not name queries."""
        is_uuid, value = parse_run_arg(str(u))
        assert is_uuid is True
        assert value == str(u)

    @given(name=_name_st.filter(lambda s: not re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        s, re.IGNORECASE,
    )))
    @settings(max_examples=200)
    def test_non_uuid_detected_as_name(self, name):
        """Non-UUID strings are detected as name queries."""
        is_uuid, value = parse_run_arg(name)
        assert is_uuid is False
        assert value == name

    @given(name=_name_st)
    @settings(max_examples=100)
    def test_preserves_original_value(self, name):
        """parse_run_arg always returns the original string unchanged."""
        is_uuid, value = parse_run_arg(name)
        assert value == name
