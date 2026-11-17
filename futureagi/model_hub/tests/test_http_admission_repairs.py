"""RED regressions for pending admissions, late evaluation writes and rendering.

These tests are authored, not executed by the static packet writer. The
rendering tests use tiny artificial budgets, not resource-exhaustion payloads.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.services import http_experiment_source as http_source
from model_hub.tests.test_http_experiment_source import entry, tenants as tenants
from model_hub.tests.test_http_runner_integration import (
    acknowledge, execution as execution, stage_http_execution,
)


@pytest.mark.django_db
@pytest.mark.parametrize('stop_path', ['request', 'callback'])
def test_pending_http_lost_ack_stop_settles_journal_and_allows_new_admission(tenants, stop_path):
    from model_hub.services.experiment_submit import (
        ADMISSIONS_KEY, STATE_KEY, admit_submission, state_of,
    )
    from tfc.temporal.experiments import activities

    actor, experiment, source, pending = stage_http_execution(tenants, accepted=False)
    admission_id = pending['admission_id']
    accepted_starts = []

    def lost_ack(**kwargs):
        accepted_starts.append(kwargs['admission_id'])
        raise TimeoutError('Start accepted; acknowledgement unavailable')

    with patch('tfc.temporal.experiments.start_experiment_v2_workflow', side_effect=lost_ack):
        with pytest.raises(TimeoutError):
            admit_submission(str(experiment.pk), admission_id)
    assert accepted_starts == [admission_id]
    assert state_of(experiment)['state'] == 'pending'
    assert not http_source.http_admission_ready(str(experiment.pk), admission_id)

    # Simulate a claim left by the older pending-enabled worker; the repaired
    # worker must never send while pending, but Stop must clean older evidence.
    row = Row.no_workspace_objects.get(dataset_id=experiment.snapshot_dataset_id, deleted=False)
    column = source.columns.get(source=SourceChoices.EXPERIMENT.value)
    cell = Cell.no_workspace_objects.create(
        dataset=experiment.snapshot_dataset, row=row, column=column,
        value='', status=CellStatus.RUNNING.value,
        value_infos=json.dumps({'http_attempt_id': http_source.http_attempt_id(admission_id, source.pk, row.pk)}),
    )
    eval_column = Column.no_workspace_objects.create(
        name='Pending HTTP evaluation', dataset=experiment.snapshot_dataset, data_type='text',
        source=SourceChoices.EXPERIMENT_EVALUATION.value, source_id='pending-http-evaluation',
    )
    source.columns.add(eval_column)
    eval_cell = Cell.no_workspace_objects.create(
        dataset=experiment.snapshot_dataset, row=row, column=eval_column,
        value='', status=CellStatus.RUNNING.value,
    )
    with patch.object(http_source.outbound_destination, 'request') as send:
        with pytest.raises(ValueError):
            http_source.setup_http_source(str(experiment.pk), str(source.pk), admission_id)
        if stop_path == 'request':
            response = actor['client'].post(f'/model-hub/experiments/v2/{experiment.pk}/stop/', {}, format='json')
            assert response.status_code == 200
            assert response.json()['result']['admission_state'] == 'cancelled'
        else:
            with patch.object(activities, 'close_old_connections'):
                result = activities._http_execution_control_sync(str(experiment.pk), admission_id, 'stop')
            assert result['status'] == 'COMPLETED'
    send.assert_not_called()
    experiment.refresh_from_db()
    experiment.snapshot_dataset.refresh_from_db()
    config = experiment.snapshot_dataset.dataset_config
    assert config[STATE_KEY]['state'] == 'cancelled'
    assert config[ADMISSIONS_KEY][admission_id]['state'] == 'cancelled'
    assert experiment.status == StatusType.CANCELLED.value
    cell.refresh_from_db()
    assert cell.status == CellStatus.ERROR.value
    assert json.loads(cell.value_infos)['error']['code'] == 'cancelled'
    eval_cell.refresh_from_db()
    assert eval_cell.status == CellStatus.ERROR.value

    with patch('tfc.temporal.experiments.start_experiment_v2_workflow', return_value='new-http-workflow'):
        response = actor['client'].post('/model-hub/experiments/v2/re-run/', {
            'experiment_ids': [str(experiment.pk)],
        }, format='json')
    assert response.status_code == 200, response.content
    latest = state_of(experiment)
    assert latest['admission_id'] != admission_id
    assert latest['state'] == 'accepted'
    source.refresh_from_db()
    assert source.legacy_prompt_config['_http_execution']['admission_id'] == latest['admission_id']


@pytest.mark.asyncio
async def test_http_parent_waits_for_ack_before_setup_or_dispatch(monkeypatch):
    from tfc.temporal.experiments import workflows
    from tfc.temporal.experiments.types import RunExperimentInput

    calls = []
    async def activity(name, arg, **options):
        calls.append(name)
        assert name == 'setup_experiment_activity'
        assert arg.admission_only and arg.http_admission_id == 'pending-admission'
        return {'status': 'ADMISSION_PENDING' if len(calls) == 1 else 'ADMISSION_CANCELLED'}

    async def sleep(_duration):
        return None

    monkeypatch.setattr(workflows.workflow, 'patched', lambda _name: True)
    monkeypatch.setattr(workflows.workflow, 'memo', lambda: {'experiment_submission_id': 'pending-admission'})
    monkeypatch.setattr(workflows.workflow, 'execute_activity', activity)
    monkeypatch.setattr(workflows.workflow, 'sleep', sleep)
    result = await workflows.RunExperimentV2Workflow().run(RunExperimentInput('experiment'))
    assert result.status == 'CANCELLED'
    assert len(calls) == 2


def _evaluation_fixture(execution):
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from model_hub.views.eval_runner import EvaluationRunner

    actor, experiment, source, plan = execution
    template = EvalTemplate.no_workspace_objects.create(
        name='HTTP admission fixture', organization=actor['org'], workspace=actor['workspace'],
        criteria='Check {{output}}', model='fixture-model',
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name='HTTP score', dataset=experiment.snapshot_dataset, organization=actor['org'],
        workspace=actor['workspace'], template=template, source_id=str(experiment.pk),
        status=StatusType.RUNNING.value, config={'mapping': {}, 'reason_column': True},
    )
    experiment.user_eval_template_ids.add(metric)
    output = Column.no_workspace_objects.get(pk=plan['column_id'])
    score = Column.no_workspace_objects.create(
        name='HTTP score', dataset=experiment.snapshot_dataset, data_type='text',
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f'{source.pk}-{output.pk}-sourceid-{metric.pk}',
    )
    reason = Column.no_workspace_objects.create(
        name='HTTP reason', dataset=experiment.snapshot_dataset, data_type='text',
        source=SourceChoices.EVALUATION_REASON.value, source_id=f'{score.pk}-sourceid-{metric.pk}',
    )
    source.columns.add(score, reason)
    context = {key: plan[key] for key in ('experiment_id', 'source_id', 'admission_id', 'dataset_id', 'column_id')}
    runner = EvaluationRunner(
        metric.pk, experiment_dataset=source, column=output, format_output=True,
        source_configs={'experiment_id': str(experiment.pk), 'http_context': context},
    )
    runner.dataset = experiment.snapshot_dataset
    runner.user_eval_metric = metric
    runner.replace_column_id = output.pk
    return runner, metric, score, reason, context


@pytest.mark.django_db
def test_current_http_evaluation_commits_scores_reasons_and_status(execution):
    runner, metric, score, reason, _ = _evaluation_fixture(execution)
    row = Row.no_workspace_objects.get(pk=execution[3]['row_ids'][0])
    runner._create_cell(runner.dataset, score, row, {'reason': 'current'}, '1', CellStatus.PASS.value)
    runner._create_reason_cell(runner.dataset, reason, row, {'reason': 'current'}, 'current', CellStatus.PASS.value)
    runner._check_and_update_eval_status(score.pk)
    assert Cell.no_workspace_objects.get(row=row, column=score).value == '1'
    assert Cell.no_workspace_objects.get(row=row, column=reason).value == 'current'
    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value


def test_non_http_evaluation_fence_is_a_noop():
    from model_hub.services.http_evaluation_fence import http_evaluation_write
    with http_evaluation_write(None):
        assert True


def _new_admission(execution):
    from django.db import transaction
    from model_hub.services.experiment_submit import stage_rerun_batch
    actor, experiment, source, _ = execution
    experiment.refresh_from_db()
    # The real control helper takes the same locks as every HTTP eval commit.
    from tfc.temporal.experiments import activities
    from model_hub.services.experiment_submit import state_of
    with patch.object(activities, 'close_old_connections'):
        result = activities._http_execution_control_sync(
            str(experiment.pk), state_of(experiment)['admission_id'], 'stop',
        )
    assert result['status'] == 'COMPLETED'
    experiment.refresh_from_db()
    with transaction.atomic():
        admissions = stage_rerun_batch([experiment], {'max_concurrent_rows': 1}, actor['workspace'])
        admission_id = admissions[str(experiment.pk)]
        http_source.bind_http_submission(experiment, admission_id, retry=True)
    acknowledge(experiment, admission_id)
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=['status'])
    return http_source.setup_http_source(str(experiment.pk), str(source.pk), admission_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('late_write', ['score', 'reason'])
def test_late_http_evaluator_cannot_overwrite_new_admission(execution, late_write):
    from model_hub.services.http_evaluation_fence import HttpEvaluationStopped
    actor, experiment, source, plan = execution
    runner, metric, score, reason, context = _evaluation_fixture(execution)
    row = Row.no_workspace_objects.get(pk=plan['row_ids'][0])
    entered, release = Event(), Event()

    def blocked_evaluator(_row, _mappings):
        entered.set()
        assert release.wait(10), 'test did not release evaluator'
        if late_write == 'reason':
            runner._create_reason_cell(runner.dataset, reason, row, {'reason': 'A reason'}, 'A reason', CellStatus.PASS.value)
        return {'reason': 'A reason'}, CellStatus.PASS.value, 'A score'

    # Exercise the real threaded update/reset and late error handling, but no
    # provider calls, usage lookup or evaluator registry is needed for this test.
    with patch.object(runner, '_process_eval_result', side_effect=blocked_evaluator), \
            patch.object(runner, '_get_column_config', return_value={}), \
            patch.object(runner, '_create_or_update_column', return_value=score):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runner.update_cell, [str(row.pk)])
            try:
                assert entered.wait(10), 'evaluator did not start'
                new_plan = _new_admission(execution)
                assert new_plan['admission_id'] != context['admission_id']
                for column, value in ((score, 'B score'), (reason, 'B reason')):
                    Cell.no_workspace_objects.update_or_create(
                        dataset=runner.dataset, row=row, column=column,
                        defaults={'value': value, 'value_infos': json.dumps({'reason': 'B reason'}), 'status': CellStatus.PASS.value},
                    )
                    column.status = StatusType.COMPLETED.value
                    column.save(update_fields=['status'])
                metric.status = StatusType.COMPLETED.value
                metric.save(update_fields=['status'])
                release.set()
                with pytest.raises(HttpEvaluationStopped):
                    future.result(timeout=10)
            finally:
                release.set()
    assert Cell.no_workspace_objects.get(row=row, column=score).value == 'B score'
    assert Cell.no_workspace_objects.get(row=row, column=reason).value == 'B reason'
    metric.refresh_from_db()
    score.refresh_from_db()
    assert metric.status == score.status == StatusType.COMPLETED.value


@pytest.mark.django_db
@pytest.mark.parametrize('operation', ['reset', 'metric_complete', 'metric_error', 'failed_policy'])
def test_delayed_http_eval_mutations_are_fenced_before_writing(execution, operation):
    from model_hub.services.http_evaluation_fence import HttpEvaluationStopped
    from tfc.temporal.experiments import activities
    actor, experiment, source, plan = execution
    runner, metric, score, reason, context = _evaluation_fixture(execution)
    row = Row.no_workspace_objects.get(pk=plan['row_ids'][0])
    _new_admission(execution)
    for column, value in ((score, 'B score'), (reason, 'B reason')):
        Cell.no_workspace_objects.update_or_create(
            dataset=runner.dataset, row=row, column=column,
            defaults={'value': value, 'value_infos': json.dumps({'reason': 'B reason'}), 'status': CellStatus.PASS.value},
        )
    metric.status = StatusType.COMPLETED.value
    metric.save(update_fields=['status'])
    if operation == 'failed_policy':
        with patch.object(activities, 'close_old_connections'):
            result = activities._create_error_eval_cells_sync(
                [str(row.pk)], plan['column_id'], plan['dataset_id'], str(experiment.pk),
                [str(metric.pk)], 'A failed', http_context=context,
            )
        assert result['status'] != 'COMPLETED'
        assert result['cells_created'] == 0
    elif operation == 'reset':
        with pytest.raises(HttpEvaluationStopped):
            runner.update_cell([str(row.pk)])
    else:
        # Avoid external usage machinery while taking the production metric
        # completion/error paths of run_prompt. A stale callback cannot write.
        with patch.object(runner, 'load_user_eval_metric'), \
                patch.object(runner, 'update_cell', side_effect=RuntimeError('A error') if operation == 'metric_error' else None), \
                patch.dict('sys.modules', {'ee.usage.services.metering': SimpleNamespace(
                    check_usage=lambda *_args: SimpleNamespace(allowed=True),
                )}), \
                patch('django.db.connections.all', return_value=[]):
            with pytest.raises(HttpEvaluationStopped):
                runner.run_prompt([str(row.pk)])
    assert Cell.no_workspace_objects.get(row=row, column=score).value == 'B score'
    assert Cell.no_workspace_objects.get(row=row, column=reason).value == 'B reason'
    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value


@pytest.mark.parametrize('template', ['text:{{small}}{{small}}', '{"reply":"{{small}}{{small}}"}', '[{{small}},{{small}}]'])
def test_rendered_body_budget_refuses_before_transport(monkeypatch, template):
    body = entry()
    body['http']['body_template'] = template
    body['variable_mapping'] = {'small': {'kind': 'constant', 'value': 'bounded-fixture'}}
    monkeypatch.setattr(http_source, 'MAX_RENDERED_BODY_BYTES', 20)
    with patch.object(http_source.outbound_destination, 'request') as send:
        result = http_source.execute_http_row(body, values={})
    assert result['error']['code'] == 'request_config'
    send.assert_not_called()


@pytest.mark.parametrize('json_template', [False, True])
def test_render_budget_rejects_before_joining_expansion(json_template):
    # A post-render size check would already have joined all fragments.
    with patch.object(http_source._RenderBuffer, 'finish', side_effect=AssertionError('joined over budget')) as finish:
        render = http_source._fill_json if json_template else http_source._fill_text
        template = '[{{small}},{{small}}]' if json_template else '{{small}}{{small}}'
        with pytest.raises(ValueError):
            render(template, lambda _name: 'bounded-fixture', 20)
    finish.assert_not_called()


def test_rendered_url_budget_counts_percent_encoded_utf8(monkeypatch):
    body = entry()
    body['http']['url'] = 'https://fixture.example/{{small}}'
    body['variable_mapping'] = {'small': {'kind': 'constant', 'value': 'éé'}}
    monkeypatch.setattr(http_source, 'MAX_RENDERED_URL_BYTES', 30)
    with patch.object(http_source.outbound_destination, 'request') as send:
        result = http_source.execute_http_row(body, values={})
    assert result['error']['code'] == 'request_config'
    send.assert_not_called()


def test_rendered_body_budget_accepts_exact_utf8_boundary(monkeypatch):
    body = entry()
    body['http']['body_template'] = 'q{{small}}'
    body['variable_mapping'] = {'small': {'kind': 'constant', 'value': 'éé'}}
    monkeypatch.setattr(http_source, 'MAX_RENDERED_BODY_BYTES', 5)
    assert http_source.build_request(body, values={})['body'] == 'qéé'
    monkeypatch.setattr(http_source, 'MAX_RENDERED_BODY_BYTES', 4)
    with pytest.raises(ValueError):
        http_source.build_request(body, values={})


def test_generated_http_parse_fixture_passes_strict_backend_entry_serializer():
    from model_hub.serializers.experiments import PromptConfigEntrySerializer
    fixture = Path(__file__).resolve().parents[3] / 'api_contracts/fixtures/http-experiment-create.json'
    # The frontend contract test asserts its parsed entry equals these bytes.
    parsed = json.loads(fixture.read_text())['prompt_config'][0]
    serializer = PromptConfigEntrySerializer(data=parsed)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == parsed
    forbidden = {'model_params', 'configuration', 'output_format', 'model', 'prompt_id', 'prompt_version'}
    assert not forbidden & parsed.keys()
