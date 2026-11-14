"""Actual DRF routing/decorators and first ORM read; no query results fabricated."""

import copy
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.db.models.query import QuerySet
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
    exact_total_explicitly_required,
)
from tracer.views.observation_span import ObservationSpanView
from tracer.views.trace import TraceView, UsersView
from tracer.views.trace_session import TraceSessionView

PID, VERSION, TID = [str(UUID(int=n)) for n in (11, 12, 13)]
FILTERS = json.dumps(
    [
        {
            "column_id": "fixture.text",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "synthetic exact text",
            },
        }
    ]
)
ROUTES = [
    (TraceView, "list_traces_of_session", {"project_id": PID}),
    (ObservationSpanView, "list_spans_observe", {"project_id": PID}),
    (TraceSessionView, "list_sessions", {"project_id": PID}),
    (UsersView, "get", {"project_id": PID}),
    (TraceView, "list_traces", {"project_version_id": VERSION}),
    (ObservationSpanView, "list_spans", {"project_version_id": VERSION}),
    (TraceView, "list_voice_calls", {"project_id": PID}),
    (
        TraceView,
        "get_trace_id_by_index",
        {"project_version_id": VERSION, "trace_id": TID},
    ),
    (TraceView, "get_trace_id_by_index_observe", {"project_id": PID, "trace_id": TID}),
    (
        ObservationSpanView,
        "get_trace_id_by_index_spans_as_base",
        {"project_version_id": VERSION, "span_id": TID},
    ),
    (
        ObservationSpanView,
        "get_trace_id_by_index_spans_as_observe",
        {"project_id": PID, "span_id": TID},
    ),
    (TraceView, "agent_graph", {"project_id": PID}),
    (TraceSessionView, "retrieve_query", {}),
]


class Boundary(BaseException):
    pass


def test_session_detail_response_retains_mixed_json_values():
    from tracer.serializers.trace_session import TraceSessionDetailResponseSerializer

    payload = {
        "status": True,
        "result": {
            "session_metadata": {"duration": 1.25, "user_id": None, "total_tokens": 3},
            "response": [
                {
                    "trace_id": TID,
                    "input": "text",
                    "output": {"ok": True},
                    "system_metrics": {"total_tokens": 3},
                    "evals_metrics": {},
                }
            ],
            "next": False,
        },
    }
    serializer = TraceSessionDetailResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == payload


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def dispatch(cls, action, method, data, monkeypatch, query="", authenticated=True):
    org = Organization(id=UUID(int=21))
    workspace = Workspace(id=UUID(int=22), organization=org)
    user = User(id=UUID(int=23), organization=org)
    factory = APIRequestFactory()
    raw = (
        factory.post("/" + query, copy.deepcopy(data), format="json")
        if method == "post"
        else factory.get("/", copy.deepcopy(data))
    )
    raw.organization, raw.workspace = org, workspace
    if authenticated:
        force_authenticate(raw, user=user, token=SimpleNamespace(pk=UUID(int=24)))
    captured = {}

    def stop(queryset):
        # Capture the real active handler frame at the mocked ORM I/O boundary.
        for frame in inspect.stack():
            request = frame.frame.f_locals.get("request")
            if request is not None and hasattr(request, "validated_query_serializer"):
                captured.update(
                    validated=copy.deepcopy(request.validated_query_data),
                    initial=copy.deepcopy(
                        request.validated_query_serializer.initial_data
                    ),
                    principal=str(request.user.id),
                    organization=str(request.organization.id),
                    workspace=str(request.workspace.id),
                )
                captured["scope"] = cursor_scope_for_request(request, project_ids=[PID])
                captured["exact_total"] = exact_total_explicitly_required(
                    request, request.validated_query_data
                )
                if cls is UsersView:
                    captured["requested_columns"] = cls._requested_columns_for_request(
                        request, request.validated_query_data
                    )
                break
        captured["orm_query"] = str(queryset.query)
        raise Boundary()

    monkeypatch.setattr(QuerySet, "_fetch_all", stop)
    if cls is UsersView:
        callback = cls.as_view()
    else:
        # Map only methods actually declared by the public action.
        callback = cls.as_view(dict(getattr(cls, action).mapping))
    try:
        response = callback(raw, **({"pk": TID} if action == "retrieve_query" else {}))
    except Boundary:
        assert captured, "actual handler did not reach mocked ORM read"
        return captured
    return {"status": response.status_code}


@pytest.mark.parametrize("cls,action,params", ROUTES, ids=[r[1] for r in ROUTES])
def test_read_post_actual_handler_matches_get_to_io(cls, action, params, monkeypatch):
    data = {**params, "filters": FILTERS}
    get = dispatch(cls, action, "get", data, monkeypatch)
    post = dispatch(cls, action, "post", data, monkeypatch)
    assert "validated" in get, get
    assert "validated" in post, post
    assert set(get["validated"]) == set(post["validated"]), set(get["validated"]) ^ set(
        post["validated"]
    )
    assert digest(get["validated"]) == digest(post["validated"])
    assert get["orm_query"] == post["orm_query"]
    assert all(get[k] == post[k] for k in ("principal", "organization", "workspace"))


@pytest.mark.parametrize("cls,action,params", ROUTES, ids=[r[1] for r in ROUTES])
@pytest.mark.parametrize("invalid", ["query", "unknown", "nonobject", "anonymous"])
def test_read_post_rejects_before_io(cls, action, params, invalid, monkeypatch):
    data = {**params, "filters": FILTERS}
    if invalid == "unknown":
        data["unknown_field"] = "reject"
    elif invalid == "nonobject":
        data = [data]
    result = dispatch(
        cls,
        action,
        "post",
        data,
        monkeypatch,
        query="?filters=" + FILTERS if invalid == "query" else "",
        authenticated=invalid != "anonymous",
    )
    assert result.get("status") in ((401, 403) if invalid == "anonymous" else (400,)), (
        result
    )


@pytest.mark.parametrize(
    "cls,action,params",
    [ROUTES[i] for i in (0, 1, 2, 4, 5, 6)],
    ids=lambda r: r if isinstance(r, str) else None,
)
@pytest.mark.parametrize("explicit", [None, False, True])
def test_allow_sampled_presence_not_default_value(
    cls, action, params, explicit, monkeypatch
):
    body = {**params, "filters": FILTERS}
    if explicit is not None:
        body["allow_sampled"] = explicit
    for method in ("get", "post"):
        result = dispatch(cls, action, method, body, monkeypatch)
        assert result["exact_total"] is (explicit is False)
        assert ("allow_sampled" in result["initial"]) is (explicit is not None)


@pytest.mark.parametrize("columns", [None, "[]", '["num_sessions"]'])
def test_users_projection_presence_retained(columns, monkeypatch):
    body = {"project_id": PID}
    if columns is not None:
        body["requested_columns"] = columns
    for method in ("get", "post"):
        result = dispatch(UsersView, "get", method, body, monkeypatch)
        assert result["requested_columns"] == (
            None if columns is None else json.loads(columns)
        )


@pytest.mark.parametrize("explicit", [None, False, True])
def test_cursor_mode_raw_presence_is_transport_independent(explicit, monkeypatch):
    body = {"project_id": PID}
    if explicit is not None:
        body["cursor_mode"] = explicit
    for method in ("get", "post"):
        result = dispatch(
            TraceView, "list_traces_of_session", method, body, monkeypatch
        )
        assert ("cursor_mode" in result["initial"]) is (explicit is not None)
        assert result["validated"]["cursor_mode"] is bool(explicit)
        if method == "post" and explicit is not None:
            assert result["initial"]["cursor_mode"] is explicit


def test_actual_handler_preserves_same_property_and_typed_mixed_family_values(
    monkeypatch,
):
    def leaf(family, key, kind, op, value, types=None):
        config = {
            "col_type": family,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        }
        if types:
            config["attribute_value_types"] = types
        return {"column_id": key, "filter_config": config}

    filters = [
        leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [0, 10]),
        leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [5, 20]),
        leaf(
            "SPAN_ATTRIBUTE",
            "mixed",
            "text",
            "in",
            ["0", 0, False],
            ["string", "number", "boolean"],
        ),
        leaf("SPAN_ATTRIBUTE", "flag", "boolean", "equals", False),
        leaf(
            "ANNOTATION", "review", "categorical", "in", ["needs,review", " approved "]
        ),
        leaf(
            "EVAL_METRIC", "quality", "categorical", "in", ["pass,with-note", " pass "]
        ),
    ]
    wire = json.dumps(filters)
    for method in ("get", "post"):
        result = dispatch(
            TraceView,
            "list_traces_of_session",
            method,
            {"project_id": PID, "filters": wire},
            monkeypatch,
        )
        assert result["initial"]["filters"] == wire
        assert result["validated"]["filters"] == filters


@pytest.mark.parametrize("cls,action,params", [ROUTES[i] for i in (0, 1, 2, 3, 6)])
def test_cursor_get_post_binding_and_page_exclusivity(cls, action, params, monkeypatch):
    body = {**params, "filters": FILTERS, "cursor_mode": True, "page_size": 10}
    if cls is UsersView or action == "list_traces_of_session":
        body["attribute_keys"] = '["exact,comma.key"]'
    first = dispatch(cls, action, "get", body, monkeypatch)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    token = encode_list_cursor(
        resource=action,
        scope=first["scope"],
        query=first["validated"],
        page_size=10,
        window_start=start,
        window_end=start + timedelta(days=1),
        order=(start, TID),
        seen_rows=10,
    )
    for method in ("get", "post"):
        # Opaque next/previous page tokens use the same method-independent binding.
        result = dispatch(cls, action, method, {**body, "cursor": token}, monkeypatch)
        assert result["initial"]["cursor"] == token
        args = {
            "resource": action,
            "scope": result["scope"],
            "query": result["validated"],
            "page_size": 10,
        }
        assert decode_list_cursor(token, **args).seen_rows == 10
        for scope_key in (
            "principal_id",
            "auth_id",
            "organization_id",
            "workspace_id",
            "project_ids",
        ):
            wrong = {
                **result["scope"],
                scope_key: ["different"] if scope_key == "project_ids" else "different",
            }
            with pytest.raises(ListCursorError, match="does not match"):
                decode_list_cursor(token, **{**args, "scope": wrong})
        for key, value in [
            ("filters", []),
            ("page_size", 20),
            ("project_id", UUID(int=99)),
        ]:
            with pytest.raises(ListCursorError, match="does not match"):
                decode_list_cursor(
                    token, **{**args, "query": {**result["validated"], key: value}}
                )
        if "attribute_keys" in body:
            with pytest.raises(ListCursorError, match="does not match"):
                decode_list_cursor(
                    token,
                    **{**args, "query": {**result["validated"], "attribute_keys": []}},
                )
        page_key = "current_page_index" if cls is UsersView else "page_number"
        invalid = dispatch(
            cls, action, method, {**body, "cursor": token, page_key: 0}, monkeypatch
        )
        assert invalid == {"status": 400}
