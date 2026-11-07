"""The platform side of the actors plan that needs no database: scenario guards, unmeasured coverage."""

from __future__ import annotations

from simulate.services.harness_scenarios import (
    actor_reference_problems,
    call_actor_entries,
    with_sessions_to_rewrite,
)
from simulate.services.hosted_harness_ingestion import _receipt_evaluation_coverage


def test_a_scenario_naming_an_actor_the_environment_does_not_list_is_refused_wherever_it_names_it():
    doc = {
        "name": "acme",
        "actors": [{"id": "manager"}, {"id": "ghost"}],
        "pre_run": [{"actors": ["priya"], "thread": [{"actor": "omar"}]}],
        "starts_with": {"actor": "bot"},
    }
    assert actor_reference_problems(doc, {"manager", "priya"}) == [
        "acme: bot is not an actor in this environment",
        "acme: ghost is not an actor in this environment",
        "acme: omar is not an actor in this environment",
    ]
    assert actor_reference_problems({"actors": [{"id": "manager"}]}, {"manager"}) == []


def test_a_changed_pre_run_situation_clears_its_thread_and_an_unchanged_one_keeps_it():
    before = {
        "pre_run": [
            {
                "actors": ["a"],
                "about": "Acme wants 20%",
                "thread": [{"text": "hi"}],
                "fingerprint": "f",
            }
        ]
    }
    same = with_sessions_to_rewrite(before, before)
    changed = with_sessions_to_rewrite(
        {"pre_run": [{**before["pre_run"][0], "about": "Acme wants 30%"}]}, before
    )
    assert same["pre_run"][0]["thread"] == [{"text": "hi"}]
    assert (
        changed["pre_run"][0]["thread"] == []
        and changed["pre_run"][0]["fingerprint"] == ""
    )


def test_a_scenario_actor_without_a_trigger_keeps_none_and_keeps_its_knowledge():
    [entry] = call_actor_entries(
        [{"id": "manager", "knowledge": [{"kind": "facts", "text": "x"}]}]
    )
    assert "trigger" not in entry and entry["knowledge"] == [
        {"kind": "facts", "text": "x"}
    ]


def test_an_unmeasured_sub_goal_stays_requested_and_is_never_counted_as_evaluated():
    coverage = _receipt_evaluation_coverage(
        {
            "sub_goals": [
                {"name": "a", "verdict": "pass"},
                {"name": "b", "verdict": "unmeasured"},
                {"name": "c", "held": None},
            ]
        }
    )
    assert coverage == {
        "expected": 3,
        "executed": 1,
        "failed": 1,
        "unmeasured": 1,
        "complete": False,
    }
