"""TH-6934: a Users cursor page's first batch is stopped by the server.

The first batch of a seeded cursor page (the unfiltered Users tab, or one
filtered only by its date range) is one whole-window statement: a latest-state
replay of every span in the window, joined through the global end-user
survivor map and aggregated per user before its ``LIMIT``. Refills already
asked the server to stop at the page wall; the first batch did not, so it
reached ClickHouse with ``max_execution_time`` 0 and ran for as long as it
took, after the UI had stopped waiting and past the gateway's own timeout.

It now carries what is left of the request's analytics wall as its server
cap (``USER_LIST_FIRST_BATCH_WALL_MS``), and so does the survivor statement
after it. A stop there has no proven checkpoint to publish, so it propagates
and the route answers its existing retryable 503; it never becomes an empty
or "complete" page. Refills, the export and numbered pages are unchanged.
The live proof that the cap reaches ClickHouse is
``test_users_first_page_server_cap_ch25.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from rest_framework import status

from tracer.services import users_list_manager
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.users_list_manager import (
    USER_LIST_CURSOR_ORDER,
    USER_LIST_PAGE_WALL_MS,
    USER_LIST_QUERY_TIMEOUT_MS,
    UsersListManager,
)

# The request's analytics wall: the first batch's server cap.
ANALYTICS_WALL_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
WINDOW_START = NOW - timedelta(days=7)
WINDOW_END = NOW
# The Users tab's default request carries only its date-range preset.
UI_DATE_RANGE = [
    {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
    }
]
SHAPES = pytest.mark.parametrize(
    "filters", [[], UI_DATE_RANGE], ids=["unfiltered", "ui-date-range"]
)


def _user(index: int) -> str:
    return str(uuid.UUID(int=index + 1))


class _Users:
    """Candidates newest first; records what every statement asked the server."""

    def __init__(
        self,
        count: int = 70,
        *,
        stop_first: Exception | None = None,
        stop_at: int = 1,
    ):
        self.rows = [
            {
                "end_user_id": _user(i),
                "user_id": f"user-{i}",
                "last_active": NOW - timedelta(seconds=i),
            }
            for i in range(count)
        ]
        # ``stop_first`` is raised by statement number ``stop_at`` (1-based).
        self.stop_first = stop_first
        self.stop_at = stop_at
        self.sent: list[dict] = []

    def service(self):
        users = self

        class _Service:
            def execute_ch_query(self, query, params=None, **kwargs):
                params = params or {}
                # The survivor statement binds the batch it expands.
                users.sent.append(
                    {"remap": "dimension_candidate_ids" in params, **kwargs}
                )
                if users.stop_first is not None and len(users.sent) == users.stop_at:
                    raise users.stop_first
                if users.sent[-1]["remap"]:
                    return SimpleNamespace(data=[])
                before = params.get("before_end_user_id")
                start = (
                    0
                    if before is None
                    else next(
                        i + 1
                        for i, row in enumerate(users.rows)
                        if row["end_user_id"] == before
                    )
                )
                return SimpleNamespace(data=users.rows[start : start + params["limit"]])

        return _Service

    def candidates(self) -> list[dict]:
        return [kwargs for kwargs in self.sent if not kwargs["remap"]]


def _manager(filters: list[dict]) -> UsersListManager:
    project_id = str(uuid.uuid4())
    return UsersListManager(
        organization_id=str(uuid.uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
        filters=filters,
        requested_columns=[],
        attribute_keys=[],
    )


def _format(_builder, rows):
    return [
        {"end_user_id": row["end_user_id"], "user_id": row["user_id"]} for row in rows
    ]


def _read(manager, users, **kwargs):
    with (
        mock.patch.object(
            users_list_manager, "V2AnalyticsQueryService", users.service()
        ),
        mock.patch.object(manager, "_format_candidate_rows", side_effect=_format),
    ):
        return manager.list_cursor_payload(**kwargs)


def _assert_first_batch_cap(kwargs: dict) -> None:
    cap = kwargs.get("server_execution_cap_ms")
    assert cap is not None, "the first batch reached ClickHouse with no cap"
    assert 0 < cap <= ANALYTICS_WALL_MS
    # The analytics wall, not the refill's eight-second statement hint nor
    # the five-second page wall: a first page that loads in 10 s still loads.
    assert cap > max(USER_LIST_QUERY_TIMEOUT_MS, USER_LIST_PAGE_WALL_MS)
    assert kwargs["timeout_ms"] == cap


@pytest.mark.unit
def test_first_batch_wall_is_the_request_analytics_wall():
    wall = getattr(users_list_manager, "USER_LIST_FIRST_BATCH_WALL_MS", None)
    assert wall == ANALYTICS_WALL_MS


@pytest.mark.unit
@SHAPES
def test_first_page_candidate_statements_carry_the_analytics_wall_as_server_cap(
    filters,
):
    users = _Users()
    page = _read(_manager(filters), users, page_size=25)

    # Candidate statement, then its survivor statement: both capped.
    assert [kwargs["remap"] for kwargs in users.sent] == [False, True]
    for kwargs in users.sent:
        _assert_first_batch_cap(kwargs)
    # The page itself is unchanged: exact, ordered, complete, resumable.
    assert [row["end_user_id"] for row in page.payload["table"]] == [
        _user(i) for i in range(25)
    ]
    assert page.payload["query_status"] == "complete"
    assert page.payload["query_exact"] is True
    assert page.has_more is True
    assert page.checkpoint_order == (
        USER_LIST_CURSOR_ORDER,
        users.rows[24]["last_active"],
        _user(24),
    )


@pytest.mark.unit
def test_resumed_page_first_batch_carries_the_same_cap_and_keeps_its_keyset():
    users = _Users()
    page = _read(
        _manager([]),
        users,
        page_size=25,
        cursor=ListCursor(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            order=(USER_LIST_CURSOR_ORDER, users.rows[24]["last_active"], _user(24)),
            seen_rows=25,
        ),
    )

    for kwargs in users.sent:
        _assert_first_batch_cap(kwargs)
    assert [row["end_user_id"] for row in page.payload["table"]] == [
        _user(i) for i in range(25, 50)
    ]
    assert page.seen_rows == 50


@pytest.mark.unit
def test_refills_keep_the_page_wall_cap_after_a_capped_first_batch():
    users = _Users()
    page = _read(_manager([]), users, page_size=30)

    first, *refills = users.candidates()
    _assert_first_batch_cap(first)
    assert refills, "thirty rows take the first batch of 25 plus a refill"
    for kwargs in refills:
        cap = kwargs["server_execution_cap_ms"]
        assert 0 < cap <= min(USER_LIST_QUERY_TIMEOUT_MS, USER_LIST_PAGE_WALL_MS)
    assert [row["end_user_id"] for row in page.payload["table"]] == [
        _user(i) for i in range(30)
    ]
    assert page.payload["query_status"] == "complete"


@pytest.mark.unit
def test_export_first_batch_still_carries_no_cap():
    users = _Users(count=10)
    page = _read(_manager([]), users, page_size=20, page_wall=False)

    assert users.sent
    for kwargs in users.sent:
        assert kwargs["timeout_ms"] is None
        assert kwargs.get("server_execution_cap_ms") is None
    assert len(page.payload["table"]) == 10
    assert page.has_more is False


@pytest.mark.unit
@SHAPES
def test_first_batch_stopped_at_its_cap_propagates_and_publishes_nothing(filters):
    stop = ReadDeadlineExceeded("ClickHouse statement exceeded its execution cap")
    users = _Users(stop_first=stop)

    with pytest.raises(ReadDeadlineExceeded):
        _read(_manager(filters), users, page_size=25)

    assert len(users.sent) == 1
    _assert_first_batch_cap(users.sent[0])


def _cancelled():
    from clickhouse_driver.errors import ErrorCodes, ServerException

    return ServerException("cancelled", code=ErrorCodes.QUERY_WAS_CANCELLED)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stop", "stop_at", "resumed"),
    [
        (ReadDeadlineExceeded("execution cap"), 2, False),
        ("cancelled", 1, False),
        (ReadDeadlineExceeded("execution cap"), 1, True),
    ],
    ids=["survivor-stopped", "raw-cancellation", "resumed-request"],
)
def test_any_first_batch_stop_propagates_instead_of_publishing(stop, stop_at, resumed):
    """Survivor stops, raw server cancellations and resumed requests alike.

    A first batch has no checkpoint of its own, so none of these may become
    an empty or complete-looking page: each one surfaces as the read failure.
    """
    from tracer.services.clickhouse.read_budget import is_read_budget_error

    error = _cancelled() if stop == "cancelled" else stop
    users = _Users(stop_first=error, stop_at=stop_at)
    cursor = (
        ListCursor(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            order=(USER_LIST_CURSOR_ORDER, users.rows[24]["last_active"], _user(24)),
            seen_rows=25,
        )
        if resumed
        else None
    )

    with pytest.raises(type(error)) as raised:
        _read(_manager([]), users, page_size=25, cursor=cursor)

    assert raised.value is error
    assert is_read_budget_error(raised.value)
    assert len(users.sent) == stop_at
    for kwargs in users.sent:
        _assert_first_batch_cap(kwargs)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_users_route_answers_a_stopped_first_page_with_its_retryable_503(
    auth_client, organization, workspace
):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    project = Project.objects.create(
        name="Users cap",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    users = _Users(stop_first=ReadDeadlineExceeded("execution cap"))
    with (
        mock.patch.object(
            users_list_manager, "V2AnalyticsQueryService", users.service()
        ),
        mock.patch.object(
            UsersListManager, "_format_candidate_rows", side_effect=_format
        ),
    ):
        response = auth_client.get(
            "/tracer/users/",
            {
                "project_id": str(project.id),
                "page_size": 25,
                "cursor_mode": "true",
                "requested_columns": "[]",
            },
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    text = str(body)
    # The route's existing sanitized, retryable answer: no rows, no cursor and
    # none of the server's own error text.
    assert "service_unavailable" in text
    assert "User data is temporarily unavailable. Please retry." in text
    assert "execution cap" not in text
    assert "table" not in text and "next_cursor" not in text
    assert len(users.sent) == 1
    _assert_first_batch_cap(users.sent[0])
