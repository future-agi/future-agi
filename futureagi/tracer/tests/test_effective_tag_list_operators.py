"""Every Tag operator the Observe filter offers, through both list handlers.

The filter panel offers exact name membership only: equals, not equals, is
null and is not null. Each must return exactly the matching rows from the
public Observe trace and span list handlers. Any other operator, or an empty
name, is a request error (400) there, as it already is on the graphs, never a
server error. Real disposable ClickHouse (chdb) and PostgreSQL; run only on a
disposable cloud worker.
"""

from types import SimpleNamespace

import pytest
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.tests import test_entity_tag_readback as readback
from tracer.tests.test_effective_tag_filters import BEGIN, END, leaf, manual, seed
from tracer.tests.test_entity_tag_readback import (
    assignment_schema,  # noqa: F401
    physical_engine,  # noqa: F401
)
from tracer.tests.test_entity_tag_target import PROJECT
from tracer.views import observation_span as span_views
from tracer.views import trace as trace_views

stores = readback.stores

# The operators TraceFilterPanel offers for Tag (TAG_FILTER_OPERATORS).
OFFERED = ("in", "not_in", "is_null", "is_not_null")


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def tagged(stores):
    """alpha: SDK ``sdk`` on its root, untagged child. beta: UI ``ui`` on the
    trace (its root). gamma: no tags."""
    seed(stores, "alpha", tags='["sdk"]')
    seed(stores, "alpha", span="child", parent_span_id="root")
    seed(stores, "beta")
    seed(stores, "gamma")
    manual("beta", ["ui"])


def post(stores, monkeypatch, kind, tag_leaf):
    """The browser's POST through the real decorators and HTTP mapping."""
    project = SimpleNamespace(id=PROJECT, trace_type="observe", organization_id=None)
    if kind == "trace":
        view = trace_views.TraceView()
        monkeypatch.setattr(
            trace_views,
            "_project_queryset_for_request",
            lambda request: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: project)
            ),
        )
        handler = view.list_traces_of_session
    else:
        view = span_views.ObservationSpanView()
        monkeypatch.setattr(
            span_views.Project,
            "objects",
            SimpleNamespace(get=lambda *a, **k: project),
        )
        handler = view.list_spans_observe
    window = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [BEGIN.isoformat(), END.isoformat()],
        },
    }
    body = {
        "project_id": PROJECT,
        "filters": [window, tag_leaf],
        "page_number": 0,
        "page_size": 30,
    }
    request = Request(
        APIRequestFactory().post("/", body, format="json"), parsers=[JSONParser()]
    )
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(
        id="ws", organization_id="org", is_default=False
    )
    request._user = SimpleNamespace(
        is_authenticated=True, organization=request.organization
    )
    view.request = request
    view.format_kwarg = None
    return handler(request)


def ids(kind, response):
    assert response.status_code == 200, response.data
    rows = response.data["result"]["table"]
    if kind == "trace":
        return sorted(row["trace_id"] for row in rows)
    return sorted(f"{row['trace_id']}/{row['span_id']}" for row in rows)


TRACE_EXPECTED = {
    ("in", "sdk"): ["alpha"],
    ("in", "ui"): ["beta"],
    ("not_in", "sdk"): ["beta", "gamma"],
    ("not_in", "ui"): ["alpha", "gamma"],
    ("is_null", None): ["gamma"],
    ("is_not_null", None): ["alpha", "beta"],
}
# A span carries its own names; a root also carries its trace's UI tags.
SPAN_EXPECTED = {
    ("in", "sdk"): ["alpha/root"],
    ("in", "ui"): ["beta/root"],
    ("not_in", "sdk"): ["alpha/child", "beta/root", "gamma/root"],
    ("not_in", "ui"): ["alpha/child", "alpha/root", "gamma/root"],
    ("is_null", None): ["alpha/child", "gamma/root"],
    ("is_not_null", None): ["alpha/root", "beta/root"],
}


@pytest.mark.parametrize("kind", ["trace", "span"])
@pytest.mark.parametrize(
    "operation,value", sorted(TRACE_EXPECTED, key=lambda item: str(item))
)
def test_every_offered_tag_operator_returns_exactly_the_matching_rows(
    stores, monkeypatch, kind, operation, value
):
    assert operation in OFFERED
    tagged(stores)
    response = post(
        stores,
        monkeypatch,
        kind,
        leaf("tag", [value] if value is not None else None, operation),
    )
    expected = (TRACE_EXPECTED if kind == "trace" else SPAN_EXPECTED)[
        (operation, value)
    ]
    assert ids(kind, response) == expected


@pytest.mark.parametrize("kind", ["trace", "span"])
@pytest.mark.parametrize(
    "operation,value",
    [
        ("starts_with", "sd"),
        ("ends_with", "dk"),
        ("between", ["a", "z"]),
        ("in", [""]),
    ],
)
def test_other_tag_shapes_are_a_request_error_not_a_server_error(
    stores, monkeypatch, kind, operation, value
):
    tagged(stores)
    response = post(stores, monkeypatch, kind, leaf("tag", value, operation))
    assert response.status_code == 400, response.data
    assert "could not be loaded" not in str(response.data)


def test_span_list_rows_carry_the_parent_that_keys_their_tag_assignment(
    stores, monkeypatch
):
    """A root span row shares its trace's assignment, a child has its own.

    The grid applies a drawer save to the rows of the saved assignment, keyed
    the way the backend keys it; without parent_span_id a root row is keyed as
    a span of its own, misses the save and its next edit is refused as stale.
    """
    tagged(stores)
    response = post(stores, monkeypatch, "span", leaf("tag", ["no-such-tag"], "not_in"))
    assert response.status_code == 200, response.data
    parents = {
        f"{row['trace_id']}/{row['span_id']}": row["parent_span_id"]
        for row in response.data["result"]["table"]
    }
    assert parents == {
        "alpha/root": "",
        "alpha/child": "root",
        "beta/root": "",
        "gamma/root": "",
    }


def test_prototype_span_list_rows_carry_the_parent_span_id(stores, monkeypatch):
    """The same contract on the prototype/experiment span list."""
    seed(stores, "alpha", project_version_id=readback.PV)
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        project_version_id=readback.PV,
    )
    view = span_views.ObservationSpanView()
    version = SimpleNamespace(id=readback.PV, project_id=PROJECT)
    monkeypatch.setattr(
        span_views.ProjectVersion,
        "objects",
        SimpleNamespace(get=lambda *a, **k: version),
    )
    request = Request(
        APIRequestFactory().post(
            "/",
            {
                "project_version_id": readback.PV,
                "filters": [
                    {
                        "column_id": "created_at",
                        "filter_config": {
                            "filter_type": "datetime",
                            "filter_op": "between",
                            "filter_value": [BEGIN.isoformat(), END.isoformat()],
                        },
                    }
                ],
            },
            format="json",
        ),
        parsers=[JSONParser()],
    )
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(
        id="ws", organization_id="org", is_default=False
    )
    request._user = SimpleNamespace(
        is_authenticated=True, organization=request.organization
    )
    view.request = request
    view.format_kwarg = None
    response = view.list_spans(request)
    assert response.status_code == 200, response.data
    parents = {
        row["span_id"]: row.get("parent_span_id", "<missing>")
        for row in response.data["result"]["table"]
    }
    assert parents == {"root": "", "child": "root"}
