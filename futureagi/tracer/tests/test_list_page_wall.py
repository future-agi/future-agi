"""Per-route page wall: a cursor page acquires its rows at its own wall.

Numbered pages cannot resume, so they keep the interactive analytics wall;
hydration after acquisition stays under the request wall on every route. The
partial page itself (rows found so far, in order, plus a resumable cursor) is
covered by the existing checkpoint tests:
``test_observe_span_cursor_publishes_safe_checkpoint_after_failed_attempt``,
``test_observe_trace_cursor_publishes_safe_checkpoint_after_failed_attempt``
and ``test_sparse_session_cursor_follows_checkpoint_without_skip_or_duplicate``.
"""

from __future__ import annotations

import pathlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.test import override_settings

from tfc.settings.runtime_setting_specs import (
    INTERACTIVE_READ_SETTING_SPECS,
    RUNTIME_NUMERIC_SETTING_SPECS,
    load_numeric_settings,
)
from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded

pytestmark = pytest.mark.unit

PAGE_WALL_SETTINGS = (
    "SPAN_LIST_PAGE_WALL_MS",
    "TRACE_LIST_PAGE_WALL_MS",
    "SESSION_LIST_PAGE_WALL_MS",
    "USER_LIST_PAGE_WALL_MS",
)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"
START = datetime(2025, 1, 1)
END = START + timedelta(days=365)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", PAGE_WALL_SETTINGS)
def test_page_wall_setting_defaults_to_five_seconds_within_range(name):
    spec = INTERACTIVE_READ_SETTING_SPECS[name]
    assert spec.default == 5_000
    assert (spec.minimum, spec.maximum) == (100, 60_000)
    global_default = INTERACTIVE_READ_SETTING_SPECS[
        "INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"
    ].default
    assert spec.default != global_default
    lowered = load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={name: "100"})
    assert lowered[name] == 100
    with pytest.raises(ValueError, match=name):
        load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={name: "60001"})


def test_route_page_walls_are_the_route_setting_not_the_global_wall():
    from tracer.services import users_list_manager
    from tracer.views import observation_span, trace, trace_session

    bound = {
        "SPAN_LIST_PAGE_WALL_MS": observation_span.SPAN_LIST_PAGE_WALL_MS,
        "TRACE_LIST_PAGE_WALL_MS": trace.TRACE_LIST_PAGE_WALL_MS,
        "SESSION_LIST_PAGE_WALL_MS": trace_session.SESSION_LIST_PAGE_WALL_MS,
        "USER_LIST_PAGE_WALL_MS": users_list_manager.USER_LIST_PAGE_WALL_MS,
    }
    for name, value in bound.items():
        assert value == getattr(settings, name)
        assert value != settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    # The request wall and the per-row hydration budgets stay on the global.
    assert (
        observation_span.SPAN_LIST_WALL_DEADLINE_MS
        == trace.TRACE_LIST_WALL_DEADLINE_MS
        == trace_session.SESSION_LIST_WALL_DEADLINE_MS
        == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


# --------------------------------------------------------------------------
# span / trace / session acquisition cap
# --------------------------------------------------------------------------
class _RecordingDeadline:
    """A request wall that reports whichever cap the route asks for."""

    def __init__(self) -> None:
        self.caps: list[int | None] = []

    def remaining_ms(self, cap_ms=None, *, floor_ms=25):
        self.caps.append(cap_ms)
        return int(cap_ms) if cap_ms is not None else 8_000

    def elapsed_ms(self):
        return 1.0


def _empty_complete_page() -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


def _time_filter(start: datetime = START, end: datetime = END) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _attribute_filter(key: str, value: str) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _view_and_request(view_class):
    view = view_class.__new__(view_class)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        query_params={},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    return view, request, organization


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "SPAN_LIST"})
@pytest.mark.parametrize("cursor_mode", [True, False], ids=["cursor", "numbered"])
def test_span_list_cursor_page_acquires_at_the_span_page_wall(cursor_mode):
    from tracer.views.observation_span import (
        SPAN_LIST_CANDIDATE_DEADLINE_MS,
        SPAN_LIST_PAGE_WALL_MS,
        ObservationSpanView,
    )

    view, request, organization = _view_and_request(ObservationSpanView)
    deadline = _RecordingDeadline()
    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_empty_complete_page(),
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        status_name, payload = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(END - timedelta(minutes=30), END),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": cursor_mode,
                "allow_sampled": True,
            },
            analytics=mock.MagicMock(),
            org_project_ids=None,
            org=organization,
            read_deadline=deadline,
        )

    assert status_name == "ok"
    assert payload["metadata"]["total_rows"] == 0
    expected = (
        SPAN_LIST_PAGE_WALL_MS if cursor_mode else SPAN_LIST_CANDIDATE_DEADLINE_MS
    )
    assert bounded_read.call_args.kwargs["deadline_ms"] == expected
    assert expected in deadline.caps
    if cursor_mode:
        assert SPAN_LIST_CANDIDATE_DEADLINE_MS not in deadline.caps


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"})
@pytest.mark.parametrize("cursor_mode", [True, False], ids=["cursor", "numbered"])
def test_observe_trace_list_cursor_page_acquires_at_the_trace_page_wall(cursor_mode):
    from tracer.views.trace import (
        TRACE_LIST_CANDIDATE_DEADLINE_MS,
        TRACE_LIST_PAGE_WALL_MS,
        TraceView,
    )

    view, request, organization = _view_and_request(TraceView)
    deadline = _RecordingDeadline()
    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_empty_complete_page(),
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        status_name, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": cursor_mode,
                "allow_sampled": True,
            },
            analytics=mock.MagicMock(),
            org_project_ids=None,
            org=organization,
            read_deadline=deadline,
        )

    assert status_name == "ok"
    assert payload["metadata"]["total_rows"] == 0
    expected = (
        TRACE_LIST_PAGE_WALL_MS if cursor_mode else TRACE_LIST_CANDIDATE_DEADLINE_MS
    )
    assert bounded_read.call_args.kwargs["deadline_ms"] == expected
    assert expected in deadline.caps
    if cursor_mode:
        assert TRACE_LIST_CANDIDATE_DEADLINE_MS not in deadline.caps


@pytest.mark.parametrize("cursor_enabled", [True, False], ids=["cursor", "numbered"])
def test_session_list_cursor_page_acquires_at_the_session_page_wall(cursor_enabled):
    from tracer.views import trace_session as trace_session_view
    from tracer.views.trace_session import (
        SESSION_LIST_PAGE_WALL_MS,
        SESSION_LIST_QUERY_TIMEOUT_MS,
        _read_session_filter_page,
    )

    builder = mock.MagicMock()
    builder.filters = [_time_filter()]
    builder.page_number = 0
    builder.page_size = 25
    builder.recommended_filter_classify_batch_size.return_value = 50
    deadline = _RecordingDeadline()
    with mock.patch.object(
        trace_session_view,
        "read_bounded_filter_page",
        return_value=_empty_complete_page(),
    ) as bounded_read:
        page = _read_session_filter_page(
            builder,
            mock.MagicMock(),
            deadline,
            cursor_state=None,
            cursor_enabled=cursor_enabled,
        )

    assert page.complete is True
    expected = (
        SESSION_LIST_PAGE_WALL_MS if cursor_enabled else SESSION_LIST_QUERY_TIMEOUT_MS
    )
    assert bounded_read.call_args.kwargs["deadline_ms"] == expected
    assert deadline.caps == [expected]
    assert bounded_read.call_args.kwargs["bounded_continuation"] is cursor_enabled


# --------------------------------------------------------------------------
# users cursor walk
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_attribute_witness(monkeypatch):
    from tracer.services.users_list_manager import UsersListManager

    monkeypatch.setattr(
        UsersListManager,
        "_prune_attribute_candidate_batch",
        lambda self, rows, **k: rows,
    )


def _manager():
    from tracer.services.users_list_manager import UsersListManager

    project_id = str(uuid.uuid4())
    return UsersListManager(
        organization_id=str(uuid.uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
        filters=[],
        requested_columns=[],
        attribute_keys=[],
    )


def _candidate(index: int, *, now: datetime) -> dict:
    return {
        "end_user_id": str(uuid.UUID(int=index + 1)),
        "first_seen": now - timedelta(seconds=index),
        "user_id": f"user-{index}",
        "user_id_type": "custom",
        "user_id_hash": "",
    }


def _exact(candidate: dict) -> dict:
    return {
        "end_user_id": candidate["end_user_id"],
        "user_id": candidate["user_id"],
        "total_cost": 1.0,
        "total_tokens": 1,
        "input_tokens": 1,
        "output_tokens": 0,
        "num_traces": 1,
        "last_active": candidate["first_seen"],
    }


class _Walk:
    """Seventy ordered candidates; batch reads record the deadline they got."""

    def __init__(self, *, survivors: set[str] | None = None, stop_on_refill=None):
        self.now = datetime(2026, 8, 5, 12, tzinfo=UTC)
        self.candidates = [_candidate(i, now=self.now) for i in range(70)]
        self.exact_by_id = {c["end_user_id"]: _exact(c) for c in self.candidates}
        self.survivors = survivors
        self.stop_on_refill = stop_on_refill
        self.candidate_deadlines: list[object] = []
        self.exact_deadlines: list[object] = []

    def read_candidates(self, **kwargs):
        self.candidate_deadlines.append(kwargs["deadline"])
        before_id = kwargs["before_end_user_id"]
        start = 0
        if before_id is not None:
            if self.stop_on_refill is not None:
                raise self.stop_on_refill
            start = (
                next(
                    i
                    for i, c in enumerate(self.candidates)
                    if c["end_user_id"] == before_id
                )
                + 1
            )
        return self.candidates[start : start + kwargs["limit"]]

    def read_exact(self, **kwargs):
        self.exact_deadlines.append(kwargs["deadline"])
        return [
            self.exact_by_id[end_user_id]
            for end_user_id in kwargs["candidate_ids"]
            if self.survivors is None or end_user_id in self.survivors
        ]


def _patched(manager, walk):
    return (
        mock.patch.object(
            manager, "_read_dimension_candidates", side_effect=walk.read_candidates
        ),
        mock.patch.object(
            manager, "_read_exact_candidate_rows", side_effect=walk.read_exact
        ),
    )


def test_user_cursor_page_starts_the_user_page_wall_not_the_global():
    from tracer.services import users_list_manager
    from tracer.services.users_list_manager import USER_LIST_PAGE_WALL_MS

    manager = _manager()
    walk = _Walk()
    started: list[int] = []
    real_start = ReadDeadline.start
    reads, exacts = _patched(manager, walk)
    with (
        reads,
        exacts,
        mock.patch.object(
            users_list_manager.ReadDeadline,
            "start",
            side_effect=lambda total_ms: (
                started.append(total_ms) or real_start(total_ms)
            ),
        ),
    ):
        page = manager.list_cursor_payload(page_size=25)

    assert started == [USER_LIST_PAGE_WALL_MS]
    assert USER_LIST_PAGE_WALL_MS == settings.USER_LIST_PAGE_WALL_MS
    assert USER_LIST_PAGE_WALL_MS != settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    assert len(page.payload["table"]) == 25
    assert page.payload["query_status"] == "complete"
    assert page.payload["query_complete"] is True
    assert page.payload["query_exact"] is True
    # The first batch runs as before: no wall reaches its statements.
    assert walk.candidate_deadlines == [None]
    assert walk.exact_deadlines == [None]


def test_user_numbered_page_carries_no_page_wall():
    from tracer.services import users_list_manager

    manager = _manager()
    fetched: list[object] = []

    def fetch_rows(**kwargs):
        fetched.append(kwargs["deadline"])
        return [], 0, mock.MagicMock()

    with (
        mock.patch.object(users_list_manager.ReadDeadline, "start") as start,
        mock.patch.object(manager, "_fetch_rows", side_effect=fetch_rows),
    ):
        payload = manager.list_payload(page_size=25, current_page=0)

    start.assert_not_called()
    assert fetched == [None]
    assert payload["table"] == []


def test_user_export_opts_out_of_the_page_wall_and_fills_its_page():
    from tracer.services import users_list_manager
    from tracer.services.users_list_manager import USER_EXPORT_PAGE_SIZE

    manager = _manager()
    # One survivor in five: filling twenty rows takes the first batch plus
    # refills, exactly the walk a page wall would cut short.
    survivors = {
        c["end_user_id"] for c in _Walk().candidates if int(c["user_id"][5:]) % 5 == 0
    }
    walk = _Walk(survivors=survivors)
    reads, exacts = _patched(manager, walk)
    with (
        reads,
        exacts,
        mock.patch.object(users_list_manager.ReadDeadline, "start") as start,
    ):
        page = manager.list_cursor_payload(
            page_size=USER_EXPORT_PAGE_SIZE, cursor=None, page_wall=False
        )

    start.assert_not_called()
    assert all(deadline is None for deadline in walk.candidate_deadlines)
    assert len(walk.candidate_deadlines) > 1
    assert len(page.payload["table"]) == len(survivors) == 14
    assert page.payload["query_status"] == "complete"
    assert page.has_more is False
    csv_text = "".join(manager.iter_export_csv(cursor_read=page))
    assert "export truncated" not in csv_text


def test_user_refill_stopped_by_the_wall_publishes_partial_page_and_resumes():
    manager = _manager()
    survivors = {
        c["end_user_id"] for c in _Walk().candidates if int(c["user_id"][5:]) % 5 == 0
    }
    walk = _Walk(survivors=survivors, stop_on_refill=ReadDeadlineExceeded("wall"))
    reads, exacts = _patched(manager, walk)
    with reads, exacts:
        first = manager.list_cursor_payload(page_size=25)

    # Batch 1 (25 candidates) published its five true matches in order and
    # the wall stopped the refill: partial page, has_more, checkpoint = the
    # last consumed candidate of batch 1, never the failed refill.
    published = [row["end_user_id"] for row in first.payload["table"]]
    assert published == [
        c["end_user_id"] for c in walk.candidates[:25] if c["end_user_id"] in survivors
    ]
    assert len(published) == 5
    assert first.has_more is True
    assert first.payload["has_more"] is True
    assert first.checkpoint_order == (
        "physical_latest_users_v1",
        walk.candidates[24]["first_seen"],
        walk.candidates[24]["end_user_id"],
    )
    assert first.seen_rows == 5
    assert walk.candidate_deadlines[0] is None
    assert isinstance(walk.candidate_deadlines[1], ReadDeadline)
    # Disclosed as a page cut short, never as a complete short page; the rows
    # it shows are exact and ordered.
    assert first.payload["query_status"] == "degraded"
    assert first.payload["query_complete"] is False
    assert first.payload["count_is_lower_bound"] is True
    assert first.payload["query_exact"] is True
    assert first.payload["ordering_exact"] is True

    # Resuming from that cursor re-reads from the checkpoint: no skip, no repeat.
    resumed = _Walk(survivors=survivors)
    reads, exacts = _patched(manager, resumed)
    with reads, exacts:
        second = manager.list_cursor_payload(
            page_size=25,
            cursor=ListCursor(
                window_start=first.window_start,
                window_end=first.window_end,
                order=first.checkpoint_order,
                seen_rows=first.seen_rows,
            ),
        )
    second_ids = [row["end_user_id"] for row in second.payload["table"]]
    expected_rest = [
        c["end_user_id"] for c in walk.candidates[25:] if c["end_user_id"] in survivors
    ]
    assert second_ids == expected_rest
    assert set(published).isdisjoint(second_ids)
    assert published + second_ids == [
        c["end_user_id"] for c in walk.candidates if c["end_user_id"] in survivors
    ]
    assert second.has_more is False
    assert second.payload["query_status"] == "complete"
    assert second.payload["query_complete"] is True


def test_user_refill_budget_error_after_checkpoint_publishes_partial_page():
    from clickhouse_driver.errors import ErrorCodes, ServerException

    manager = _manager()
    survivors = {
        c["end_user_id"] for c in _Walk().candidates if int(c["user_id"][5:]) % 5 == 0
    }
    timed_out = ServerException("timeout", code=ErrorCodes.TIMEOUT_EXCEEDED)
    walk = _Walk(survivors=survivors, stop_on_refill=timed_out)
    reads, exacts = _patched(manager, walk)
    with reads, exacts:
        page = manager.list_cursor_payload(page_size=25)

    assert len(page.payload["table"]) == 5
    assert page.has_more is True
    assert page.checkpoint_order is not None
    assert page.payload["query_status"] == "degraded"


def test_user_first_batch_never_stops_at_the_wall_and_no_refill_follows():
    from tracer.services import users_list_manager

    manager = _manager()
    survivors = {
        c["end_user_id"] for c in _Walk().candidates if int(c["user_id"][5:]) % 5 == 0
    }
    walk = _Walk(survivors=survivors)
    expired = ReadDeadline(total_ms=1, started=time.monotonic() - 10)
    reads, exacts = _patched(manager, walk)
    with (
        reads,
        exacts,
        mock.patch.object(
            users_list_manager.ReadDeadline, "start", return_value=expired
        ),
    ):
        page = manager.list_cursor_payload(page_size=25)

    # An already-expired wall cannot touch the first batch; it only stops the
    # refill, which publishes what the first batch proved plus its checkpoint.
    assert walk.candidate_deadlines == [None]
    assert len(page.payload["table"]) == 5
    assert page.has_more is True
    assert page.checkpoint_order[2] == walk.candidates[24]["end_user_id"]
    assert page.payload["query_status"] == "degraded"
    assert page.payload["query_complete"] is False


@pytest.mark.parametrize("refill", [True, False], ids=["refill", "first-batch"])
def test_user_refill_candidate_statements_carry_the_page_wall_as_a_server_cap(
    refill,
):
    """A refill's whole-window candidate statement is stopped by the server.

    It used to be admitted by the page wall only: ``timeout_ms`` is not a
    statement deadline, so ClickHouse ran it with ``max_execution_time`` 0.
    Under a refill's page wall the candidate statement and its survivor
    statement ask the server to stop them at ``USER_LIST_QUERY_TIMEOUT_MS``
    or what is left of the wall; a stop is a page-wall stop the page
    resumes from (``test_user_refill_stopped_by_the_wall_publishes_partial_page_and_resumes``).
    The first batch has no deadline and still sends none.
    """
    from tracer.services import users_list_manager

    manager = _manager()
    user = str(uuid.UUID(int=7))
    sent: list[dict] = []

    class _Service:
        def execute_ch_query(self, query, params=None, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(data=[{"end_user_id": user}])

    deadline = ReadDeadline.start(5_000) if refill else None
    with (
        mock.patch.object(users_list_manager, "V2AnalyticsQueryService", _Service),
        mock.patch.object(
            manager,
            "_format_candidate_rows",
            return_value=[{"end_user_id": user}],
        ),
    ):
        manager._read_dimension_candidates(
            deadline=deadline,
            limit=26,
            before_first_seen=None,
            before_end_user_id=None,
            window_start=START,
            window_end=END,
        )

    assert len(sent) == 2
    for kwargs in sent:
        if refill:
            assert 0 < kwargs["timeout_ms"] <= 5_000
            assert kwargs["server_execution_cap_ms"] == kwargs["timeout_ms"]
        else:
            assert kwargs["timeout_ms"] is None
            assert kwargs.get("server_execution_cap_ms") is None


def test_user_refill_programming_error_still_fails_closed():
    manager = _manager()
    walk = _Walk(survivors=set(), stop_on_refill=RuntimeError("private defect"))
    reads, exacts = _patched(manager, walk)
    with reads, exacts, pytest.raises(RuntimeError, match="private defect"):
        manager.list_cursor_payload(page_size=25)


# --------------------------------------------------------------------------
# a numbered page is not bound by the wall, driven through a transport that
# actually spends more than the wall allows
# --------------------------------------------------------------------------
_OVERRUN_MS = 400
_OVERRUN_ROWS = 3


def _wall_stopped_page() -> BoundedFilterPage:
    """What an acquisition really publishes when its budget runs out."""

    return BoundedFilterPage(
        rows=[],
        has_more=True,
        complete=False,
        status="degraded",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=float(_OVERRUN_MS),
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


def _complete_page(rows: list[dict]) -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=list(rows),
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=len(rows),
        elapsed_ms=float(_OVERRUN_MS),
        query_count=1,
        rows_returned=len(rows),
        result_payload_bytes=0,
        attempts=(),
    )


def _budget_enforcing_reader(rows: list[dict]):
    """An acquisition that needs ``_OVERRUN_MS`` and honours its own budget.

    This is the point of the test. The route decides how much time the
    acquisition may spend; this stands in for the transport that spends it.
    Handed a budget that covers the work it publishes every row; handed less
    it publishes what a wall-stopped read publishes, which is no rows, not
    complete, and more to come. A route that wrongly put a numbered request
    on the page wall therefore loses rows here rather than merely passing a
    different number.
    """

    seen: list[int | None] = []

    def _read(*_args, **kwargs):
        budget = kwargs["deadline_ms"]
        seen.append(budget)
        if budget is not None and budget < _OVERRUN_MS:
            return _wall_stopped_page()
        return _complete_page(rows)

    _read.budgets = seen
    return _read


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "SPAN_LIST"})
@pytest.mark.parametrize("cursor_mode", [True, False], ids=["cursor", "numbered"])
def test_span_numbered_page_outlives_the_wall_a_cursor_page_stops_at(cursor_mode):
    from tracer.views import observation_span as span_view
    from tracer.views.observation_span import ObservationSpanView

    view, request, organization = _view_and_request(ObservationSpanView)
    reader = _budget_enforcing_reader([])
    with (
        mock.patch.object(span_view, "SPAN_LIST_PAGE_WALL_MS", _OVERRUN_MS // 4),
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            side_effect=reader,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        result = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(END - timedelta(minutes=30), END),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": cursor_mode,
                "allow_sampled": True,
            },
            analytics=mock.MagicMock(),
            org_project_ids=None,
            org=organization,
            read_deadline=_RecordingDeadline(),
        )

    budget = reader.budgets[0]
    if cursor_mode:
        # The wall binds. This acquisition never reached a checkpoint, so the
        # route fails closed rather than publishing a page it cannot resume.
        assert budget == _OVERRUN_MS // 4
        assert result[0] == "error"
    else:
        # A numbered page has no cursor to resume from, so it may not be cut
        # short: it acquires under the request deadline instead, and every
        # row the transport found survives into the response.
        assert budget == span_view.SPAN_LIST_CANDIDATE_DEADLINE_MS
        assert budget > _OVERRUN_MS
        status_name, payload = result
        assert status_name == "ok"
        assert payload["metadata"]["query_complete"] is True


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"})
@pytest.mark.parametrize("cursor_mode", [True, False], ids=["cursor", "numbered"])
def test_trace_numbered_page_outlives_the_wall_a_cursor_page_stops_at(cursor_mode):
    """The trace list carries the identical gate; pin it the same way."""

    from tracer.views import trace as trace_view
    from tracer.views.trace import TraceView

    view, request, organization = _view_and_request(TraceView)
    reader = _budget_enforcing_reader([])
    with (
        mock.patch.object(trace_view, "TRACE_LIST_PAGE_WALL_MS", _OVERRUN_MS // 4),
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            side_effect=reader,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        result = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": cursor_mode,
                "allow_sampled": True,
            },
            analytics=mock.MagicMock(),
            org_project_ids=None,
            org=organization,
            read_deadline=_RecordingDeadline(),
        )

    budget = reader.budgets[0]
    if cursor_mode:
        assert budget == _OVERRUN_MS // 4
        assert result[0] == "error"
    else:
        assert budget == trace_view.TRACE_LIST_CANDIDATE_DEADLINE_MS
        assert budget > _OVERRUN_MS
        status_name, payload = result
        assert status_name == "ok"
        assert payload["metadata"]["query_complete"] is True


@pytest.mark.parametrize("cursor_enabled", [True, False], ids=["cursor", "numbered"])
def test_session_numbered_page_keeps_every_row_the_wall_would_have_cut(cursor_enabled):
    from tracer.views import trace_session as trace_session_view
    from tracer.views.trace_session import _read_session_filter_page

    rows = [{"session_id": str(uuid.UUID(int=i + 1))} for i in range(_OVERRUN_ROWS)]
    builder = mock.MagicMock()
    builder.filters = [_time_filter()]
    builder.page_number = 0
    builder.page_size = 25
    builder.recommended_filter_classify_batch_size.return_value = 50
    reader = _budget_enforcing_reader(rows)
    with (
        mock.patch.object(
            trace_session_view, "SESSION_LIST_PAGE_WALL_MS", _OVERRUN_MS // 4
        ),
        mock.patch.object(
            trace_session_view, "read_bounded_filter_page", side_effect=reader
        ),
    ):
        page = _read_session_filter_page(
            builder,
            mock.MagicMock(),
            _RecordingDeadline(),
            cursor_state=None,
            cursor_enabled=cursor_enabled,
        )

    if cursor_enabled:
        assert reader.budgets[0] == _OVERRUN_MS // 4
        assert page.complete is False
        assert page.rows == []
        assert page.has_more is True
    else:
        assert reader.budgets[0] > _OVERRUN_MS
        assert page.complete is True
        assert len(page.rows) == _OVERRUN_ROWS
        assert page.total_rows_lower_bound == _OVERRUN_ROWS


# --------------------------------------------------------------------------
# a CSV export is cursor-capable but it is not a page
# --------------------------------------------------------------------------
@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "SPAN_LIST"})
def test_span_export_fills_its_page_under_the_request_budget_not_the_wall():
    """Rule B bounds an interactive page; a download is not one.

    The bounded export turns cursor mode on to get the keyset reader, which
    also selected the five-second page wall over the request budget. A
    download has no reader to resume it, so the wall only truncated it.
    """

    from tracer.views import observation_span as span_view
    from tracer.views.observation_span import ObservationSpanView

    view, request, organization = _view_and_request(ObservationSpanView)
    reader = _budget_enforcing_reader([])
    with (
        mock.patch.object(span_view, "SPAN_LIST_PAGE_WALL_MS", _OVERRUN_MS // 4),
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            side_effect=reader,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(END - timedelta(minutes=30), END),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "page_wall": False,
                "allow_sampled": True,
            },
            analytics=mock.MagicMock(),
            org_project_ids=None,
            org=organization,
            read_deadline=_RecordingDeadline(),
        )

    assert reader.budgets[0] == span_view.SPAN_LIST_CANDIDATE_DEADLINE_MS
    assert reader.budgets[0] > _OVERRUN_MS


def test_session_export_fills_its_page_under_the_request_budget_not_the_wall():
    """Driven through the view, so the plumbing is under test too.

    Calling the page reader directly would prove only that it honours the
    parameter; it would not catch the view passing the wrong value, which is
    where the export's opt-out actually has to travel.
    """

    from tracer.tests.test_session_positive_witness_page import (
        PROJECT,
        builder,
        leaf,
    )
    from tracer.views import trace_session as trace_session_view
    from tracer.views.trace_session import (
        SESSION_LIST_QUERY_TIMEOUT_MS,
        TraceSessionView,
    )

    view = TraceSessionView.__new__(TraceSessionView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *a, **k: ("error", a, k),
    )
    organization = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        query_params={},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = SimpleNamespace(
        execute_ch_query=mock.Mock(return_value=SimpleNamespace(data=[]))
    )
    rows = [{"session_id": str(uuid.UUID(int=i + 1))} for i in range(_OVERRUN_ROWS)]
    reader = _budget_enforcing_reader(rows)
    data = {
        "filters": builder(leaf("company", ["alpha"], "text", "in")).filters,
        "sort_params": [],
        "page_number": 0,
        "page_size": 25,
        "cursor_mode": True,
        # What the bounded export sets, and what this test is really about.
        "page_wall": False,
    }
    with (
        mock.patch.object(
            trace_session_view, "SESSION_LIST_PAGE_WALL_MS", _OVERRUN_MS // 4
        ),
        mock.patch.object(
            trace_session_view, "read_bounded_filter_page", side_effect=reader
        ),
    ):
        TraceSessionView._select_session_page(
            view,
            request,
            validated_data=data,
            project_id=PROJECT,
            project=None,
            analytics=analytics,
            org_project_ids=None,
        )

    assert reader.budgets, "the export never reached the bounded page reader"
    # The view builds a real deadline, so the budget is the request timeout
    # less the millisecond or two already spent -- not the page wall.
    assert reader.budgets[0] > _OVERRUN_MS
    assert SESSION_LIST_QUERY_TIMEOUT_MS - reader.budgets[0] < 1_000


def test_the_bounded_exports_turn_the_page_wall_off_where_they_are_built():
    """The opt-out has to be set by the export entry point, not assumed."""

    from tracer.views import observation_span as span_view
    from tracer.views import trace_session as trace_session_view

    for module in (span_view, trace_session_view):
        source = pathlib.Path(module.__file__).read_text()
        export_block = source.split('kwargs.get("bounded_export")', 1)[1][:400]
        assert "cursor_mode=True" in export_block
        assert "page_wall=False" in export_block
