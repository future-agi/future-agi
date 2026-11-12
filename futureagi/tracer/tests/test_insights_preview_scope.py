"""Preview reads the report window, without recounting or changing fixed reads."""

from datetime import UTC, datetime
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("ee.agenthub.insights")

from ee.agenthub.insights import draft  # noqa: E402
from ee.agenthub.insights.units import UnitText  # noqa: E402
from tracer.views import insights as views  # noqa: E402

PROJECT = "11111111-1111-4111-8111-111111111111"
WINDOW = ["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]
SINCE = datetime(2026, 11, 1, tzinfo=UTC)
UNTIL = datetime(2026, 11, 2, tzinfo=UTC)


def preview_body(**overrides):
    return {
        "project_id": PROJECT,
        "config": {
            "name": "Historical activity",
            "unit": "users",
            "lens": "usage",
            "question": "What did users do?",
        },
        "unit_ids": ["retained-user"],
        **overrides,
    }


def date_filter(window):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": window,
        },
    }


@pytest.fixture
def preview_endpoint(monkeypatch):
    """Exercise serializer + real handler, with no storage or paid work."""
    engine = SimpleNamespace(preview=Mock(return_value=[]))
    monkeypatch.setattr(views, "_pipeline", lambda: (engine, None, RuntimeError))
    monkeypatch.setattr(views, "_org", lambda request: SimpleNamespace(id="org"))
    monkeypatch.setattr(
        views, "_project", lambda request, project_id: SimpleNamespace(id=project_id)
    )
    monkeypatch.setattr(views, "_gate", lambda *args: None)
    monkeypatch.setattr(views, "_spend", lambda *args: True)
    monkeypatch.setattr(views, "_credits_refused", lambda *args, **kwargs: None)
    monkeypatch.setattr(views, "_charge", lambda *args, **kwargs: None)
    monkeypatch.setattr(views, "_ran_on", lambda: {})
    monkeypatch.setattr(
        views.users_service,
        "count_view",
        Mock(side_effect=AssertionError("Preview must not recount the population")),
    )

    def post(body):
        serializer = views.InsightPreviewSerializer(data=body)
        serializer.is_valid(raise_exception=True)
        request = SimpleNamespace(validated_data=serializer.validated_data)
        return unwrap(views.InsightPreviewView.post)(
            views.InsightPreviewView(), request
        )

    return post, engine.preview


def test_preview_serializer_preserves_selected_scope():
    scope = {"preset": "Custom", "window": WINDOW, "search": "retained"}
    serializer = views.InsightPreviewSerializer(data=preview_body(scope=scope))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["scope"] == scope


@pytest.mark.parametrize("source", ["window", "filter", "filter-precedence"])
def test_preview_forwards_the_run_window_without_recounting(preview_endpoint, source):
    post, preview = preview_endpoint
    scope = {"preset": "Custom", "window": WINDOW}
    if source != "window":
        scope = {"filters": [date_filter(WINDOW)]}
    if source == "filter-precedence":
        # Like a run, a date filter wins over a separate scope.window.
        scope["window"] = ["2026-11-08T00:00:00Z", "2026-11-09T00:00:00Z"]
    response = post(preview_body(scope=scope))
    assert response.status_code == 200
    preview.assert_called_once()
    assert preview.call_args.kwargs["since"] == SINCE
    assert preview.call_args.kwargs["until"] == UNTIL
    assert preview.call_args.kwargs["unit_ids"] == ["retained-user"]


@pytest.mark.parametrize(
    "scope",
    [
        {"window": list(reversed(WINDOW))},
        {"window": ["not-a-date", WINDOW[1]]},
        {"filters": [date_filter(list(reversed(WINDOW)))]},
        {"preset": "not-a-preset", "window": WINDOW},
    ],
    ids=["reversed-window", "unreadable-window", "reversed-filter", "unknown-preset"],
)
def test_preview_serializer_refuses_invalid_scope(scope):
    serializer = views.InsightPreviewSerializer(data=preview_body(scope=scope))
    assert not serializer.is_valid()
    assert "scope" in serializer.errors


@pytest.mark.parametrize(
    "scope", [{}, {"window": WINDOW[:1]}], ids=["empty", "partial"]
)
def test_preview_refuses_nonfixed_scope_without_a_window(preview_endpoint, scope):
    post, preview = preview_endpoint
    response = post(preview_body(scope=scope))
    assert response.status_code == 400
    preview.assert_not_called()


@pytest.mark.parametrize("kind", ["legacy", "fixed", "fixed-partial-window"])
def test_fixed_and_legacy_previews_remain_unbounded(preview_endpoint, kind):
    post, preview = preview_endpoint
    body = preview_body()
    if kind != "legacy":
        body["scope"] = {"preset": "fixed", "label": "Handed-over users"}
    if kind == "fixed-partial-window":
        body["scope"]["window"] = WINDOW[:1]
    response = post(body)
    assert response.status_code == 200
    preview.assert_called_once()
    assert preview.call_args.kwargs.get("since") is None
    assert preview.call_args.kwargs.get("until") is None


def test_rows_handed_over_from_a_report_preview_within_its_window(preview_endpoint):
    """Find patterns inside a report's category hands its rows over with the
    window the report read; the run reads each user within it, and so does
    the preview of that run (OBS-SW-143)."""
    post, preview = preview_endpoint
    body = preview_body(
        scope={"preset": "fixed", "label": "Churned · Weekly", "window": WINDOW}
    )
    response = post(body)
    assert response.status_code == 200
    preview.assert_called_once()
    assert preview.call_args.kwargs["since"] == SINCE
    assert preview.call_args.kwargs["until"] == UNTIL
    assert preview.call_args.kwargs["unit_ids"] == ["retained-user"]


@pytest.mark.parametrize("agent_enabled", [False, True], ids=["builtin", "agent"])
def test_preview_passes_window_to_loader_before_either_engine(
    monkeypatch, agent_enabled
):
    loaded = UnitText(unit_id="retained-user", content="November 1 activity")
    load = Mock(return_value={loaded.unit_id: loaded})
    monkeypatch.setattr(draft, "load_units", load)
    monkeypatch.setattr(draft.insight_agent, "enabled", lambda: agent_enabled)
    monkeypatch.setattr(draft.insight_agent, "run_context", lambda *args: object())
    facets = {"summary": "November 1 activity"}
    read = Mock(return_value={loaded.unit_id: {"facets": facets}})
    extract = Mock(return_value=facets)
    monkeypatch.setattr(draft.insight_agent, "read_units", read)
    monkeypatch.setattr(draft, "extract", extract)
    monkeypatch.setattr(draft, "included", lambda *args: True)

    result = draft.preview(
        project_id=PROJECT,
        unit="users",
        lens_key="usage",
        question="What did users do?",
        agent_description="",
        instructions="",
        attributes=[],
        unit_ids=["retained-user", "u2", "u3", "u4"],
        since=SINCE,
        until=UNTIL,
    )

    load.assert_called_once_with(
        "users", PROJECT, ["retained-user", "u2", "u3"], since=SINCE, until=UNTIL
    )
    assert result[0]["facets"] == facets
    assert result[0]["included"] is True
    if agent_enabled:
        assert read.call_args.args[1] == [(loaded.unit_id, loaded.content)]
        extract.assert_not_called()
    else:
        assert extract.call_args.args == ("users", loaded.content)
        read.assert_not_called()


def test_preview_loader_still_accepts_unbounded_legacy_calls(monkeypatch):
    load = Mock(return_value={})
    monkeypatch.setattr(draft, "load_units", load)
    monkeypatch.setattr(draft.insight_agent, "enabled", lambda: False)
    assert (
        draft.preview(
            project_id=PROJECT,
            unit="users",
            lens_key="usage",
            question="What did users do?",
            agent_description="",
            instructions="",
            attributes=[],
            unit_ids=["retained-user"],
        )
        == []
    )
    load.assert_called_once()
    assert load.call_args.kwargs.get("since") is None
    assert load.call_args.kwargs.get("until") is None
