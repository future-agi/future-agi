"""Live ClickHouse 25: for the same filters and window, the population an
Instant eval run (or an Insights report) reads equals the view's own total.

Traces, spans and sessions are compared with the count statement their list
builder runs for an exact page total. Users are compared with the Users list
itself: its numbered page total when its statement decides every filter, and
every user its cursor pages publish when it decides some per user. Both sides
also equal the seeded truth, so a shared mistake can't pass.

Runs on a lane database carrying the deployed schema (``_users_live_ch``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.insights import InsightRun, InsightUnit
from tracer.services import instant_eval_users as users_service
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.v2.dispatch import get_v2_class
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.users_list_manager import UsersListManager
from tracer.tests._users_live_ch import lane_client

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

WINDOW_START = datetime(2026, 8, 3, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
WINDOW = {
    "column_id": "created_at",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [
            WINDOW_START.isoformat().replace("+00:00", "Z"),
            WINDOW_END.isoformat().replace("+00:00", "Z"),
        ],
    },
}
ERRORED = {
    "column_id": "status",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "ERROR",
    },
}
CLAUDE = {
    "column_id": "model",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "claude-3",
    },
}


def _more_than(column_id, value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": value,
        },
    }


@pytest.fixture
def ch_client():
    with lane_client() as client:
        yield client


@pytest.fixture
def seeded(ch_client, observe_project):
    """Four users active in the window and one only before it:

    ======  ========  ======  =================================
    user    sessions  traces  notes
    ======  ========  ======  =================================
    U1      3         6
    U2      1         1       its trace errored
    U3      2         2       its LLM calls ran on claude-3
    U4      1         3
    U5      1         2       five days before the window
    ======  ========  ======  =================================

    plus one trace with no user or session. Each trace is a root span and one
    LLM span under it.
    """
    project = observe_project.id
    users = {name: uuid.uuid4() for name in ("U1", "U2", "U3", "U4", "U5")}
    layout = {
        "U1": ([2, 2, 2], {}),
        "U2": ([1], {"status": "ERROR"}),
        "U3": ([1, 1], {"model": "claude-3"}),
        "U4": ([3], {}),
        "U5": ([2], {"days": -5}),
    }
    rows = []
    hour = 0

    def trace(user, session, *, status="OK", model="gpt-4o", days=0):
        nonlocal hour
        hour += 1
        start = WINDOW_START + timedelta(days=days, hours=hour)
        trace_id = str(uuid.uuid4())
        for kind, parent, span_model in (
            ("agent", "", ""),
            ("llm", f"{trace_id}-root", model),
        ):
            rows.append(
                {
                    "project_id": project,
                    "observation_type": kind,
                    "service_name": "svc",
                    "start_time": start,
                    "end_time": start + timedelta(seconds=2),
                    "trace_id": trace_id,
                    "id": f"{trace_id}-{'root' if not parent else 'llm'}",
                    "parent_span_id": parent,
                    "name": kind,
                    "trace_session_id": session,
                    "end_user_id": user,
                    "latency_ms": 2000,
                    "status": status,
                    "model": span_model,
                    "cost": 0.01,
                    "total_tokens": 10,
                    "prompt_tokens": 6,
                    "completion_tokens": 4,
                }
            )

    for name, (traces_per_session, extra) in layout.items():
        for count in traces_per_session:
            session = uuid.uuid4()
            for _ in range(count):
                trace(users[name], session, **extra)
    trace(None, None)

    columns = list(rows[0])
    ch_client.execute(
        f"INSERT INTO spans ({', '.join(columns)}) VALUES",
        [tuple(row[c] for c in columns) for row in rows],
    )
    ch_client.execute(
        "INSERT INTO end_users (project_id, end_user_id, organization_id, "
        "user_id, user_id_type, first_seen) VALUES",
        [
            (
                project,
                user,
                observe_project.organization_id,
                f"user-{name}",
                "string",
                WINDOW_START - timedelta(days=10),
            )
            for name, user in users.items()
        ],
    )
    try:
        yield {name: str(user) for name, user in users.items()}
    finally:
        for table in ("spans", "end_users"):
            ch_client.execute(
                f"ALTER TABLE {table} DELETE WHERE project_id = %(project)s "
                "SETTINGS mutations_sync = 2",
                {"project": project},
            )


def _list_total(query_type: str, project_id: str, filters: list[dict]) -> int:
    """The total a list page reports on its exact path: its builder's count."""
    builder = get_v2_class(query_type)(project_id=project_id, filters=filters)
    builder.build()
    sql, params = builder.build_count_query()
    rows = V2AnalyticsQueryService().execute_ch_query(sql, params).data
    return int(rows[0]["total"]) if rows else 0


def _users_manager(project, filters):
    return UsersListManager(
        organization_id=str(project.organization_id),
        allowed_project_ids=[str(project.id)],
        project_id=str(project.id),
        filters=filters,
        requested_columns=[],
    )


USERS = "/tracer/users/"


def _users_page(client, project, filters, **params):
    response = client.get(
        USERS,
        {
            "project_id": str(project.id),
            "filters": json.dumps(filters),
            "page_size": 50,
            **params,
        },
    )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _users_listed_over_http(client, project, filters) -> set[str]:
    """Every user the Users view's cursor pages publish, as the page asks."""
    listed: set[str] = set()
    cursor = None
    while True:
        page = _users_page(
            client,
            project,
            filters,
            page_size=2,
            cursor_mode="true",
            **({"cursor": cursor} if cursor else {}),
        )
        listed.update(str(r["end_user_id"]) for r in page["table"])
        cursor = page.get("next_cursor")
        if not cursor:
            return listed


def _users_listed(project, filters) -> set[str]:
    """Every user the Users list's cursor pages publish."""
    listed: set[str] = set()
    cursor = None
    while True:
        read = _users_manager(project, filters).list_cursor_payload(
            page_size=2, cursor=cursor
        )
        listed.update(str(r["end_user_id"]) for r in read.payload["table"])
        if not read.has_more:
            return listed
        cursor = ListCursor(
            window_start=read.window_start,
            window_end=read.window_end,
            order=tuple(read.checkpoint_order),
            seen_rows=read.seen_rows,
        )


@pytest.mark.parametrize(
    ("unit", "query_type", "extra", "truth"),
    [
        ("traces", "TRACE_LIST", [], 13),
        ("traces", "TRACE_LIST", [ERRORED], 1),
        ("spans", "SPAN_LIST", [], 26),
        ("spans", "SPAN_LIST", [CLAUDE], 2),
        ("sessions", "SESSION_LIST", [], 7),
    ],
)
def test_traces_spans_and_sessions_equal_their_lists_total(
    seeded, observe_project, unit, query_type, extra, truth
):
    project_id = str(observe_project.id)
    filters = [WINDOW, *extra]
    population = users_service.count_view(project_id, unit, filters)
    assert population.count == _list_total(query_type, project_id, filters) == truth
    picked = users_service.pick_view(
        project_id, unit, population.filters, n=100, after=None
    )
    assert len(picked) == truth


def test_users_decided_in_the_lists_statement_equal_its_page_total(
    seeded, observe_project, auth_client
):
    project_id = str(observe_project.id)
    for filters, truth in [
        ([WINDOW], {"U1", "U2", "U3", "U4"}),
        ([WINDOW, _more_than("num_traces", 2)], {"U1", "U4"}),
    ]:
        population = users_service.count_users(project_id, filters)
        listed = _users_manager(observe_project, filters).list_payload(
            page_size=50, current_page=0
        )
        assert population.count == listed["total_count"] == len(truth)
        # The Users view's numbered page reports the same total.
        page = _users_page(auth_client, observe_project, filters, current_page_index=0)
        assert page["total_count"] == population.count
        picked = users_service.pick_users(project_id, filters, n=50, after=None)
        assert (
            {rid for rid, _ in picked}
            == {str(r["end_user_id"]) for r in listed["table"]}
            == {seeded[name] for name in truth}
        )


def test_users_decided_per_user_equal_every_user_the_list_publishes(
    seeded, observe_project, auth_client
):
    project_id = str(observe_project.id)
    for filters, truth in [
        ([WINDOW, _more_than("num_sessions", 1)], {"U1", "U3"}),
        ([WINDOW, _more_than("avg_trace_latency", 1000)], {"U1", "U2", "U3", "U4"}),
    ]:
        assert not users_service._UsersView(project_id, filters, None).in_sql
        population = users_service.count_users(project_id, filters, finish=True)
        listed = _users_listed(observe_project, filters)
        assert (population.count, population.lower_bound) == (len(listed), False)
        assert _users_listed_over_http(auth_client, observe_project, filters) == listed
        picked = users_service.pick_users(project_id, filters, n=50, after=None)
        assert {rid for rid, _ in picked} == listed == {seeded[n] for n in truth}


def test_each_users_sessions_are_read_inside_the_window(seeded, observe_project):
    sessions = users_service.user_sessions(
        str(observe_project.id),
        [seeded["U1"], seeded["U5"]],
        WINDOW_START,
        WINDOW_END,
    )
    # U1's three sessions; U5's only session is before the window.
    assert len(sessions) == 3


def _user_is(*labels, op="in", unit="sessions"):
    """A User ID filter as a view's filter panel sends it (the Sessions shape
    is the one captured from the browser), or with no property (``unit=None``)
    as older saved views and the Users view carry it."""
    leaf = {
        "column_id": "user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": list(labels) if op in ("in", "not_in") else labels[0],
        },
    }
    if unit:
        leaf["property_id"] = f"system_attribute:{unit}:user"
        leaf["display_name"] = "User ID"
    return leaf


@pytest.fixture
def licensed(monkeypatch):
    """Instant eval and Insights served as a licensed install serves them;
    a report's run is recorded, not started on Temporal."""
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision
    from tracer.views import insights as insight_views

    monkeypatch.setattr(capability_service, "_configured", True)
    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **_: CapabilityDecision(allowed=True, feature_id=feature_id),
    )
    monkeypatch.setattr(insight_views, "_start", lambda run: None)
    monkeypatch.setattr(insight_views, "_spend", lambda *a: True)


def _sessions_grid_total(client, project, filters) -> int:
    """The total the Sessions grid itself reports for these filters."""
    response = client.get(
        "/tracer/trace-session/list_sessions/",
        {
            "project_id": str(project.id),
            "filters": json.dumps(filters),
            "page_number": 0,
            "page_size": 50,
        },
    )
    assert response.status_code == 200, response.content
    return int(response.json()["result"]["metadata"]["total_rows"])


@pytest.mark.parametrize(
    ("unit", "user_filter", "truth"),
    [
        ("sessions", _user_is("user-U1"), 3),
        ("sessions", _user_is("user-U1", op="equals"), 3),
        ("sessions", _user_is("user-U1", unit=None), 3),
        ("sessions", _user_is("user-U1", "user-U3"), 5),
        ("sessions", _user_is("user-U1", op="not_in"), 4),
        ("sessions", _user_is("nobody"), 0),
        ("traces", _user_is("user-U1", unit="traces"), 6),
        ("spans", _user_is("user-U3", unit="spans"), 4),
        ("users", _user_is("user-U1", "user-U3", unit=None), 2),
    ],
)
def test_a_view_filtered_by_user_id_counts_and_picks_what_its_grid_shows(
    seeded, observe_project, auth_client, unit, user_filter, truth
):
    """A User ID filter is the users' label, which the spans don't carry.
    Counting and picking a filtered view reads the same rows its grid lists."""
    project_id = str(observe_project.id)
    filters = [WINDOW, user_filter]
    # As a start counts: users to the end, past an estimate's time budget.
    population = users_service.count_view(project_id, unit, filters, finish=True)
    assert (population.count, population.lower_bound) == (truth, False)
    if unit == "sessions":
        assert _sessions_grid_total(auth_client, observe_project, filters) == truth
    picked = users_service.pick_view(
        project_id, unit, population.filters, n=100, after=None
    )
    assert len(picked) == truth


def test_both_estimates_count_a_sessions_view_filtered_by_user_id(
    seeded, observe_project, auth_client, licensed
):
    """What the Ask page and a new Insights report ask first: on a Sessions
    view filtered by User ID, each counts the sessions the grid shows."""
    filters = [WINDOW, _user_is("user-U1")]
    project = str(observe_project.id)
    asked = auth_client.post(
        "/tracer/instant-eval/estimate/",
        {"project_id": project, "unit": "sessions", "filters": filters},
        format="json",
    )
    assert asked.status_code == 200, asked.content
    assert asked.json()["result"]["population"] == 3
    report = auth_client.post(
        "/tracer/insights/estimate/",
        {"project_id": project, "unit": "sessions", "scope": {"filters": filters}},
        format="json",
    )
    assert report.status_code == 200, report.content
    assert report.json()["result"]["population"] == 3


def test_a_report_on_a_sessions_view_filtered_by_user_id_reads_its_sessions(
    seeded, observe_project, auth_client, ch_client, licensed
):
    """Run report on the new-report form's scope: the server counts and draws
    the user's own sessions, as the Sessions grid lists them."""
    response = auth_client.post(
        "/tracer/insights/runs/",
        {
            "project_id": str(observe_project.id),
            "config": {
                "name": "Where is the agent failing?",
                "unit": "sessions",
                "lens": "failures",
                "question": "Where is the agent failing?",
            },
            "scope": {
                "filters": [WINDOW, _user_is("user-U1")],
                "label": "1 filter",
            },
            "sample_size": 10,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    run = InsightRun.objects.get(id=response.json()["result"]["run_id"])
    sessions = {
        row[0]
        for row in ch_client.execute(
            "SELECT DISTINCT toString(trace_session_id) FROM spans "
            "WHERE toString(project_id) = %(project)s "
            "AND toString(end_user_id) = %(user)s",
            {"project": str(observe_project.id), "user": seeded["U1"]},
        )
    }
    assert len(sessions) == 3
    assert run.population_size == 3
    units = InsightUnit.objects.filter(run=run).values_list("unit_id", flat=True)
    assert set(units) == sessions
