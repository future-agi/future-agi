import json
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from simulate.models import CallExecution, CallTranscript, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.utils.test_execution import DEFAULT_CHAT_SIM_COL


@pytest.fixture
def chat_run(db, organization, workspace):
    run_test = RunTest.objects.create(
        name="Grouped chat", organization=organization, workspace=workspace
    )
    execution = SimulationTestExecution.objects.create(
        run_test=run_test, status=SimulationTestExecution.ExecutionStatus.COMPLETED
    )

    def add_call(name="Checkout", *, status="completed", score=4):
        scenario, _ = Scenarios.objects.get_or_create(
            organization=organization,
            workspace=workspace,
            name=name,
            defaults={"source": "prompt", "scenario_type": "script"},
        )
        return CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status=status,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_type="chat",
            overall_score=score,
            response_time_ms=200,
            conversation_metrics_data={"avg_latency_ms": 100, "csat_score": score},
        )

    return execution, add_call


def _detail(client, execution, **params):
    for key in ("row_groups", "group_keys"):
        if key in params:
            params[key] = json.dumps(params[key])
    response = client.get(f"/simulate/test-executions/{execution.id}/", params)
    assert response.status_code == 200, response.data
    return response.data


def _follow(client, execution, link):
    # Grouped links are relative to the endpoint; send them exactly as returned.
    assert link.startswith("?"), link
    response = client.get(f"/simulate/test-executions/{execution.id}/{link}")
    assert response.status_code == 200, response.data
    return response.data


def _groups(data, *fields):
    return [(*(row.get(f) for f in fields), row["count"]) for row in data["results"]]


def test_status_group_ignores_displayed_score(auth_client, chat_run):
    execution, add = chat_run
    first, second = add(score=2), add(score=4)
    # Isolate display selection: ordering by creation time cannot split these rows.
    CallExecution.objects.filter(pk=second.pk).update(created_at=first.created_at)

    data = _detail(auth_client, execution, row_groups=["status"])
    assert data["column_order"] == DEFAULT_CHAT_SIM_COL
    assert data["count"] == 1
    (row,) = data["results"]
    assert (row["status"], row["count"], row["avg_overall_score"]) == (
        "completed",
        2,
        3,
    )
    assert "overall_score" not in row


def test_status_pages_ignore_creation_time(auth_client, chat_run):
    execution, add = chat_run
    # Isolate ordering: the displayed column is already the requested field.
    columns = [{"id": "status", "column_name": "Status"}]
    execution.execution_metadata = {"Provider": True, "column_order": columns}
    execution.save(update_fields=["execution_metadata"])
    add(status="failed")
    first, second = add(), add()
    CallExecution.objects.filter(pk=second.pk).update(
        created_at=first.created_at + timedelta(minutes=1)
    )

    first = _detail(auth_client, execution, row_groups=["status"], limit=1)
    second = _follow(auth_client, execution, first["next"])
    for data, page, status, count in [
        (first, 1, "completed", 2),
        (second, 2, "failed", 1),
    ]:
        assert data["count"] == data["total_pages"] == 2
        assert data["current_page"] == page
        assert data["column_order"] == columns
        (row,) = data["results"]
        assert (row["status"], row["count"]) == (status, count)
    assert first["previous"] is None and second["next"] is None
    assert _follow(auth_client, execution, second["previous"]) == first


def test_grouped_page_links_keep_the_request(auth_client, chat_run):
    execution, add = chat_run
    add()
    add()
    add("Returns")
    add(status="failed")
    slow, unrelated = add(), add()
    CallExecution.objects.filter(test_execution=execution).update(
        call_summary="Order help"
    )
    CallExecution.objects.filter(pk=slow.pk).update(response_time_ms=5000)
    CallExecution.objects.filter(pk=unrelated.pk).update(call_summary="Billing")
    filters = [
        {
            "column_id": "responseTime",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "less_than",
                "filter_value": 1,
            },
        }
    ]
    request = {
        "row_groups": ["status", "scenario"],
        "group_keys": ["completed"],
        "search": "order help",
        "filters": json.dumps(filters),
        "limit": 1,
    }

    first = _detail(auth_client, execution, **dict(request))
    sent = {
        k: [json.dumps(v) if isinstance(v, list) else str(v)]
        for k, v in request.items()
    }
    assert parse_qs(urlsplit(first["next"]).query) == {**sent, "page": ["2"]}
    second = _follow(auth_client, execution, first["next"])
    back = _follow(auth_client, execution, second["previous"])

    # Each excluded call (failed, slow, unrelated) would add a group or a count.
    assert _groups(first, "status", "scenario__name") == [("completed", "Checkout", 2)]
    assert _groups(second, "status", "scenario__name") == [("completed", "Returns", 1)]
    assert (first["count"], second["count"]) == (2, 2)
    assert (first["previous"], second["next"]) == (None, None)
    assert parse_qs(urlsplit(second["previous"]).query) == {**sent, "page": ["1"]}
    assert back == first


def _add_transcripts(call, count):
    for index in range(count):
        CallTranscript.objects.create(
            call_execution=call,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content=f"turn {index}",
            start_time_ms=index * 1000,
            end_time_ms=index * 1000 + 500,
        )


def test_search_counts_each_matching_call_once(auth_client, chat_run):
    execution, add = chat_run
    calls = [add(score=4), add(score=4), add(score=1)]
    for call, transcripts in zip(calls, (2, 1, 1), strict=True):
        _add_transcripts(call, transcripts)
    # Matching but out of scope: a soft-deleted call and another run's call.
    deleted, moved = add(score=1), add(score=1)
    for call in (deleted, moved):
        _add_transcripts(call, 2)
    CallExecution.objects.filter(pk=deleted.pk).update(deleted=True)
    other = SimulationTestExecution.objects.create(
        run_test=execution.run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
    )
    CallExecution.objects.filter(pk=moved.pk).update(test_execution=other)

    searched = _detail(auth_client, execution, row_groups=["status"], search="Checkout")
    unsearched = _detail(auth_client, execution, row_groups=["status"])

    # Per transcript row: count 4, average 3.25. Distinct values average 2.5.
    for data in (searched, unsearched):
        (row,) = data["results"]
        assert (row["count"], row["avg_overall_score"], row["csat_score"]) == (
            3,
            3.0,
            3.0,
        )
    assert searched["results"] == unsearched["results"]


def test_unique_calls_stay_on_the_incoming_database(chat_run):
    # Imported here so the endpoint tests above still collect on the old head.
    from simulate.utils.test_execution_utils import _unique_matching_calls

    execution, add = chat_run
    add()
    add()
    calls = CallExecution.objects.using("default").filter(test_execution=execution)

    unique = _unique_matching_calls(calls.order_by("created_at"))
    with mock.patch("django.db.router.db_for_read", return_value="elsewhere"):
        assert unique.db == "default"
    assert unique.query.order_by == ("created_at",)
    assert unique.count() == 2


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (
            [],
            [
                ("completed", "Checkout", 2),
                ("completed", "Returns", 1),
                ("failed", "Checkout", 1),
            ],
        ),
        (["completed"], [("completed", "Checkout", 2), ("completed", "Returns", 1)]),
        (["completed", "Checkout"], [("completed", "Checkout", 2)]),
    ],
)
def test_flat_groups_with_positional_keys(auth_client, chat_run, keys, expected):
    execution, add = chat_run
    add("Returns")
    add("Checkout", status="failed")
    add("Checkout")
    add("Checkout")

    data = _detail(
        auth_client, execution, row_groups=["status", "scenario"], group_keys=keys
    )
    assert [
        (r.get("status"), r.get("scenario__name"), r["count"]) for r in data["results"]
    ] == expected
    assert all("id" not in row for row in data["results"])


@pytest.mark.parametrize(
    ("alias", "key", "field", "value"),
    [
        ("callType", "CHAT", "call_type", "chat"),
        ("overallScore", "4", "overall_score", 4),
        ("responseTime", "200", "response_time_ms", 200),
    ],
)
def test_legacy_group_ids_keep_field_names_and_filters(
    auth_client, chat_run, alias, key, field, value
):
    execution, add = chat_run
    add()
    data = _detail(auth_client, execution, row_groups=[alias], group_keys=[key])
    (row,) = data["results"]
    assert (row.get(field), row["count"]) == (value, 1)
    assert "overall_score" not in row or field == "overall_score"


@pytest.mark.parametrize("params", [{}, {"row_groups": []}], ids=["omitted", "empty"])
def test_ungrouped_calls_keep_newest_updated_order(auth_client, chat_run, params):
    execution, add = chat_run
    first, second = add(), add()
    # The first-created call was updated most recently.
    CallExecution.objects.filter(pk=first.pk).update(
        updated_at=second.updated_at + timedelta(minutes=1)
    )
    data = _detail(auth_client, execution, **params)
    assert data["column_order"] == DEFAULT_CHAT_SIM_COL
    assert data["count"] == 2
    assert [row["id"] for row in data["results"]] == [str(first.id), str(second.id)]


@pytest.mark.parametrize(
    ("alias", "field", "key", "value"),
    [
        ("callType", "call_type", "chat", "chat"),
        ("call_type", "call_type", "chat", "chat"),
        ("overallScore", "overall_score", 4, 4),
        ("overall_score", "overall_score", 4, 4),
        ("responseTime", "response_time_ms", 200, 200),
        ("response_time", "response_time_ms", 200, 200),
    ],
)
def test_null_group_key_selects_the_null_bucket(
    auth_client, chat_run, alias, field, key, value
):
    execution, add = chat_run
    add()
    blank = add()
    CallExecution.objects.filter(pk=blank.pk).update(
        call_type=None, overall_score=None, response_time_ms=None
    )

    def groups(**params):
        data = _detail(auth_client, execution, row_groups=[alias], **params)
        return _groups(data, field)

    assert groups() == [(value, 1), (None, 1)]
    assert groups(group_keys=[None]) == [(None, 1)]
    assert groups(group_keys=[key]) == [(value, 1)]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["completed", None], [("completed", None, 1)]),
        (["completed", "chat"], [("completed", "chat", 1)]),
        ([None, None], []),
    ],
    ids=["completed-null", "completed-chat", "null-null"],
)
def test_null_group_key_in_multi_level_drilldown(auth_client, chat_run, keys, expected):
    execution, add = chat_run
    add()
    blank = add()
    CallExecution.objects.filter(pk=blank.pk).update(call_type=None)

    data = _detail(
        auth_client, execution, row_groups=["status", "callType"], group_keys=keys
    )
    assert _groups(data, "status", "call_type") == expected


ALL_STATUS_SCENARIO = [
    ("completed", "Checkout", 2),
    ("completed", "Returns", 1),
    ("failed", "Checkout", 1),
]


@pytest.mark.parametrize(
    ("row_groups", "keys", "expected"),
    [
        (["turn_count", "status", "scenario"], [], ALL_STATUS_SCENARIO),
        (["status", "turn_count", "scenario"], [], ALL_STATUS_SCENARIO),
        (["status", "scenario", "turn_count"], [], ALL_STATUS_SCENARIO),
        (
            ["status", "turn_count", "scenario"],
            ["completed", "2", "Returns"],
            [("completed", "Returns", 1)],
        ),
        # A key at an unrecognised position does not filter the next field.
        (["turn_count", "status", "scenario"], ["completed"], ALL_STATUS_SCENARIO),
        (
            ["turn_count", "status", "scenario"],
            ["3", "failed"],
            [("failed", "Checkout", 1)],
        ),
        (
            ["status", "scenario", "turn_count"],
            ["completed", "Checkout", "3"],
            [("completed", "Checkout", 2)],
        ),
        (
            ["status", "turn_count"],
            ["completed", "3"],
            [("completed", None, 3)],
        ),
    ],
    ids=[
        "unknown-first",
        "unknown-middle",
        "unknown-last",
        "keys-across-unknown",
        "key-on-unknown-only",
        "key-after-unknown",
        "key-on-unknown-last",
        "status-then-unknown",
    ],
)
def test_mixed_groups_use_every_recognised_field(
    auth_client, chat_run, row_groups, keys, expected
):
    execution, add = chat_run
    add("Returns")
    add("Checkout", status="failed")
    add("Checkout")
    add("Checkout")

    data = _detail(auth_client, execution, row_groups=row_groups, group_keys=keys)
    assert _groups(data, "status", "scenario__name") == expected
    # The display fallback would group by the displayed score instead.
    for row in data["results"]:
        assert "overall_score" not in row and "id" not in row


def _display_fallback_calls(add):
    calls = [add(score=4), add(score=2), add(score=4)]
    for minutes, call in enumerate(calls):
        CallExecution.objects.filter(pk=call.pk).update(
            created_at=calls[0].created_at + timedelta(minutes=minutes)
        )
    return calls


FALLBACK_ROW_KEYS = {
    "overall_score",
    "count",
    "avg_overall_score",
    "avg_response_time",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "avg_latency_ms",
    "turn_count",
    "csat_score",
}


@pytest.mark.parametrize(
    "keys", [[], ["4"], [None]], ids=["no-keys", "key", "null-key"]
)
def test_unrecognised_only_groups_keep_the_display_fallback(
    auth_client, chat_run, keys
):
    # Frozen-dev control: no search and a single page, so fixes 1 and 3 do not apply.
    execution, add = chat_run
    _display_fallback_calls(add)

    data = _detail(auth_client, execution, row_groups=["turn_count"], group_keys=keys)
    # The fallback also groups by each call's created_at, so equal scores split.
    assert _groups(data, "overall_score") == [(4, 1), (2, 1), (4, 1)]
    assert all(set(row) == FALLBACK_ROW_KEYS for row in data["results"])
    assert (data["count"], data["next"], data["previous"]) == (3, None, None)


def test_unrecognised_only_groups_count_searched_calls_once(auth_client, chat_run):
    # Differs from frozen dev by fix 1 only: dev counts the first call twice.
    execution, add = chat_run
    first, *_ = _display_fallback_calls(add)
    _add_transcripts(first, 2)

    data = _detail(auth_client, execution, row_groups=["turn_count"], search="Checkout")
    assert _groups(data, "overall_score") == [(4, 1), (2, 1), (4, 1)]


def test_unrecognised_only_group_links_keep_the_request(auth_client, chat_run):
    # Differs from frozen dev by fix 3 only: dev's bare link drops the grouping.
    execution, add = chat_run
    _display_fallback_calls(add)

    first = _detail(auth_client, execution, row_groups=["turn_count"], limit=2)
    second = _follow(auth_client, execution, first["next"])
    assert _groups(first, "overall_score") == [(4, 1), (2, 1)]
    assert _groups(second, "overall_score") == [(4, 1)]
    assert _follow(auth_client, execution, second["previous"]) == first
