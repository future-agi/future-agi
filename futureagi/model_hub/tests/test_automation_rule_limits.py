import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.db.models import Q
from rest_framework.response import Response

from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.utils import annotation_queue_helpers as helpers
from model_hub.views.annotation_queues import (
    _AUTOMATION_RULE_READ_WALL_MS,
    AutomationRulePagination,
    AutomationRuleReadLimitExceeded,
    AutomationRuleViewSet,
    _bounded_automation_rule_read,
    _execute_automation_rule_query_with_deadline,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


class _BoundedRows:
    def __init__(self, ids):
        self.ids = ids
        self.requested_slice = None

    def order_by(self, *fields):
        assert fields == ("order", "id")
        return self

    def values_list(self, field, flat=False):
        assert field == "id"
        assert flat is True
        return self

    def __getitem__(self, item):
        self.requested_slice = item
        return self.ids[item]

    def count(self):
        raise AssertionError("dataset automation resolver must not issue COUNT(*)")


def _resolve_dataset_ids(ids, cap):
    rows = _BoundedRows(ids)
    organization = SimpleNamespace(pk="org-1")
    workspace = SimpleNamespace(pk="workspace-1", organization=organization)
    queue = SimpleNamespace(
        organization=organization,
        organization_id=organization.pk,
        workspace=workspace,
        workspace_id=workspace.pk,
        is_default=False,
        dataset_id="dataset-1",
    )
    dataset = SimpleNamespace(
        pk="dataset-1",
        organization=organization,
        workspace=workspace,
    )
    rule = SimpleNamespace(
        organization=organization,
        queue=queue,
        source_type="dataset_row",
    )

    def selected_rows(**kwargs):
        assert kwargs == {"dataset": dataset}
        return rows

    # The dataset lookup applies the queue's workspace rule; it is a stand-in
    # here because this resolver test runs without a database.
    scope_q = Q(pk="queue-workspace-scope")
    with (
        patch.object(
            helpers, "automation_rule_scope_is_allowed", return_value=True
        ) as scope,
        patch.object(
            helpers, "_queue_scope_workspace", return_value=workspace
        ) as scope_workspace,
        patch.object(
            helpers, "_queue_source_workspace_q", return_value=scope_q
        ) as workspace_q,
        patch.object(
            Dataset.no_workspace_objects, "get", return_value=dataset
        ) as get_dataset,
        patch.object(
            helpers,
            "_queue_source_queryset",
            return_value=SimpleNamespace(filter=selected_rows),
        ) as selection,
        patch.object(Column.objects, "filter", return_value=[]),
        patch.object(Cell.objects, "filter", return_value=object()),
    ):
        result = helpers._resolve_dataset_rule_ids(
            rule,
            filters=[],
            dataset_id="dataset-1",
            cap=cap,
        )
    scope.assert_called_once_with(
        queue, "dataset_row", {"scope": {"dataset_id": "dataset-1"}}
    )
    scope_workspace.assert_called_once_with(queue)
    workspace_q.assert_called_once_with(workspace)
    get_dataset.assert_called_once_with(
        scope_q,
        id="dataset-1",
        organization=organization,
        deleted=False,
    )
    selection.assert_called_once_with(queue, "dataset_row")
    return result, rows.requested_slice


def test_filter_mode_overflow_fails_before_queue_access():
    rule = SimpleNamespace(source_type="trace")

    with patch.object(helpers, "get_fk_field_name", return_value="trace"):
        result = helpers._add_source_ids_to_queue(
            rule,
            source_ids=["trace-1"],
            total_matching=helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        )

    assert result == {
        "matched": helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        "added": 0,
        "duplicates": 0,
        "truncated": True,
        "error": helpers.AUTOMATION_RULE_MATCH_LIMIT_ERROR,
    }


def test_filter_mode_overflow_preview_is_explicitly_truncated():
    rule = SimpleNamespace(source_type="trace")

    with patch.object(helpers, "get_fk_field_name", return_value="trace"):
        result = helpers._add_source_ids_to_queue(
            rule,
            source_ids=["trace-1"],
            total_matching=helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
            dry_run=True,
        )

    assert result == {
        "matched": helpers.AUTOMATION_RULE_MATCH_LIMIT + 1,
        "added": 0,
        "duplicates": 0,
        "truncated": True,
    }


def test_dataset_resolver_returns_exact_total_without_count_at_or_below_cap():
    result, requested_slice = _resolve_dataset_ids(["row-1", "row-2"], cap=2)

    assert result == (2, ["row-1", "row-2"])
    assert requested_slice == slice(None, 3, None)


def test_dataset_resolver_uses_cap_plus_one_overflow_sentinel_without_count():
    result, requested_slice = _resolve_dataset_ids(
        ["row-1", "row-2", "row-3", "row-4"], cap=2
    )

    assert result == (3, ["row-1", "row-2"])
    assert requested_slice == slice(None, 3, None)


def test_automation_rule_list_has_a_bounded_stable_page_contract():
    assert AutomationRulePagination.page_size == 25
    assert AutomationRulePagination.page_size_query_param == "limit"
    assert AutomationRulePagination.max_page_size == 100
    assert AutomationRuleViewSet.pagination_class is AutomationRulePagination
    source = inspect.getsource(AutomationRuleViewSet.get_queryset)
    assert "filter(queue_id=queue_id)" in source
    assert 'order_by("-created_at", "-id")' in source


def test_automation_rule_query_timeout_shrinks_before_each_statement():
    raw_cursor = SimpleNamespace(calls=[])

    def raw_execute(sql, params):
        raw_cursor.calls.append((sql, params))

    raw_cursor.execute = raw_execute
    deadline = SimpleNamespace(
        remaining=iter((8_100, 7_900)),
        remaining_ms=lambda *, floor_ms: next(deadline.remaining),
    )
    executed = []

    def execute(sql, params, many, context):
        executed.append((sql, params, many, context))
        return "rows"

    context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
    result = _execute_automation_rule_query_with_deadline(
        deadline, execute, "SELECT rules", (), False, context
    )

    assert result == "rows"
    assert raw_cursor.calls == [
        ("SELECT set_config('statement_timeout', %s, true)", ("8100",))
    ]
    assert executed == [("SELECT rules", (), False, context)]


def test_automation_rule_list_and_detail_reads_share_the_action_deadline():
    wrapper_code = _bounded_automation_rule_read(lambda *_args: None).__code__

    assert AutomationRuleViewSet.list.__code__ is wrapper_code
    assert AutomationRuleViewSet.retrieve.__code__ is wrapper_code


def test_automation_rule_read_failures_are_sanitized(monkeypatch):
    deadline = object()

    class _DeadlineFactory:
        @staticmethod
        def start(total_ms):
            assert total_ms == _AUTOMATION_RULE_READ_WALL_MS
            return deadline

    @contextmanager
    def bounded(received):
        assert received is deadline
        yield

    monkeypatch.setattr(
        "model_hub.views.annotation_queues.ReadDeadline", _DeadlineFactory
    )
    monkeypatch.setattr(
        "model_hub.views.annotation_queues._bounded_automation_rule_postgres",
        bounded,
    )

    for failure in (
        ReadDeadlineExceeded("secret timeout"),
        DatabaseError("secret database"),
        AutomationRuleReadLimitExceeded("secret payload"),
    ):
        response = _bounded_automation_rule_read(
            lambda _view, _request, failure=failure: (_ for _ in ()).throw(failure)
        )(SimpleNamespace(_gm=AutomationRuleViewSet._gm), object())

        assert response.status_code == 503
        assert response.data["code"] == "automation_rule_read_unavailable"
        assert "secret" not in str(response.data)


def test_automation_rule_success_payload_is_checked_before_return(monkeypatch):
    deadline = SimpleNamespace(remaining_ms=lambda *, floor_ms: 1_000)

    class _DeadlineFactory:
        @staticmethod
        def start(_total_ms):
            return deadline

    @contextmanager
    def bounded(_received):
        yield

    monkeypatch.setattr(
        "model_hub.views.annotation_queues.ReadDeadline", _DeadlineFactory
    )
    monkeypatch.setattr(
        "model_hub.views.annotation_queues._bounded_automation_rule_postgres",
        bounded,
    )
    monkeypatch.setattr(
        "model_hub.views.annotation_queues._ensure_automation_rule_response_bounded",
        lambda _value: (_ for _ in ()).throw(AutomationRuleReadLimitExceeded()),
    )

    response = _bounded_automation_rule_read(
        lambda _view, _request: Response({"conditions": "oversized"})
    )(SimpleNamespace(_gm=AutomationRuleViewSet._gm), object())

    assert response.status_code == 503
    assert response.data["code"] == "automation_rule_read_unavailable"
