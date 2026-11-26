from types import SimpleNamespace
from unittest.mock import ANY, Mock

from rest_framework.exceptions import PermissionDenied

from simulate.services import harness_source_citations as citations
from simulate.services import harness_source_reviews


def test_only_accepted_reviewer_visible_names_are_displayed(monkeypatch):
    sources = [
        {
            "pin": {"source_id": "file"},
            "filename": "Northstar.pdf",
            "visibility": "agent_visible",
        },
        {
            "pin": {"source_id": "hidden"},
            "filename": "Private.txt",
            "visibility": "evaluator_only",
        },
    ]
    review = SimpleNamespace(preview={"sources": sources})
    scope = {"organization_id": 1, "workspace_id": 2}
    job = SimpleNamespace()
    monkeypatch.setattr(
        harness_source_reviews, "require_source_scope", lambda *args, **kwargs: scope
    )
    monkeypatch.setattr(citations, "_accepted_checkpoint", lambda *_: review)
    query = Mock()
    query.return_value.only.return_value = [
        SimpleNamespace(
            preview={
                "sources": [
                    {
                        "pin": {"source_id": "hidden"},
                        "filename": "Was public.txt",
                        "visibility": "agent_visible",
                    }
                ]
            }
        )
    ]
    monkeypatch.setattr(citations.HarnessSourceReview.objects, "filter", query)
    fixture = {
        "citations": [
            {"source_id": "file", "page": 2, "quote": "private excerpt"},
            {"source_id": "file", "page": 2},
            {"source_id": "unknown", "page": 3},
            {"source_id": "hidden", "row": 1},
        ]
    }
    row = SimpleNamespace(
        scenario_key="returns",
        tests="",
        reviewed_source_locations=citations.indexed_locations(
            {"fixture": {"reviewed_source": fixture}}
        ),
    )
    assert citations.visible_citations(None, job, [row]) == {
        "returns": [{"filename": "Northstar.pdf", "page": 2}]
    }
    query.assert_called_once_with(
        ANY, **scope, owner=job, owner_kind="environment", state="accepted"
    )


def test_restricted_reader_cannot_resolve_historical_citations(monkeypatch):
    row = SimpleNamespace(
        scenario_key="late",
        reviewed_source_locations=None,
        tests=(
            'Decline the return.\n\nReviewed source expectations: {"starting_facts": [],'
            ' "citations": [{"source_id": "file", "page": 1}]}'
        ),
    )
    assert citations._legacy_locations(row) == [{"source_id": "file", "page": 1}]
    monkeypatch.setattr(
        harness_source_reviews,
        "require_source_scope",
        Mock(side_effect=PermissionDenied()),
    )
    assert citations.visible_citations(None, None, [row]) == {}
