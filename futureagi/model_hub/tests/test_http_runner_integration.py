"""RED-first HTTP dispatch regressions. Authored statically; not executed here."""
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.experiments import ExperimentsTable
from model_hub.services import http_experiment_source as http_source
from model_hub.tests.test_http_experiment_source import entry, endpoint, tenants
from tfc.utils import outbound_destination


def acknowledge(experiment, admission_id):
    from model_hub.services.experiment_submit import admit_submission
    with patch('tfc.temporal.experiments.start_experiment_v2_workflow', return_value='http-fixture-workflow'):
        return admit_submission(str(experiment.pk), admission_id)


def stage_http_execution(tenants, *, accepted=True):
    from model_hub.services.dataset_snapshot import create_dataset_snapshot
    from model_hub.services.experiment_submit import stage_submission

    actor = tenants['a']
    dataset = actor['exp']['dataset']
    experiment = ExperimentsTable.no_workspace_objects.create(
        name='HTTP runner integration', dataset=dataset, user=actor['user'],
        experiment_type='llm', prompt_config=[],
    )
    create_dataset_snapshot(dataset, experiment)
    body = entry()
    body['variable_mapping'] = {'question': str(dataset.column_set.get().pk)}
    source = http_source.persist_http_source(experiment, body)
    http_source.create_http_output_column(experiment, source)
    from django.db import transaction
    with transaction.atomic():
        submission = stage_submission(experiment, {}, 'create', authorized_workspace=actor['workspace'])
        http_source.bind_http_submission(experiment, submission['admission_id'])
    if not accepted:
        return actor, experiment, source, submission
    acknowledge(experiment, submission['admission_id'])
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=['status'])
    plan = http_source.setup_http_source(str(experiment.pk), str(source.pk), submission['admission_id'])
    return actor, experiment, source, plan


@pytest.fixture
def execution(tenants):
    return stage_http_execution(tenants)


@pytest.mark.django_db
def test_runner_dispatches_immutable_selected_cells_with_original_identities(execution, endpoint):
    from model_hub.views.experiment_runner import ExperimentRunner
    actor, experiment, source, plan = execution
    # Mutating the live dataset must not affect the frozen request.
    Cell.no_workspace_objects.filter(dataset=experiment.dataset).update(value='changed after capture')
    row_id = plan['row_ids'][0]
    result = ExperimentRunner(experiment.pk).process_http_row(
        source_id=str(source.pk), row_id=row_id, admission_id=plan['admission_id'],
    )
    assert result['status'] == 'COMPLETED'
    sent = json.loads(endpoint[0]['body'])
    assert sent['id'] == str(actor['exp']['row'].pk)
    assert sent['question'] == actor['exp']['secrets'][0].decode()
    cell = Cell.no_workspace_objects.get(row_id=row_id, column_id=plan['column_id'])
    assert cell.value == 'answer:' + sent['question']
    assert cell.status == CellStatus.PASS.value
    assert cell.response_time >= 0
    assert json.loads(cell.value_infos)['http_attempt_id'].startswith(plan['admission_id'] + ':')
    duplicate = ExperimentRunner(experiment.pk).process_http_row(
        source_id=str(source.pk), row_id=row_id, admission_id=plan['admission_id'],
    )
    assert duplicate['status'] == 'BLOCKED'
    assert len(endpoint) == 1


@pytest.mark.django_db
def test_unknown_http_attempt_settles_without_post_replay(execution):
    from model_hub.models.develop_dataset import Row, Column
    actor, experiment, source, plan = execution
    row = Row.no_workspace_objects.get(pk=plan['row_ids'][0])
    column = Column.no_workspace_objects.get(pk=plan['column_id'])
    attempt = http_source.http_attempt_id(plan['admission_id'], source.pk, row.pk)
    assert http_source.claim_http_attempt(source=source, row=row, column=column, attempt_id=attempt)
    with patch.object(outbound_destination, 'request') as send:
        result = http_source.settle_http_row(str(experiment.pk), str(source.pk), str(row.pk), plan['admission_id'])
    send.assert_not_called()
    assert result['status'] == 'FAILED'
    cell = Cell.no_workspace_objects.get(row=row, column=column)
    assert json.loads(cell.value_infos)['error']['code'] == 'unknown_outcome'
    assert 'Check whether' in cell.value


@pytest.mark.django_db
def test_runner_refuses_foreign_source_or_row_before_transport(execution, tenants):
    actor, experiment, source, plan = execution
    foreign = tenants['b']['exp']
    with patch.object(outbound_destination, 'request') as send:
        with pytest.raises(ValueError):
            http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), str(foreign['snapshot_row'].pk), plan['admission_id'])
        with pytest.raises(ValueError):
            http_source.run_http_snapshot_row(str(foreign['experiment'].pk), str(source.pk), plan['row_ids'][0], plan['admission_id'])
    send.assert_not_called()


@pytest.mark.django_db
def test_stopped_and_stale_admission_cannot_dispatch(execution):
    actor, experiment, source, plan = execution
    with patch.object(outbound_destination, 'request') as send:
        with pytest.raises(ValueError):
            http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), plan['row_ids'][0], str(uuid4()))
        experiment.status = StatusType.CANCELLED.value
        experiment.save(update_fields=['status'])
        with pytest.raises(ValueError):
            http_source.run_http_snapshot_row(str(experiment.pk), str(source.pk), plan['row_ids'][0], plan['admission_id'])
    send.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize('selected', [lambda secret: {'nested': [{'value': secret}]}, lambda secret: [secret, {'nested': secret}]])
def test_structured_secret_redaction_precedes_serialization_and_persistence(execution, selected):
    actor, experiment, source, plan = execution
    credential = 'fixture-quote"-slash\\-only'
    source.legacy_prompt_config['http']['headers'].append({'name': 'X-Test', 'secret_ref': 'FIXTURE'})
    source.save(update_fields=['legacy_prompt_config'])
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'reply': selected(credential)}).encode()
    from model_hub.models.develop_dataset import Row, Column
    row = Row.no_workspace_objects.get(pk=plan['row_ids'][0])
    column = Column.no_workspace_objects.get(pk=plan['column_id'])
    with patch.object(outbound_destination, 'request', return_value=response):
        cell = http_source.execute_and_persist_http_row(
            source=source, row=row, column=column,
            attempt_id=http_source.http_attempt_id(plan['admission_id'], source.pk, row.pk),
            values={str(experiment.dataset.column_set.get().pk): 'hello'}, original_row_id='original',
            resolve_secret=lambda name: credential,
        )
    cell.refresh_from_db()
    assert json.loads(cell.value) == selected('[redacted]')
    assert credential not in json.dumps(json.loads(cell.value))


@pytest.mark.django_db
def test_http_setup_enumeration_and_final_status_require_all_rows(execution):
    from model_hub.views.experiment_runner import check_and_update_experiment_status
    actor, experiment, source, plan = execution
    assert str(source.pk) in http_source.http_source_ids(experiment)
    assert not check_and_update_experiment_status(experiment.pk)
    for row_id in plan['row_ids']:
        http_source.settle_http_row(str(experiment.pk), str(source.pk), row_id, plan['admission_id'])
    result = http_source.finish_http_source(str(experiment.pk), str(source.pk), plan['admission_id'])
    assert result['failed_rows'] == len(plan['row_ids'])
    assert result['completed_rows'] == 0
    assert http_source.http_sources_finished(experiment)


def test_http_worker_registration_and_nonretryable_request_activity():
    from tfc.temporal.experiments import get_activities, get_workflows
    from tfc.temporal.experiments.workflows import HTTP_REQUEST_RETRY_POLICY, ProcessHttpSourceWorkflow
    assert HTTP_REQUEST_RETRY_POLICY.maximum_attempts == 1
    assert ProcessHttpSourceWorkflow in get_workflows()
    names = {fn.__name__ for fn in get_activities()}
    assert {'setup_http_source_activity', 'process_http_row_activity', 'settle_http_row_activity', 'finish_http_source_activity'} <= names
