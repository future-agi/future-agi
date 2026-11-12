"""HTTP orchestration contracts; no live endpoint needed for these tests."""
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from model_hub.models.choices import StatusType
from model_hub.services import http_experiment_source as http_source
from model_hub.tests.test_http_runner_integration import execution as execution
from model_hub.tests.test_http_experiment_source import tenants as tenants, endpoint as endpoint
from tfc.utils import outbound_destination


@pytest.mark.asyncio
async def test_http_workflow_settles_unknown_once_and_wires_failed_policy(monkeypatch):
    from tfc.temporal.experiments import workflows
    from tfc.temporal.experiments.types import HttpSourceWorkflowInput

    calls = []
    async def activity(name, arg, **options):
        calls.append((name, arg, options))
        if name == 'setup_http_source_activity':
            return {'row_ids': ['ok', 'unknown'], 'column_id': 'output', 'dataset_id': 'snapshot'}
        if name == 'process_http_row_activity':
            assert options['retry_policy'].maximum_attempts == 1
            if arg.row_id == 'unknown':
                raise RuntimeError('Lost result delivery')
            return {'status': 'COMPLETED'}
        if name == 'settle_http_row_activity':
            assert arg.row_id == 'unknown'
            return {'status': 'FAILED'}
        if name == 'finish_http_source_activity':
            return {'status': 'COMPLETED', 'completed_rows': 1, 'failed_rows': 1}
        return {'status': 'COMPLETED'}

    monkeypatch.setattr(workflows.workflow, 'execute_activity', activity)
    result = await workflows.ProcessHttpSourceWorkflow().run(
        HttpSourceWorkflowInput('experiment', 'source', 'admission', eval_template_ids=['eval'], max_concurrent_rows=2)
    )
    assert result['completed_rows'] == 1 and result['failed_rows'] == 1
    assert sum(name == 'process_http_row_activity' for name, _, _ in calls) == 2
    assert sum(name == 'settle_http_row_activity' for name, _, _ in calls) == 1
    evaluations = [arg for name, arg, _ in calls if name == 'process_batch_template_eval_activity']
    assert len(evaluations) == 1 and evaluations[0].row_ids == ['ok']
    failed = [arg for name, arg, _ in calls if name == 'create_error_eval_cells_activity']
    assert len(failed) == 1 and failed[0].failed_row_ids == ['unknown']
    for arg in [*evaluations, *failed]:
        assert arg.http_context == {
            'experiment_id': 'experiment', 'source_id': 'source', 'admission_id': 'admission',
            'dataset_id': 'snapshot', 'column_id': 'output',
        }


@pytest.mark.django_db
def test_worker_secret_lookup_uses_execution_tenant_not_ambient_context(execution, tenants):
    from model_hub.models.api_key import SecretModel
    from model_hub.models.develop_dataset import Cell
    from tfc.middleware.workspace_context import clear_workspace_context, set_workspace_context

    actor, experiment, source, plan = execution
    other = tenants['b']
    SecretModel.no_workspace_objects.create(
        name='HTTP_OTHER_ONLY', key='foreign-fixture-value', organization=other['org'], workspace=other['workspace'],
    )
    source.refresh_from_db()
    source.legacy_prompt_config['http']['headers'].append({'name': 'X-Test', 'secret_ref': 'HTTP_OTHER_ONLY'})
    source.save(update_fields=['legacy_prompt_config'])
    set_workspace_context(organization=other['org'], workspace=other['workspace'])
    try:
        with patch.object(outbound_destination, 'request') as send:
            result = http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), plan['row_ids'][0], plan['admission_id'])
        send.assert_not_called()
        assert result['status'] == 'FAILED'
        cell = Cell.no_workspace_objects.get(row_id=plan['row_ids'][0], column_id=plan['column_id'])
        assert json.loads(cell.value_infos)['error']['code'] == 'header_reference'
        assert 'foreign-fixture-value' not in str(cell.value_infos) + str(cell.value)
    finally:
        clear_workspace_context()


@pytest.mark.django_db
def test_explicit_rerun_uses_new_identity_and_admission_recovery_does_not_reset(execution, endpoint):
    from django.db import transaction
    from model_hub.services.experiment_submit import stage_submission
    actor, experiment, source, plan = execution
    row = plan['row_ids'][0]
    http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), row, plan['admission_id'])
    http_source.finish_http_source(str(experiment.pk), str(source.pk), plan['admission_id'])
    with transaction.atomic():
        http_source.bind_http_submission(experiment, plan['admission_id'])
    source.refresh_from_db()
    assert source.legacy_prompt_config['_http_execution']['phase'] == 'finished'
    experiment.status = StatusType.COMPLETED.value
    experiment.save(update_fields=['status'])
    with transaction.atomic():
        admission = stage_submission(experiment, {'explicit': str(uuid4())}, 'rerun', authorized_workspace=actor['workspace'])
        http_source.bind_http_submission(experiment, admission['admission_id'], retry=True)
    assert admission['admission_id'] != plan['admission_id']
    from model_hub.tests.test_http_runner_integration import acknowledge
    acknowledge(experiment, admission['admission_id'])
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=['status'])
    http_source.setup_http_source(str(experiment.pk), str(source.pk), admission['admission_id'])
    result = http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), row, admission['admission_id'])
    assert result['status'] == 'COMPLETED'
    assert len(endpoint) == 2


@pytest.mark.django_db
def test_http_active_execution_blocks_edit_and_append_without_mutating_source(execution):
    actor, experiment, source, plan = execution
    source.refresh_from_db()
    original = json.dumps(source.legacy_prompt_config, sort_keys=True)
    payload = http_source.public_http_source(source)
    payload.pop('order')
    for append in (False, True):
        response = actor['client'].put(
            f'/model-hub/experiments/v2/{experiment.pk}/',
            {'prompt_config': [{key: value for key, value in payload.items() if key != 'id'}], **({'append_only': True} if append else {})},
            format='json', HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        assert response.status_code == 400
        assert b'active execution' in response.content
        source.refresh_from_db()
        assert json.dumps(source.legacy_prompt_config, sort_keys=True) == original


@pytest.mark.asyncio
async def test_parent_workflow_enumerates_http_sources_with_submission_identity(monkeypatch):
    import asyncio
    from tfc.temporal.experiments import workflows
    from tfc.temporal.experiments.types import RunExperimentInput

    children = []
    async def activity(name, arg, **options):
        if name == 'setup_experiment_activity':
            assert arg.http_admission_id == 'admission'
            return {'status': 'READY', 'dataset_id': 'snapshot', 'prompt_configs': [],
                    'agent_configs': [], 'http_sources': ['http-source'], 'eval_template_ids': [],
                    'has_base_column': False}
        return {'status': 'COMPLETED'}

    async def completed():
        return {'completed_rows': 2, 'failed_rows': 1}

    async def child(function, arg, **options):
        children.append((function, arg, options))
        return asyncio.create_task(completed())

    monkeypatch.setattr(workflows.workflow, 'patched', lambda name: True)
    monkeypatch.setattr(workflows.workflow, 'memo', lambda: {'experiment_submission_id': 'admission'})
    monkeypatch.setattr(workflows.workflow, 'execute_activity', activity)
    monkeypatch.setattr(workflows.workflow, 'start_child_workflow', child)
    result = await workflows.RunExperimentV2Workflow().run(RunExperimentInput('experiment'))
    assert len(children) == 1
    assert children[0][0] == workflows.ProcessHttpSourceWorkflow.run
    assert children[0][1].source_id == 'http-source'
    assert children[0][1].admission_id == 'admission'
    assert result.total_rows_processed == 2 and result.failed_rows == 1


@pytest.mark.django_db
def test_delayed_http_setup_cannot_revive_stop(execution):
    from model_hub.views.experiment_runner import ExperimentRunner
    actor, experiment, source, plan = execution
    experiment.status = StatusType.CANCELLED.value
    experiment.save(update_fields=['status'])
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    with pytest.raises(ValueError, match='stopped'):
        http_source.prepare_http_experiment(runner, plan['admission_id'])
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value
