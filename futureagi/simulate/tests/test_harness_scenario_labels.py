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
