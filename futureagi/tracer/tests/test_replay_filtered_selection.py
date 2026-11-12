"""Replay creation freezes the authorized filtered session population.

Transport, persistence and population reads are replaced; serializer validation
and the real create handler run together. No database access is required.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from model_hub.services import bulk_selection
from tracer.serializers import replay_session as serializers
from tracer.services.clickhouse.v2 import trace_session_dict_reader
from tracer.views import replay_session as views

PROJECT = UUID("11111111-1111-4111-8111-111111111111")
SESSION_A = UUID("22222222-2222-4222-8222-222222222222")
SESSION_B = UUID("33333333-3333-4333-8333-333333333333")
SESSION_C = UUID("44444444-4444-4444-8444-444444444444")
FILTERS = [
    {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
        },
    },
    {
        "column_id": "user_id",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "Alice",
        },
    },
]


@pytest.fixture
def harness(monkeypatch):
    project = SimpleNamespace(id=PROJECT, organization=object(), workspace=object())

    def validate_project_id(self, value):
        assert value == PROJECT
        self.context["project"] = project
        return value

    monkeypatch.setattr(
        serializers.CreateReplaySessionSerializer,
        "validate_project_id",
        validate_project_id,
    )
    sources = Mock(
        side_effect=lambda ids, **kwargs: {
            str(identifier): {"project_id": str(PROJECT)} for identifier in ids
        }
    )
    monkeypatch.setattr(trace_session_dict_reader, "resolve_session_fields", sources)

    # A and B match; C is outside the date/filter range. The shared selector
    # contract subtracts exclusions before returning its complete population.
    def resolve(**kwargs):
        assert kwargs["filters"] == FILTERS
        return bulk_selection.ResolveResult(
            ids=[
                str(identifier)
                for identifier in (SESSION_A, SESSION_B)
                if str(identifier) not in {str(i) for i in kwargs["exclude_ids"]}
            ],
            total_matching=1,
            truncated=False,
        )

    resolver = Mock(side_effect=resolve)
    monkeypatch.setattr(bulk_selection, "resolve_filtered_session_ids", resolver)
    suggestions = Mock(return_value=(False, {}, None))
    monkeypatch.setattr(views, "get_agent_suggestions", suggestions)
    create = Mock(return_value=SimpleNamespace(id=SESSION_C))
    monkeypatch.setattr(views.ReplaySession.objects, "create", create)
    monkeypatch.setattr(
        views,
        "ReplaySessionResponseSerializer",
        lambda replay: SimpleNamespace(data={"id": str(replay.id)}),
    )
    return SimpleNamespace(
        project=project,
        user=object(),
        resolver=resolver,
        sources=sources,
        suggestions=suggestions,
        create=create,
    )


def payload(**overrides):
    return {
        "project_id": str(PROJECT),
        "replay_type": "session",
        "select_all": True,
        "ids": [],
        "exclude_ids": [str(SESSION_B)],
        "filters": FILTERS,
        **overrides,
    }


def create_replay(harness, data):
    request = SimpleNamespace(data=data, user=harness.user)
    view = views.ReplaySessionView()
    view.action = "create"
    view.request = request
    return view.create(request)


def test_replay_freezes_filtered_population_minus_exclusions(harness):
    response = create_replay(harness, payload())
    assert response.status_code == 201
    harness.resolver.assert_called_once()
    args = harness.resolver.call_args.kwargs
    assert str(args["project_id"]) == str(PROJECT)
    assert args["organization"] is harness.project.organization
    assert args["workspace"] is harness.project.workspace
    assert args["user"] is harness.user
    assert {str(i) for i in args["exclude_ids"]} == {str(SESSION_B)}
    harness.suggestions.assert_called_once_with(
        project=harness.project,
        replay_type="session",
        ids=[str(SESSION_A)],
        select_all=False,
    )
    persisted = harness.create.call_args.kwargs
    assert persisted["ids"] == [str(SESSION_A)]
    assert persisted["select_all"] is False


def test_replay_keeps_legacy_ids_as_exclusions_when_filters_are_supplied(harness):
    response = create_replay(harness, payload(ids=[str(SESSION_B)], exclude_ids=[]))
    assert response.status_code == 201
    assert harness.create.call_args.kwargs["ids"] == [str(SESSION_A)]
    assert harness.create.call_args.kwargs["select_all"] is False


def test_replay_select_all_requires_an_explicit_filter_population(harness):
    data = payload()
    del data["filters"]
    response = create_replay(harness, data)
    assert response.status_code == 400
    harness.suggestions.assert_not_called()
    harness.create.assert_not_called()


@pytest.mark.parametrize("filters", [[{"invalid": True}], "not-json", None])
def test_replay_rejects_malformed_filters(harness, filters):
    response = create_replay(harness, payload(filters=filters))
    assert response.status_code == 400
    harness.resolver.assert_not_called()
    harness.create.assert_not_called()


def test_replay_validates_exclusion_ownership(harness):
    harness.sources.return_value = {}
    harness.sources.side_effect = None
    response = create_replay(harness, payload())
    assert response.status_code == 400
    harness.resolver.assert_not_called()
    harness.create.assert_not_called()


@pytest.mark.parametrize("kind", ["empty", "truncated", "incomplete"])
def test_replay_never_persists_an_empty_or_partial_population(harness, kind):
    if kind == "incomplete":
        harness.resolver.side_effect = bulk_selection.BulkSelectionReadIncomplete(
            "selection_prefix_too_large"
        )
    else:
        harness.resolver.side_effect = None
        harness.resolver.return_value = bulk_selection.ResolveResult(
            ids=[] if kind == "empty" else [str(SESSION_A)],
            total_matching=0 if kind == "empty" else 2,
            truncated=kind == "truncated",
        )
    response = create_replay(harness, payload())
    assert response.status_code == 400
    harness.suggestions.assert_not_called()
    harness.create.assert_not_called()


def test_replay_explicit_selection_does_not_expand_to_filtered_population(harness):
    response = create_replay(
        harness, payload(select_all=False, ids=[str(SESSION_A)], exclude_ids=[])
    )
    assert response.status_code == 201
    harness.resolver.assert_not_called()
    assert harness.create.call_args.kwargs["ids"] == [str(SESSION_A)]
    assert harness.create.call_args.kwargs["select_all"] is False


def test_replay_rejects_exclusions_without_session_select_all(harness):
    response = create_replay(harness, payload(select_all=False, ids=[str(SESSION_A)]))
    assert response.status_code == 400
    harness.create.assert_not_called()
