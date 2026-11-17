"""A span added to a dataset copies only its own project's results.

A span id can be held by two projects: another organization's copy in
ClickHouse and a sender's Postgres span with the same id. Eval rows,
annotations and child trees are keyed by span or trace id, so the dataset
copy must keep each span to the results of its own project.
"""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    QueueItemSourceType,
    ScoreSource,
    SourceChoices,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.score import Score
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

OWNER_SECRET = "owner-only explanation"
VIRTUAL_FIELDS = ("eval_metrics", "annotation_metrics", "child_spans")
NUMERIC_SETTINGS = {"min": 0, "max": 10, "step_size": 1, "display_type": "slider"}


def _ch_only_span_row(project, trace_id, span_id, *, name, content, parent=""):
    """A span the project holds only in ClickHouse (no Postgres row)."""
    now = timezone.now()
    return {
        "id": span_id,
        "project_id": str(project.id),
        "trace_id": str(trace_id),
        "parent_span_id": parent,
        "org_id": str(project.organization_id),
        "name": name,
        "observation_type": "llm",
        "operation_name": "",
        "start_time": now - timedelta(seconds=2),
        "end_time": now - timedelta(seconds=1),
        "input": {"messages": [{"role": "user", "content": content}]},
        "output": {"choices": [{"message": {"content": content}}]},
        "model": "gpt-4",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "latency_ms": 100,
        "cost": 0.001,
        "status": "OK",
        "status_message": "",
        "tags": [],
        "metadata": {},
        "span_events": [],
        "span_attributes": {},
        "resource_attributes": {},
        "created_at": now,
        "updated_at": now,
        "deleted": False,
    }


def _postgres_span(span_id, project, trace, *, name, parent=None):
    now = timezone.now()
    return ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace,
        parent_span_id=parent,
        name=name,
        observation_type="llm" if parent is None else "tool",
        start_time=now - timedelta(seconds=10),
        end_time=now - timedelta(seconds=9),
        input={"messages": [{"role": "user", "content": f"{name} input"}]},
        output={"choices": [{"message": {"content": f"{name} output"}}]},
        model="gpt-4",
        status="OK",
    )


def _eval_row(span_id, *, key, score, explanation, trace_id, config=None):
    # save() runs full_clean() apart from the span and trace links, which may
    # name rows that live only in ClickHouse.
    return EvalLogger.objects.create(
        trace_id=trace_id,
        observation_span_id=span_id,
        custom_eval_config=config,
        eval_type_id=key,
        output_float=score,
        results_explanation={"reason": explanation},
    )


def _label(name, *, organization, workspace, project):
    # save() runs full_clean(); a numeric label needs all of its settings.
    return AnnotationsLabels.objects.create(
        name=name,
        type=AnnotationTypeChoices.NUMERIC.value,
        organization=organization,
        workspace=workspace,
        project=project,
        settings=dict(NUMERIC_SETTINGS),
    )


def _score(span_id, label, *, organization, workspace, tracer_project_id, value):
    score = Score(
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span_id,
        label=label,
        value={"value": value},
        score_source=ScoreSource.HUMAN.value,
        organization=organization,
        workspace=workspace,
        tracer_project_id=tracer_project_id,
    )
    score.full_clean()
    score.save()
    return score


def _copy_into_dataset(dataset, columns, span_ids, *, project_id, org_id):
    """Run the task the dataset views dispatch; return each column's cell."""
    from tracer.tasks.dataset import process_spans_chunk_task

    mapping = [
        {"column_id": str(column.id), "column_name": name, "span_field": name}
        for name, column in columns.items()
    ]
    # The task runs on a worker that holds no request workspace. The fixtures
    # set the sender's workspace, which would hide the owner's scores from the
    # read before the task scopes anything.
    clear_workspace_context()
    result = process_spans_chunk_task.run_sync(
        span_ids, str(dataset.id), mapping, project_id, org_id
    )
    assert result["rows_created"] == len(span_ids)
    return {
        name: Cell.objects.get(dataset=dataset, column=column).value
        for name, column in columns.items()
    }


@pytest.fixture
def dataset(db, organization, user, workspace):
    """The sender's dataset."""
    return Dataset.objects.create(
        id=uuid.uuid4(),
        name="Sender Dataset",
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        source=DatasetSourceChoices.OBSERVE.value,
        user=user,
    )


@pytest.fixture
def virtual_columns(db, dataset):
    return {
        name: Column.objects.create(
            id=uuid.uuid4(),
            name=name,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
        )
        for name in VIRTUAL_FIELDS
    }


@pytest.fixture
def owner(db):
    """Another organization whose project holds the span id in ClickHouse."""
    suffix = uuid.uuid4().hex[:8]
    organization = Organization.objects.create(name=f"Owner Organization {suffix}")
    user = User.objects.create_user(
        email=f"owner-{suffix}@futureagi.com",
        password="testpassword123",
        name="Owner User",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name=f"Owner Workspace {suffix}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    project = Project.no_workspace_objects.create(
        name=f"Owner Project {suffix}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace = Trace.no_workspace_objects.create(project=project, name="Owner Trace")
    template = EvalTemplate.objects.create(
        name=f"Owner Eval Template {suffix}",
        description="The owner's evaluation",
        organization=organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "Owner criteria"},
    )
    config = CustomEvalConfig.objects.create(
        name="Owner Eval",
        project=project,
        eval_template=template,
        config={},
        mapping={"input": "input"},
        filters={},
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        project=project,
        trace=trace,
        config=config,
    )


@pytest.fixture
def sender_config(db, observe_project, eval_template):
    return CustomEvalConfig.objects.create(
        name="Sender Eval",
        project=observe_project,
        eval_template=eval_template,
        config={},
        mapping={"input": "input"},
        filters={},
    )


@pytest.fixture
def shared_span(
    db,
    owner,
    organization,
    workspace,
    observe_project,
    session_trace,
    sender_config,
    ch_seed,
):
    """Span X: the owner's in ClickHouse, the sender's in Postgres, with each
    side's eval rows, annotations and a child span on it."""
    span_id = f"shared_span_{uuid.uuid4().hex[:16]}"
    sender_span = _postgres_span(
        span_id, observe_project, session_trace, name="Sender Root"
    )
    sender_child = _postgres_span(
        f"sender_child_{uuid.uuid4().hex[:16]}",
        observe_project,
        session_trace,
        name="Sender Child",
        parent=span_id,
    )
    ch_seed([sender_span, sender_child])
    # The owner's rows arrive later, so they are the newest ClickHouse rows
    # for the id.
    owner_child_id = f"owner_child_{uuid.uuid4().hex[:16]}"
    ch_seed(
        [
            _ch_only_span_row(
                owner.project,
                owner.trace.id,
                span_id,
                name="Owner Root",
                content=OWNER_SECRET,
            ),
            _ch_only_span_row(
                owner.project,
                owner.trace.id,
                owner_child_id,
                name="Owner Child",
                content=OWNER_SECRET,
                parent=span_id,
            ),
        ]
    )

    _eval_row(
        span_id,
        key="owner_config_eval",
        score=0.91,
        explanation=OWNER_SECRET,
        trace_id=owner.trace.id,
        config=owner.config,
    )
    _eval_row(
        span_id,
        key="owner_imported_score",
        score=0.88,
        explanation=OWNER_SECRET,
        trace_id=owner.trace.id,
    )
    # The sender's config row names a trace held only in ClickHouse; its
    # imported score has no config and sits on the sender's Postgres trace,
    # as the Langfuse upsert writes it.
    _eval_row(
        span_id,
        key="sender_config_eval",
        score=0.42,
        explanation="sender config verdict",
        trace_id=uuid.uuid4(),
        config=sender_config,
    )
    _eval_row(
        span_id,
        key="sender_imported_score",
        score=0.73,
        explanation="sender imported verdict",
        trace_id=session_trace.id,
    )

    owner_scope = {
        "organization": owner.organization,
        "workspace": owner.workspace,
    }
    sender_scope = {"organization": organization, "workspace": workspace}
    _score(
        span_id,
        _label("owner-rating", project=owner.project, **owner_scope),
        tracer_project_id=owner.project.id,
        value=9,
        **owner_scope,
    )
    # A score written before scores recorded their tracer project.
    _score(
        span_id,
        _label("owner-legacy-rating", project=owner.project, **owner_scope),
        tracer_project_id=None,
        value=8,
        **owner_scope,
    )
    _score(
        span_id,
        _label("sender-rating", project=observe_project, **sender_scope),
        tracer_project_id=observe_project.id,
        value=6,
        **sender_scope,
    )
    _score(
        span_id,
        _label("sender-legacy-rating", project=observe_project, **sender_scope),
        tracer_project_id=None,
        value=4,
        **sender_scope,
    )
    return SimpleNamespace(
        span_id=span_id,
        sender_child_id=sender_child.id,
        owner_child_id=owner_child_id,
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestDatasetCopyProjectScope:
    def test_sender_dataset_copies_none_of_the_owner_eval_results(
        self, dataset, virtual_columns, observe_project, organization, shared_span
    ):
        cells = _copy_into_dataset(
            dataset,
            virtual_columns,
            [shared_span.span_id],
            project_id=str(observe_project.id),
            org_id=str(organization.id),
        )

        evals = json.loads(cells["eval_metrics"])
        assert "owner_config_eval" not in evals
        assert "owner_imported_score" not in evals
        assert OWNER_SECRET not in cells["eval_metrics"]

    def test_sender_dataset_copies_none_of_the_owner_annotations(
        self, dataset, virtual_columns, observe_project, organization, shared_span
    ):
        cells = _copy_into_dataset(
            dataset,
            virtual_columns,
            [shared_span.span_id],
            project_id=str(observe_project.id),
            org_id=str(organization.id),
        )

        annotations = json.loads(cells["annotation_metrics"])
        assert "owner-rating" not in annotations
        assert "owner-legacy-rating" not in annotations

    def test_without_a_task_project_the_span_project_scopes_the_copy(
        self, dataset, virtual_columns, organization, shared_span
    ):
        cells = _copy_into_dataset(
            dataset,
            virtual_columns,
            [shared_span.span_id],
            project_id=None,
            org_id=str(organization.id),
        )

        evals = json.loads(cells["eval_metrics"])
        assert set(evals) == {"sender_config_eval", "sender_imported_score"}
        annotations = json.loads(cells["annotation_metrics"])
        assert set(annotations) == {"sender-rating", "sender-legacy-rating"}
        assert OWNER_SECRET not in cells["child_spans"]
        assert shared_span.owner_child_id not in cells["child_spans"]
        assert shared_span.sender_child_id in cells["child_spans"]

    def test_sender_own_results_on_the_shared_span_still_copy(
        self, dataset, virtual_columns, observe_project, organization, shared_span
    ):
        cells = _copy_into_dataset(
            dataset,
            virtual_columns,
            [shared_span.span_id],
            project_id=str(observe_project.id),
            org_id=str(organization.id),
        )

        evals = json.loads(cells["eval_metrics"])
        assert evals["sender_config_eval"]["score"] == 0.42
        assert evals["sender_config_eval"]["explanation"] == {
            "reason": "sender config verdict"
        }
        assert evals["sender_imported_score"]["score"] == 0.73
        annotations = json.loads(cells["annotation_metrics"])
        assert annotations["sender-rating"] == {"value": 6}
        assert annotations["sender-legacy-rating"] == {"value": 4}
        assert shared_span.sender_child_id in cells["child_spans"]

    def test_unshared_span_copies_its_results_as_before(
        self,
        dataset,
        virtual_columns,
        organization,
        workspace,
        observe_project,
        session_trace,
        sender_config,
        ch_seed,
    ):
        span = _postgres_span(
            f"unshared_span_{uuid.uuid4().hex[:16]}",
            observe_project,
            session_trace,
            name="Unshared Root",
        )
        ch_seed([span])
        _eval_row(
            span.id,
            key="sender_config_eval",
            score=0.5,
            explanation="unshared verdict",
            trace_id=session_trace.id,
            config=sender_config,
        )
        _score(
            span.id,
            _label(
                "sender-rating",
                organization=organization,
                workspace=workspace,
                project=observe_project,
            ),
            organization=organization,
            workspace=workspace,
            tracer_project_id=observe_project.id,
            value=7,
        )

        cells = _copy_into_dataset(
            dataset,
            virtual_columns,
            [span.id],
            project_id=str(observe_project.id),
            org_id=str(organization.id),
        )

        assert json.loads(cells["eval_metrics"]) == {
            "sender_config_eval": {
                "score": 0.5,
                "explanation": {"reason": "unshared verdict"},
                "error": False,
                "error_message": None,
            }
        }
        assert json.loads(cells["annotation_metrics"]) == {
            "sender-rating": {"value": 7}
        }
