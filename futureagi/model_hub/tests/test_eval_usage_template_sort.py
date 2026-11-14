"""Sort contract of POST /model-hub/get-eval-templates (Evaluations > Usage grid).

The grid sorts by three columns (``eval_template_name``, ``last_30_run``,
``updated_at``); older clients send their camelCase names. Any other sort
column id is a validation error, and the SQL builder refuses one as well.
"""

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status

from model_hub.models.choices import OwnerChoices, SourceChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.serializers.contracts import LegacyEvalTemplatesRequestSerializer
from model_hub.utils.SQL_queries import SQLQueryHandler

URL = "/model-hub/get-eval-templates"

GRID_SORT_COLUMNS = ["eval_template_name", "last_30_run", "updated_at"]
LEGACY_SORT_COLUMNS = ["evalTemplateName", "last30Run", "updatedAt"]
# Column ids the grid does not offer: an internal column of the query, the
# query's internal name for a grid column, and the unrouted legacy grid's
# "Avg score" field.
UNOFFERED_SORT_COLUMNS = ["template_id", "last30run", "average.average"]


def _usage_models():
    return pytest.importorskip("ee.usage.models.usage", reason="requires ee/")


def _create_template(organization, workspace, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": "def evaluate(output=None, **kwargs):\n    return True",
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _log_runs(organization, workspace, template, runs):
    usage_models = _usage_models()
    for _ in range(runs):
        usage_models.APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=usage_models.APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"output": {"output": True}}),
        )


@pytest.fixture
def sort_probe_templates(user, workspace):
    """Three templates whose name, run and updated orders all differ.

    name:        a < b < c
    last 30 run: a (1) < c (2) < b (3)
    updated at:  c (3 days ago) < a (2 days ago) < b (1 day ago)
    """
    now = timezone.now()
    specs = {
        "sort-probe-a": (1, now - timedelta(days=2)),
        "sort-probe-b": (3, now - timedelta(days=1)),
        "sort-probe-c": (2, now - timedelta(days=3)),
    }
    for name, (runs, updated_at) in specs.items():
        template = _create_template(user.organization, workspace, name)
        _log_runs(user.organization, workspace, template, runs)
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(
            updated_at=updated_at
        )
    return specs


def _post(auth_client, sort):
    return auth_client.post(
        URL,
        {
            "search_text": "sort-probe",
            "current_page_index": 0,
            "page_size": 10,
            "sort": sort,
        },
        format="json",
    )


def _template_list_queries(captured):
    return [q["sql"] for q in captured if "filtered_templates" in q["sql"]]


EXPECTED_ORDER = {
    ("eval_template_name", "ascending"): ["a", "b", "c"],
    ("eval_template_name", "descending"): ["c", "b", "a"],
    ("last_30_run", "ascending"): ["a", "c", "b"],
    ("last_30_run", "descending"): ["b", "c", "a"],
    ("updated_at", "ascending"): ["c", "a", "b"],
    ("updated_at", "descending"): ["b", "a", "c"],
}
LEGACY_TO_GRID = dict(zip(LEGACY_SORT_COLUMNS, GRID_SORT_COLUMNS, strict=True))


@pytest.mark.django_db
@pytest.mark.parametrize("direction", ["ascending", "descending"])
@pytest.mark.parametrize("column_id", GRID_SORT_COLUMNS + LEGACY_SORT_COLUMNS)
def test_usage_grid_sorts_by_each_offered_column(
    auth_client, sort_probe_templates, column_id, direction
):
    _usage_models()
    response = _post(auth_client, [{"column_id": column_id, "type": direction}])

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = response.data["result"]["row_data"]
    grid_column = LEGACY_TO_GRID.get(column_id, column_id)
    assert [row["eval_template_name"][-1] for row in rows] == EXPECTED_ORDER[
        (grid_column, direction)
    ]
    runs = {row["eval_template_name"]: row["last30_run"] for row in rows}
    assert runs == {name: spec[0] for name, spec in sort_probe_templates.items()}


@pytest.mark.django_db
def test_usage_grid_first_load_without_sort_still_lists_templates(
    auth_client, sort_probe_templates
):
    _usage_models()
    # The grid's first request: an empty sort model.
    response = _post(auth_client, [])

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["total_rows"] == 3
    # Default order is most recently updated first.
    assert [
        row["eval_template_name"][-1] for row in response.data["result"]["row_data"]
    ] == ["b", "a", "c"]


@pytest.mark.django_db
@pytest.mark.parametrize("column_id", UNOFFERED_SORT_COLUMNS)
def test_usage_grid_rejects_unoffered_sort_column_before_querying(
    auth_client, sort_probe_templates, column_id
):
    _usage_models()
    with CaptureQueriesContext(connection) as captured:
        response = _post(auth_client, [{"column_id": column_id, "type": "ascending"}])

    assert _template_list_queries(captured.captured_queries) == []
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "sort" in response.data["result"]


@pytest.mark.parametrize(
    "sort",
    [
        [{"column_id": column_id, "type": "ascending"}]
        for column_id in UNOFFERED_SORT_COLUMNS
    ]
    + [
        [{"column_id": "", "type": "ascending"}],
        [{"column_id": None, "type": "ascending"}],
        [{"column_id": ["updated_at"], "type": "ascending"}],
        [{"type": "ascending"}],
        ["updated_at"],
    ],
)
def test_legacy_eval_templates_request_rejects_unoffered_sort(sort):
    serializer = LegacyEvalTemplatesRequestSerializer(data={"sort": sort})

    assert not serializer.is_valid()
    assert "sort" in serializer.errors


@pytest.mark.parametrize(
    "payload",
    [
        # Exactly what EvalsUsageView sends on first load and after a click.
        {"search_text": "", "current_page_index": 0, "page_size": 10, "sort": []},
        *[
            {
                "search_text": "example",
                "current_page_index": 1,
                "page_size": 10,
                "sort": [{"column_id": column_id, "type": direction}],
            }
            for column_id in GRID_SORT_COLUMNS + LEGACY_SORT_COLUMNS
            for direction in ("ascending", "descending")
        ],
        {"current_page_index": 3, "page_size": 2},
    ],
)
def test_legacy_eval_templates_request_accepts_grid_payloads(payload):
    serializer = LegacyEvalTemplatesRequestSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == payload.get("sort", [])


class _RecordingQueries:
    def __init__(self):
        self.queries = []

    def __call__(self, query, params=None, **_kwargs):
        self.queries.append((query, params))
        return []


def _get_all_templates(sort_by, sort_order="DESC"):
    return SQLQueryHandler.get_all_templates(
        used_template_ids=["11111111-1111-4111-8111-111111111111"],
        search_name="",
        org_id="22222222-2222-4222-8222-222222222222",
        sort_order=sort_order,
        sort_by=sort_by,
        limit=10,
        offset=0,
    )


@pytest.mark.parametrize(
    ("sort_by", "expression"),
    [
        ("last_30_run", "last30run"),
        ("updated_at", "updated_at"),
        ("eval_template_name", "template_name"),
        ("last30Run", "last30run"),
        ("updatedAt", "updated_at"),
        ("evalTemplateName", "template_name"),
        (None, "last30run"),
        ("", "last30run"),
    ],
)
@pytest.mark.parametrize("sort_order", ["ASC", "DESC"])
def test_get_all_templates_orders_by_fixed_expression(
    monkeypatch, sort_by, expression, sort_order
):
    recorder = _RecordingQueries()
    monkeypatch.setattr(SQLQueryHandler, "execute_query", recorder)

    _get_all_templates(sort_by, sort_order)

    [(query, _params)] = recorder.queries
    assert f"ORDER BY {expression} {sort_order}, updated_at {sort_order}" in query


@pytest.mark.parametrize(
    ("sort_by", "sort_order"),
    [(column_id, "ASC") for column_id in UNOFFERED_SORT_COLUMNS]
    + [("updated_at", "descending"), ("updated_at", "random")],
)
def test_get_all_templates_refuses_unmapped_sort(monkeypatch, sort_by, sort_order):
    recorder = _RecordingQueries()
    monkeypatch.setattr(SQLQueryHandler, "execute_query", recorder)

    with pytest.raises(ValueError):
        _get_all_templates(sort_by, sort_order)

    assert recorder.queries == []
