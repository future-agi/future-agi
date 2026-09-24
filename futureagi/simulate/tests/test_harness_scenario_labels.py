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

    assert labels["transit"] == "Airport or train station"
    assert labels["quiet line"] == "Quiet line (no background noise)"
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


def test_a_call_reports_no_turn_budget_among_its_end_conditions():
    from types import SimpleNamespace

    from simulate.services.harness_environment import _end_conditions

    docs = [{"max_turns": 5}, {"max_turns": 9}]
    call = SimpleNamespace(payload={"agent": {"connector": "phone"}, "runtime": {}})
    chat = SimpleNamespace(payload={"agent": {"connector": "http"}, "runtime": {}})

    assert _end_conditions(call, {}, docs)["max_turns"] is None
    assert _end_conditions(chat, {}, docs)["max_turns"] == 9
