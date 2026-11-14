"""Localization follows the project-scoped CH span, never the PG telemetry FK."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

import model_hub.tasks  # noqa: F401 — initialize the tasks/eval import cycle
from model_hub.models.error_localizer_model import (
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.tasks.user_evaluation import (
    process_single_error_localization,
    trigger_error_localization_for_span,
)
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.eval_tasks.entries import (
    persist_eval_result,
    running_entry_epoch,
    writing_onto_entry,
)
from tracer.services.eval_tasks.run_entry import _run_for_target
from tracer.tests._ch_seed import (
    _ensure_ch_eval_logger_table,
    _get_ch_client,
    seed_ch_span,
    seed_ch_trace,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def localization_entry(project, custom_eval_config, eval_template):
    """Real PG entry with deliberately absent PG span and trace targets."""
    eval_template.config = {
        "output": "boolean",
        "rule_prompt": "The date must be Monday.",
    }
    eval_template.save(update_fields=["config"])
    custom_eval_config.config = {"run_config": {"error_localizer_enabled": True}}
    custom_eval_config.save(update_fields=["config"])
    now = timezone.now() - timedelta(minutes=1)
    trace = Trace(
        id=uuid.uuid4(),
        project=project,
        name="delivery",
        created_at=now,
        updated_at=now,
    )
    span = ObservationSpan(
        id=uuid.uuid4().hex[:16],
        trace=trace,
        project=project,
        name="answer",
        observation_type="llm",
        input="It arrives Monday.",
        output="It arrives Friday.",
        start_time=now,
        end_time=now + timedelta(seconds=1),
        created_at=now,
        updated_at=now,
    )
    seed_ch_trace(trace)
    seed_ch_span(span)
    entry = EvalLogger(
        trace_id=trace.id,
        observation_span_id=span.id,
        custom_eval_config=custom_eval_config,
        status=EvalEntryStatus.RUNNING,
    )
    EvalLogger.objects.bulk_create([entry])
    return entry, span


def _evaluate(entry, config, project):
    with running_entry_epoch(entry.updated_at):
        _run_for_target(entry, config, task_project=project)


@pytest.mark.parametrize("pg_span", [False, True])
@pytest.mark.parametrize("fallback_workspace", [False, True])
def test_engine_creates_and_updates_localization_without_pg_span_lookup(
    localization_entry,
    custom_eval_config,
    project,
    workspace,
    stub_run_eval,
    stub_cost_log,
    pg_span,
    fallback_workspace,
):
    entry, span = localization_entry
    if pg_span:
        span.trace.save()
        span.save()
    if fallback_workspace:
        project.workspace = None
        project.save(update_fields=["workspace"])
    stub_run_eval(value=False, reason="The response uses the wrong date.")

    _evaluate(entry, custom_eval_config, project)

    task = ErrorLocalizerTask.objects.get(source_id=entry.id)
    assert task.status == ErrorLocalizerStatus.PENDING
    assert task.organization_id == project.organization_id
    assert task.workspace_id == workspace.id
    assert task.eval_result is False
    entry.refresh_from_db()
    assert entry.output_bool is False
    assert not entry.error
    # Reruns update the same source rather than duplicating localization work.
    _evaluate(entry, custom_eval_config, project)
    assert ErrorLocalizerTask.objects.filter(source_id=entry.id).count() == 1
    assert ObservationSpan.objects.filter(id=span.id).exists() is pg_span
    assert Trace.objects.filter(id=span.trace_id).exists() is pg_span


@pytest.mark.parametrize("lifecycle", ["reclaimed", "deleted"])
@pytest.mark.parametrize("existing_task", [False, True])
def test_rejected_result_has_no_localization_side_effects(
    localization_entry,
    custom_eval_config,
    project,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
    lifecycle,
    existing_task,
):
    from evaluations.engine import run_eval as original_run

    entry, span = localization_entry
    if existing_task:
        # PG-backed targets demonstrate that a rejected write must not reset
        # existing localization even when the old FK traversal would succeed.
        span.trace.save()
        span.save()
        trigger_error_localization_for_span(
            eval_template=custom_eval_config.eval_template,
            eval_logger=entry,
            observation_span=span,
            value=False,
            mapping={"output": "original input"},
            eval_explanation="original",
        )
        task = ErrorLocalizerTask.objects.get(source_id=entry.id)
        task.mark_as_completed({"original": []}, "output")

    def abandon_during_evaluation(request):
        if lifecycle == "deleted":
            EvalLogger.objects.filter(id=entry.id).update(deleted=True)
        else:
            EvalLogger.objects.filter(id=entry.id).update(
                updated_at=entry.updated_at + timedelta(seconds=1)
            )
        return original_run(request)

    stub_run_eval(value=False)
    monkeypatch.setattr("evaluations.engine.run_eval", abandon_during_evaluation)
    _evaluate(entry, custom_eval_config, project)
    if existing_task:
        task.refresh_from_db()
        assert task.status == ErrorLocalizerStatus.COMPLETED
        assert task.input_data == {"output": "original input"}
    else:
        assert not ErrorLocalizerTask.objects.filter(source_id=entry.id).exists()
    with running_entry_epoch(entry.updated_at), writing_onto_entry(entry.id):
        assert persist_eval_result({"eval_explanation": "abandoned"}) is None


@pytest.mark.parametrize("mismatch", ["span", "trace", "project", "template"])
def test_mismatched_context_cannot_start_localization(
    localization_entry,
    custom_eval_config,
    eval_template,
    mismatch,
):
    entry, span = localization_entry
    if mismatch == "span":
        entry.observation_span_id = "another-span"
    elif mismatch == "trace":
        entry.trace_id = uuid.uuid4()
    elif mismatch == "project":
        span.project_id = uuid.uuid4()
    else:
        eval_template.id = uuid.uuid4()
    trigger_error_localization_for_span(
        eval_template=eval_template,
        eval_logger=entry,
        observation_span=span,
        value=False,
        mapping={"output": "Friday"},
        eval_explanation="Wrong date.",
    )
    assert not ErrorLocalizerTask.objects.filter(source_id=entry.id).exists()


def test_localization_worker_writeback_and_clickhouse_detail_read(
    localization_entry,
    custom_eval_config,
    project,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
    auth_client,
):
    entry, span = localization_entry
    stub_run_eval(value=False)
    _evaluate(entry, custom_eval_config, project)
    task = ErrorLocalizerTask.objects.get(source_id=entry.id)
    findings = {
        "input_1": [
            {
                "rank": "1",
                "orgSen": {"text": "It arrives Friday.", "start_idx": 0, "end_idx": 18},
            }
        ]
    }
    localizer = MagicMock()
    localizer.localize_errors.return_value = SimpleNamespace(
        analysis=findings,
        selected_key="output",
        skip_reason=None,
    )
    monkeypatch.setattr(
        "model_hub.tasks.user_evaluation.ErrorLocalizer", lambda **kw: localizer
    )
    log = MagicMock(status="processing")
    from tfc.constants.api_calls import APICallStatusChoices

    log.status = APICallStatusChoices.PROCESSING.value
    monkeypatch.setattr(
        "model_hub.tasks.user_evaluation.log_and_deduct_cost_for_api_request",
        lambda **kw: log,
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *a, **k: SimpleNamespace(allowed=True),
    )
    # pytest's outer transaction must remain open while the inline worker runs.
    monkeypatch.setattr(
        "model_hub.tasks.user_evaluation.close_old_connections", lambda: None
    )
    process_single_error_localization._original_func(str(task.id))

    task.refresh_from_db()
    entry.refresh_from_db()
    assert task.status == ErrorLocalizerStatus.COMPLETED
    assert entry.output_bool is False
    assert entry.output_metadata["error_analysis"] == findings
    assert not ObservationSpan.objects.filter(id=span.id).exists()
    assert not Trace.objects.filter(id=span.trace_id).exists()
    # The test stack has no CDC; explicitly mirror the real worker's PG output.
    with _get_ch_client() as client:
        _ensure_ch_eval_logger_table(client)
        client.insert(
            "tracer_eval_logger",
            [
                [
                    str(entry.id),
                    str(entry.trace_id),
                    entry.observation_span_id,
                    str(entry.custom_eval_config_id),
                    entry.target_type,
                    entry.output_bool,
                    json.dumps(entry.output_metadata),
                    entry.created_at,
                    entry.updated_at,
                    1,
                ]
            ],
            column_names=[
                "id",
                "trace_id",
                "observation_span_id",
                "custom_eval_config_id",
                "target_type",
                "output_bool",
                "output_metadata",
                "created_at",
                "updated_at",
                "_peerdb_version",
            ],
        )
    response = auth_client.get(
        "/tracer/observation-span/get_evaluation_details/",
        {
            "observation_span_id": span.id,
            "custom_eval_config_id": str(custom_eval_config.id),
            "project_id": str(project.id),
        },
    )
    assert response.status_code == 200, response.content
    payload = response.json().get("result", response.json())
    assert payload["error_analysis"] == findings
    assert payload["score"] == 0


@pytest.mark.parametrize("run_type", ["historical", "continuous"])
@pytest.mark.parametrize("row_type", ["spans", "voice_calls"])
def test_task_engine_span_and_voice_paths_create_localization(
    localization_entry,
    custom_eval_config,
    project,
    stub_run_eval,
    stub_cost_log,
    run_type,
    row_type,
):
    from tracer.models.eval_task import EvalTask
    from tracer.services.eval_tasks.run_entry import run_entry

    entry, _span = localization_entry
    task = EvalTask.objects.create(
        project=project,
        name="localization task",
        run_type=run_type,
        row_type=row_type,
    )
    task.evals.add(custom_eval_config)
    EvalLogger.objects.filter(id=entry.id).update(eval_task_id=str(task.id))
    entry.refresh_from_db()
    stub_run_eval(value=False)
    assert run_entry(entry) == "completed"
    assert ErrorLocalizerTask.objects.filter(source_id=entry.id).count() == 1


def test_legacy_span_evaluation_preserves_localization(
    localization_entry,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    entry, span = localization_entry
    span.trace.save()
    span.save()
    stub_run_eval(value=False)
    _execute_evaluation(
        observation_span_id=span.id,
        observation_span=span,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=None,
        run_params={"output": "It arrives Friday."},
        type=OBSERVE,
    )
    assert ErrorLocalizerTask.objects.filter(source_id=entry.id).count() == 1


def test_other_organization_span_context_is_rejected(
    localization_entry,
    eval_template,
):
    from accounts.models import Organization
    from tracer.models.project import Project

    entry, span = localization_entry
    other_org = Organization.objects.create(name="Other synthetic tenant")
    span.project = Project.objects.create(
        name="Other project",
        organization=other_org,
        model_type=span.project.model_type,
        trace_type="observe",
    )
    trigger_error_localization_for_span(
        eval_template=eval_template,
        eval_logger=entry,
        observation_span=span,
        value=False,
        mapping={"output": "Friday"},
        eval_explanation="Wrong date.",
    )
    assert not ErrorLocalizerTask.objects.filter(source_id=entry.id).exists()
