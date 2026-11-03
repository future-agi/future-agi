"""A workspace-scoped span list over a trace held by two projects.

A trace id is not unique across projects: an OTLP replay of the same corpus, a
re-import, or a shared provider account writes the same ``trace_id`` and span
ids into more than one project. The user detail page's Spans tab lists spans
for the whole workspace (no project picked), so one page can hold both
projects' copies of the same span. Each copy is its own row, identified by
``(project_id, trace_id, span_id)``, carries its ``project_id`` so the drawer
can pin the detail read to it, and shows only its own project's evals and
annotations.

These tests run the real view against the live test ClickHouse.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.tests._ch_seed import _get_ch_client, seed_ch_spans

LIST_SPANS = "/tracer/observation-span/list_spans_observe/"


def _observe_project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )


def _insert(client, table, rows):
    columns = list(rows[0])
    client.insert(
        table, [[row[c] for c in columns] for row in rows], column_names=columns
    )


@pytest.fixture
def copies(db, organization, workspace, ch_seed):
    """One trace (root + child) written into projects A and B for one end user.

    Each project has its own eval config with a score on its copy of the child
    span. One label carries a score stamped with each project on the child span
    and a legacy score (no ``tracer_project_id``) on the root span.
    """

    project_a = _observe_project(organization, workspace, "Copy A")
    project_b = _observe_project(organization, workspace, "Copy B")
    template = EvalTemplate.objects.create(
        name=f"g2-template-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        config={"type": "score"},
    )
    configs = {
        str(project.id): CustomEvalConfig.objects.create(
            name=f"g2-eval-{project.name}",
            project=project,
            eval_template=template,
            config={},
            mapping={},
            filters={},
        )
        for project in (project_a, project_b)
    }
    label = AnnotationsLabels.objects.create(
        name="g2-quality",
        type="numeric",
        organization=organization,
        workspace=workspace,
        project=project_a,
        settings={"min": 0, "max": 5, "step_size": 1, "display_type": "slider"},
    )
    user_id = f"g2-user-{uuid.uuid4().hex[:8]}"
    trace_id = str(uuid.uuid4())
    root_id = uuid.uuid4().hex[:16]
    child_id = uuid.uuid4().hex[:16]
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    end_users = {}
    spans = []
    for offset, project in enumerate((project_a, project_b)):
        end_user_id = str(uuid.uuid4())
        end_users[str(project.id)] = end_user_id
        for span_id, parent, name in (
            (root_id, None, f"root-{project.name}"),
            (child_id, root_id, f"child-{project.name}"),
        ):
            start = started + timedelta(seconds=offset)
            spans.append(
                {
                    "id": span_id,
                    "trace_id": trace_id,
                    "project_id": str(project.id),
                    "org_id": str(organization.id),
                    "parent_span_id": parent,
                    "name": name,
                    "observation_type": "llm" if parent else "chain",
                    "start_time": start,
                    "end_time": start + timedelta(seconds=1),
                    "created_at": start,
                    "input": json.dumps({"copy": project.name}),
                    "output": "",
                    "end_user_id": end_user_id,
                    "span_attributes": {"user.id": user_id},
                }
            )
    seed_ch_spans(spans)

    eval_scores = {str(project_a.id): 0.8, str(project_b.id): 0.3}
    label_values = {str(project_a.id): 4, str(project_b.id): 2}
    eval_table, _ = eval_logger_source()
    client = _get_ch_client()
    try:
        _insert(
            client,
            "end_users",
            [
                {
                    "project_id": uuid.UUID(project_id),
                    "end_user_id": uuid.UUID(end_user_id),
                    "organization_id": organization.id,
                    "user_id": user_id,
                    "user_id_type": "custom",
                    "first_seen": started,
                    "version": datetime.now(UTC),
                    "is_deleted": 0,
                }
                for project_id, end_user_id in end_users.items()
            ],
        )
        _insert(
            client,
            eval_table,
            [
                {
                    "id": uuid.uuid4(),
                    "trace_id": uuid.UUID(trace_id),
                    "observation_span_id": child_id,
                    "target_type": "span",
                    "custom_eval_config_id": configs[project_id].id,
                    "output_float": score,
                    "error": 0,
                    "created_at": started,
                }
                for project_id, score in eval_scores.items()
            ],
        )
        score_rows = [
            (child_id, uuid.UUID(project_id), value)
            for project_id, value in label_values.items()
        ] + [(root_id, None, 5)]
        _insert(
            client,
            "model_hub_score",
            [
                {
                    "id": uuid.uuid4(),
                    "source_type": "observation_span",
                    "trace_id": uuid.UUID(trace_id),
                    "observation_span_id": span_id,
                    "tracer_project_id": tracer_project_id,
                    "label_id": label.id,
                    "value": json.dumps({"value": value}),
                    "organization_id": organization.id,
                    "deleted": 0,
                    "created_at": started,
                    "updated_at": started,
                    "_peerdb_synced_at": started,
                    "_peerdb_is_deleted": 0,
                    "_peerdb_version": 1,
                }
                for span_id, tracer_project_id, value in score_rows
            ],
        )
    finally:
        client.close()
    yield {
        "project_a": str(project_a.id),
        "project_b": str(project_b.id),
        "config_a": str(configs[str(project_a.id)].id),
        "config_b": str(configs[str(project_b.id)].id),
        "label": str(label.id),
        "user_id": user_id,
        "trace_id": trace_id,
        "root_id": root_id,
        "child_id": child_id,
        "started": started,
    }
    client = _get_ch_client()
    try:
        for table in ("end_users", eval_table, "model_hub_score"):
            client.command(f"TRUNCATE TABLE IF EXISTS {table}")
    finally:
        client.close()


def _user_spans_request(copies, **extra):
    """The request the user page's Spans tab sends (workspace scope)."""

    window_start = copies["started"] - timedelta(hours=1)
    window_end = copies["started"] + timedelta(hours=1)
    return {
        "page_size": 25,
        "page_number": 0,
        "cursor_mode": True,
        "filters": json.dumps(
            [
                {
                    "column_id": "user_id",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": copies["user_id"],
                    },
                },
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            window_start.isoformat().replace("+00:00", "Z"),
                            window_end.isoformat().replace("+00:00", "Z"),
                        ],
                    },
                },
            ]
        ),
        **extra,
    }


def _rows_by_identity(response):
    assert response.status_code == status.HTTP_200_OK, response.data
    return {
        (row["project_id"], row["trace_id"], row["span_id"]): row
        for row in response.data["result"]["table"]
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_user_span_list_returns_every_projects_copy(auth_client, copies):
    response = auth_client.post(LIST_SPANS, _user_spans_request(copies), format="json")

    rows = _rows_by_identity(response)
    assert len(response.data["result"]["table"]) == 4
    assert set(rows) == {
        (project_id, copies["trace_id"], span_id)
        for project_id in (copies["project_a"], copies["project_b"])
        for span_id in (copies["root_id"], copies["child_id"])
    }
    for (project_id, _trace_id, span_id), row in rows.items():
        side = "A" if project_id == copies["project_a"] else "B"
        kind = "child" if span_id == copies["child_id"] else "root"
        assert row["span_name"] == f"{kind}-Copy {side}"


@pytest.mark.integration
@pytest.mark.django_db
def test_user_span_list_shows_each_copy_only_its_own_evals_and_scores(
    auth_client, copies
):
    response = auth_client.post(LIST_SPANS, _user_spans_request(copies), format="json")

    rows = _rows_by_identity(response)
    trace_id = copies["trace_id"]
    child_a = rows[(copies["project_a"], trace_id, copies["child_id"])]
    child_b = rows[(copies["project_b"], trace_id, copies["child_id"])]
    assert child_a[copies["config_a"]] == 80.0
    assert copies["config_b"] not in child_a
    assert child_b[copies["config_b"]] == 30.0
    assert copies["config_a"] not in child_b
    # Scores stamped with a project stay on that project's copy.
    assert child_a[copies["label"]] == 4
    assert child_b[copies["label"]] == 2
    # A legacy score without a project cannot be attributed: every copy keeps it.
    for project_id in (copies["project_a"], copies["project_b"]):
        assert rows[(project_id, trace_id, copies["root_id"])][copies["label"]] == 5


@pytest.mark.integration
@pytest.mark.django_db
def test_single_project_span_list_is_unchanged(auth_client, copies):
    response = auth_client.post(
        LIST_SPANS,
        _user_spans_request(copies, project_id=copies["project_a"]),
        format="json",
    )

    rows = _rows_by_identity(response)
    trace_id = copies["trace_id"]
    assert set(rows) == {
        (copies["project_a"], trace_id, copies["root_id"]),
        (copies["project_a"], trace_id, copies["child_id"]),
    }
    child = rows[(copies["project_a"], trace_id, copies["child_id"])]
    assert child[copies["config_a"]] == 80.0
    assert copies["config_b"] not in child
    root = rows[(copies["project_a"], trace_id, copies["root_id"])]
    assert root[copies["label"]] == 5
