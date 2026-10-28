"""Draft proposes a report setup, but never chooses which units it reads."""

from types import SimpleNamespace

import pytest

from ee.agenthub.insights import draft

pytestmark = pytest.mark.unit


def test_a_drafted_attribute_never_filters_the_report(monkeypatch):
    monkeypatch.setattr(
        draft,
        "load_units",
        lambda unit, project_id, ids: {
            "s1": SimpleNamespace(content="User: cancel my plan\nAssistant: half price?")
        },
    )
    # The model asks to read only resolved sessions: on a failures report that
    # would leave out the failures the report is meant to find.
    monkeypatch.setattr(
        draft,
        "chat_json",
        lambda *a, **k: {
            "name": "Unresolved sessions",
            "question": "Where does the agent fail?",
            "attributes": [
                {
                    "key": "resolved_by_agent",
                    "type": "boolean",
                    "description": "The agent resolved the request",
                    "filter_by": True,
                },
                {"key": "turns", "type": "number", "description": "Turns"},
            ],
        },
    )
    proposed = draft.draft_config(
        project_id="p",
        unit="sessions",
        lens_key="failures",
        goal="",
        agent_description="",
        sample_ids=["s1"],
    )
    assert [a["key"] for a in proposed["attributes"]] == ["resolved_by_agent", "turns"]
    assert [a["filter_by"] for a in proposed["attributes"]] == [False, False]
