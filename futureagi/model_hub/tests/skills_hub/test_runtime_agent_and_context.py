"""Agent/context primitives for the shared API; HTTP wiring is not installed."""

import pytest

from model_hub.services.skills_hub.runtime_usage import (
    adoption_state,
    agent_identity,
    closure_diff,
    library_runtime,
    runtime_diagnosis,
)


@pytest.mark.parametrize(
    "agent,service,name,source",
    [
        ("worker", "api", "worker", "agent"),
        ("", "api", "api", "service"),
        ("", "unknown_service:python", "Unnamed agent in support", "project"),
        ("", "", "Unnamed agent in support", "project"),
    ],
)
def test_agent_display_precedence_keeps_raw_identity(agent, service, name, source):
    row = agent_identity(
        project_id="p", project_name="support", service_name=service, agent_name=agent
    )
    assert row["key"] == ("p", service, agent)
    assert row["name"] == name and row["name_source"] == source


@pytest.mark.parametrize(
    "loads,rules,reason",
    [
        ([], {}, "not_seen"),
        ([{"skill_version_id": "new"}], {}, "got_new"),
        (
            [{"skill_version_id": "old", "requested_version": 3}],
            {},
            "asked_for_version",
        ),
        (
            [{"skill_version_id": "old", "via_context_version_id": "c"}],
            {"c": "pinned"},
            "pinned_in_context",
        ),
        (
            [{"skill_version_id": "old", "via_context_version_id": "c"}],
            {"c": "following"},
            "waiting_for_context_release",
        ),
        (
            [{"skill_version_id": "old", "requested_label": "production"}],
            {},
            "got_old_after_move",
        ),
    ],
)
def test_six_not_moved_reasons(loads, rules, reason):
    result = adoption_state(loads, to_version_id="new", context_rules=rules)
    assert result["not_moved_reason"] == reason
    assert result["share"] == (None if not loads else 1 if reason == "got_new" else 0)


@pytest.mark.parametrize(
    "rules,reason",
    [
        ({"c": "pinned"}, "pinned_in_context"),
        ({"c": "following"}, "waiting_for_context_release"),
        ({}, "got_old_after_move"),
    ],
)
def test_context_snapshot_version_selector_is_not_an_application_pin(rules, reason):
    row = {
        "skill_version_id": "old",
        "via_context_version_id": "c",
        "requested_version": 3,
    }
    assert (
        adoption_state([row], to_version_id="new", context_rules=rules)[
            "not_moved_reason"
        ]
        == reason
    )


def test_mixed_old_selectors_do_not_assert_one_cause_or_all_cached():
    result = adoption_state(
        [
            {"skill_version_id": "new"},
            {"skill_version_id": "old", "requested_version": 3, "cached": True},
            {
                "skill_version_id": "old",
                "requested_label": "production",
                "cached": False,
            },
        ],
        to_version_id="new",
        context_rules={},
    )
    assert result == {
        "share": 1 / 3,
        "not_moved_reason": "got_old_after_move",
        "cached": False,
    }


def test_cached_copy_only_when_old_loads_carry_it():
    result = adoption_state(
        [{"skill_version_id": "old", "cached": True}],
        to_version_id="new",
        context_rules={},
    )
    assert result["cached"] is True


def test_closure_diff_is_identity_based_and_does_not_include_unchanged_versions():
    before = {
        "a": {"name": "first", "version_id": "a1", "version": 1},
        "b": {"name": "removed", "version_id": "b1", "version": 1},
        "same": {"name": "same", "version_id": "s1", "version": 1},
    }
    after = {
        "a": {"name": "first", "version_id": "a2", "version": 2},
        "c": {"name": "added", "version_id": "c1", "version": 1},
        "same": before["same"],
    }
    assert closure_diff(before, after) == [
        {
            "skill_id": "a",
            "name": "first",
            "from_version": 1,
            "to_version": 2,
            "change": "changed",
        },
        {
            "skill_id": "b",
            "name": "removed",
            "from_version": 1,
            "to_version": None,
            "change": "removed",
        },
        {
            "skill_id": "c",
            "name": "added",
            "from_version": None,
            "to_version": 1,
            "change": "added",
        },
    ]


def consumer(**kwargs):
    return {
        "label": "worker SDK",
        "channel": "sdk",
        "sdk_version": "1.0.0",
        "tracing_mode": "on",
        "can_record": False,
        **kwargs,
    }


@pytest.mark.parametrize(
    "has_projects,readable,consumers,loads,state",
    [
        (False, 0, [], 0, "no_tracing_project"),
        (True, 0, [], 0, "no_readable_projects"),
        (True, 1, [], 0, "never_fetched"),
        (True, 1, [consumer(channel="mcp", sdk_version=None)], 0, "non_sdk_fetches"),
        (True, 1, [consumer()], 0, "sdk_cannot_record"),
        (True, 1, [consumer(tracing_mode="off")], 0, "tracing_unavailable"),
        (True, 1, [consumer(tracing_mode="no_otel")], 0, "tracing_unavailable"),
        (True, 1, [consumer(can_record=True)], 0, "awaiting_loads"),
        (True, 1, [consumer(can_record=True)], 1, "recording"),
        (True, 1, [consumer(can_record=True), consumer()], 1, "partially_recording"),
    ],
)
def test_diagnosis_does_not_equate_no_loads_with_unused(
    has_projects, readable, consumers, loads, state
):
    result = runtime_diagnosis(
        has_projects=has_projects,
        readable_projects=readable,
        consumers=consumers,
        loads=loads,
    )
    assert result["state"] == state


def test_diagnosis_preserves_st7_label_but_never_consumer_identity_or_private_fields():
    result = runtime_diagnosis(
        has_projects=True,
        readable_projects=1,
        consumers=[consumer(consumer_key="credential:secret", project_name="hidden")],
        loads=0,
    )
    assert result["consumers"] == [
        {
            "label": "worker SDK",
            "channel": "sdk",
            "sdk_version": "1.0.0",
            "tracing_mode": "on",
            "can_record": False,
        }
    ]


def test_library_distinguishes_unknown_capability_from_measured_zero():
    result = library_runtime(
        skill_ids=["a", "b", "c"],
        counts={"c": 4},
        consumers={
            "a": [consumer()],
            "b": [consumer(can_record=True)],
            "c": [consumer(can_record=True)],
        },
        moves={"c": {"to_version": 2, "verdict": "watching", "hours_left": 2}},
    )
    assert result["a"]["loaded_7d"] is None and result["a"]["can_record"] is False
    assert result["b"]["loaded_7d"] == 0 and result["b"]["can_record"] is True
    assert (
        result["c"]["loaded_7d"] == 4 and result["c"]["move"]["verdict"] == "watching"
    )
