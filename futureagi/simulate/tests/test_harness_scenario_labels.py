from __future__ import annotations

from simulate.services.harness_scenarios import field_catalogue, level_labels_for


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def values(self, *paths):
        return self._rows


def test_a_background_reads_as_what_the_caller_is_heard_over():
    labels = level_labels_for(
        [
            {"background_noise": "transit", "coverage": {"interface": "noisy_line"}},
            {"background_noise": "quiet line", "coverage": {"overlay": "none"}},
        ]
    )

    assert labels["transit"] == "Airport / station"
    assert labels["quiet line"] == "Off"
    assert labels["none"] == "No attack"


def test_a_coverage_level_keeps_its_own_label_when_it_shares_a_background_name():
    labels = level_labels_for(
        [{"background_noise": "retail", "coverage": {"disposition": "retail"}}]
    )

    assert labels["retail"] == "Retail"


def test_voice_only_fields_are_not_offered_for_a_chat_agent():
    rows = _Rows([{"persona__accent": "Indian", "background_noise": "office", "use_case": "billing"}])

    spoken = {field["value"] for field in field_catalogue(rows, spoken=True)}
    typed = {field["value"] for field in field_catalogue(rows, spoken=False)}

    assert {"persona.accent", "background_noise"} <= spoken
    assert not {"persona.accent", "background_noise"} & typed
    assert "use_case" in typed


def test_a_call_offers_no_turn_budget_and_a_chat_no_voice_fields():
    from simulate.services.harness_provider import HostedHarnessProvider

    spoken = HostedHarnessProvider._editing_contract(HostedHarnessProvider, spoken=True)
    typed = HostedHarnessProvider._editing_contract(HostedHarnessProvider, spoken=False)

    assert "max_turns" not in spoken["editable_fields"]
    assert "background_noise" in spoken["editable_fields"] and "accent" in spoken["persona_fields"]
    assert "max_turns" in typed["editable_fields"]
    assert "background_noise" not in typed["editable_fields"] and "accent" not in typed["persona_fields"]


def test_the_contract_serves_each_editable_persona_fields_choices():
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.persona import Persona
    from simulate.services.harness_provider import HostedHarnessProvider

    spoken = HostedHarnessProvider._editing_contract(HostedHarnessProvider, spoken=True)
    typed = HostedHarnessProvider._editing_contract(HostedHarnessProvider, spoken=False)

    assert set(spoken["persona_choices"]) == set(spoken["persona_fields"])
    accents = [value for value, _ in Persona.AccentChoices.choices]
    assert spoken["persona_choices"]["accent"] == accents
    assert "accent" not in typed["persona_choices"]
    assert spoken["persona_choices"]["languages"] == list(
        AgentDefinition.LanguageChoices.labels
    )


def test_a_call_reports_no_turn_budget_among_its_end_conditions():
    from types import SimpleNamespace

    from simulate.services.harness_environment import _end_conditions

    docs = [{"max_turns": 5}, {"max_turns": 9}]
    call = SimpleNamespace(payload={"agent": {"connector": "phone"}, "runtime": {}})
    chat = SimpleNamespace(payload={"agent": {"connector": "http"}, "runtime": {}})

    assert _end_conditions(call, {}, docs)["max_turns"] is None
    assert _end_conditions(chat, {}, docs)["max_turns"] == 9


def test_a_chat_reads_its_axes_in_a_chat_s_words():
    from simulate.services.harness_scenarios import axis_label

    assert axis_label("interaction") == "How the call goes"
    assert axis_label("interaction", spoken=False) == "How the chat goes"
    assert axis_label("counterparty", spoken=False) == "Who is asking"
    assert axis_label("task", spoken=False) == axis_label("task")


def test_filter_choices_off_the_page_are_labelled_too():
    labels = level_labels_for(
        [],
        [
            {"value": "background_noise", "choices": ["quiet line", "street"]},
            {"value": "coverage.overlay", "choices": ["none"]},
        ],
    )

    assert labels["quiet line"] == "Off"
    assert labels["street"] == "Street"
    assert labels["none"] == "No attack"


def test_a_scenario_sits_under_every_sub_goal_it_carries():
    from simulate.services.harness_scenarios import group_counts, grouped

    rows = grouped(
        [
            {"name": "a", "number": 1, "sub_goals": ["greets", "books"]},
            {"name": "b", "number": 2, "sub_goals": ["books"]},
            {"name": "c", "number": 3, "sub_goals": []},
        ],
        "sub_goal",
    )
    suite = _Rows([["greets", "books"], ["books"], [], ["greets"]])
    suite.values_list = lambda *paths, flat=False: suite._rows

    assert [(row["group"], row["name"]) for row in rows] == [
        ("Ungrouped", "c"), ("books", "a"), ("books", "b"), ("greets", "a")
    ]
    sections = group_counts(rows, suite, "sub_goal")
    totals = {one["name"]: one["total"] for one in sections}
    assert totals == {"Ungrouped": 1, "books": 2, "greets": 2}


def test_the_scenarios_list_and_coverage_contracts_carry_the_served_shape():
    from simulate.serializers.harness_job import (
        HarnessScenarioAmendResponseSerializer,
        HarnessScenarioCoverageResponseSerializer,
        HarnessScenarioListResponseSerializer,
    )
    from simulate.services.harness_provider import HostedHarnessProvider

    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "scenario_id": None,
        "scenario_key": "a",
        "number": 1,
        "name": "a",
        "use_case": "Book a ride",
        "instruction": "Call and book.",
        "branch": "",
        "tests": "books the ride",
        "persona": {"name": "Ana", "languages": ["Spanish"], "multilingual": False},
        "coverage": {"overlay": "none"},
        "sub_goals": ["books_ride"],
        "keywords": [],
        "background_noise": "quiet line",
        "max_turns": None,
        "status": "authored",
        "call_execution_id": None,
        "group": "Book a ride",
    }
    listed = {
        "count": 1,
        "next": None,
        "previous": None,
        "total_pages": 1,
        "current_page": 1,
        "results": [row],
        "groups": [{"name": "Book a ride", "count": 1, "total": 1}],
        "group_by": "goal",
        "fields": [
            {
                "value": "name",
                "label": "Scenario",
                "type": "string",
                "category": "scenario",
            }
        ],
        "scenario_editing": HostedHarnessProvider._editing_contract(
            HostedHarnessProvider, spoken=True
        ),
        "groupings": [{"value": "", "label": "None"}],
        "level_labels": {"none": "No attack", "quiet line": "Off"},
    }
    grid = {
        "per_axis": [
            {
                "axis": "overlay",
                "label": "Attack",
                "levels": 1,
                "scenarios": 1,
                "counts": {"none": 1},
            }
        ],
        "row_axis": "task",
        "row_axis_label": "Task",
        "col_axis": "overlay",
        "col_axis_label": "Attack",
        "rows": ["book"],
        "columns": ["none"],
        "cells": [{"row": "book", "column": "none", "count": 1}],
        "axes": ["task", "overlay"],
        "axis_labels": {"task": "Task", "overlay": "Attack"},
        "level_labels": {"none": "No attack"},
    }
    receipts = {
        "receipts": [{"scenario": "a", "outcome": "refused", "why": "no such scenario"}]
    }

    for serializer, payload in (
        (HarnessScenarioListResponseSerializer, listed),
        (HarnessScenarioCoverageResponseSerializer, grid),
        (HarnessScenarioAmendResponseSerializer, receipts),
    ):
        checked = serializer(data=payload)
        assert checked.is_valid(), checked.errors
        assert serializer(payload).data == payload


def test_a_sub_goal_reads_as_words():
    labels = level_labels_for(
        [{"sub_goals": ["exact_opening_greeting"]}],
        [{"value": "sub_goals", "choices": ["spoken_pin_guidance"]}],
    )

    assert labels["exact_opening_greeting"] == "Exact opening greeting"
    assert labels["spoken_pin_guidance"] == "Spoken pin guidance"
