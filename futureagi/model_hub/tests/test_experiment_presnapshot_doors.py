"""An experiment created before experiments kept a snapshot of their rows
either runs or is refused with one sentence: never a started message with
nothing queued.

The older create route (POST /model-hub/experiments/) made one, and is now
refused for it (the v2 create makes experiments): its dataset is the source
dataset, its prompts are only in its inline prompt_config and it has no
snapshot. Such experiments are seeded here in exactly the shape that route
made them. No run reads that prompt_config, so a
whole rerun, a configuration edit and the older route's update with re_run
are refused before any write or start, as Rerun cells already is. The older
update checks what it is sent first, as the older create does. One whose
prompts are voice or image prompts cannot be copied either, so each of those
doors refuses it with its own sentence, and it still opens with its results.
Its evaluations are scored by the evaluation sweeper, as an evaluation group's
are, so Run evaluations and Add evaluation with Run leave them to it instead
of starting a run that does nothing. Stop and Resume start nothing: Stop
says it is not running, and Resume is off.

The caller is the organization's owner and a member of its workspace, with the
membership rows every person has; the run controls look an experiment up only
for a member.

The workflow starts are replaced and recorded; the sweeper tick is the real
execute_evaluation, with what it dispatches recorded.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.services.experiment_service import ServiceError, create_experiment
from model_hub.services.external_metrics import (
    ExternalMetricDefinition,
    create_or_reuse_external_metric,
)
from model_hub.tasks import user_evaluation
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context, workspace_context
from tfc.utils.general_methods import GeneralMethods

SENTENCE = (
    "This experiment was created before run snapshots and can't be re-run. "
    "Duplicate it to run it again."
)
NOT_DUPLICABLE = (
    "This experiment was created in an earlier version and can't be "
    "duplicated here. Create a new experiment."
)
STARTED = "Re-Run has started for the experiments."
NOT_RUNNING = "Experiment is not running. Current status: Completed"
RUN_CONTROL_OFF = "Run control is not available."
RETIRED = (
    "This route does not create experiments. "
    "Create them with POST /model-hub/experiments/v2/."
)
EXTERNAL = (
    "External metric(s) correct are scored by the caller (the SDK) and are "
    "never executed by the server. Upload their scores as score_refs on an "
    "external experiment run instead."
)
OLDER_CREATE = "/model-hub/experiments/"
OLDER_RERUN = "/model-hub/experiments/re-run/"
V2 = "/model-hub/experiments/v2"
START = "tfc.temporal.experiments.start_experiment_v2_workflow"
OLDER_START = "tfc.temporal.experiments.start_experiment_workflow"
CELLS_START = "tfc.temporal.experiments.start_rerun_cells_v2_workflow"
RUNNER = "model_hub.views.experiments.ExperimentRunner"


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Before snapshots")


@pytest.fixture
def user(db, organization):
    """The organization's owner, with the membership row every person has."""
    user = User.objects.create_user(
        email=f"before-snapshots-{uuid.uuid4().hex[:8]}@example.com",
        password="testpassword123",
        name="Before Snapshots",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


@pytest.fixture
def workspace(db, organization, user):
    """The organization's default workspace, the owner a member of it."""
    workspace = Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return workspace


@pytest.fixture
def auth_client(user, workspace):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    set_workspace_context(workspace=workspace, organization=user.organization)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="Questions",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


@pytest.fixture
def question(db, dataset):
    return Column.objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )


@pytest.fixture
def answer(db, dataset):
    return Column.objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )


def older_prompt(question):
    """One prompt as the older create route stored it."""
    return {
        "name": "Prompt A",
        "messages": [
            {"role": "user", "content": "Answer {{" + str(question.id) + "}}"},
        ],
        "model": ["gpt-4o-mini"],
        "configuration": {},
    }


def older_voice_prompt(question):
    """One text-to-speech prompt as the older create route stored it."""
    return {
        "name": "Voice A",
        "model_type": "tts",
        "messages": [
            {"role": "user", "content": "Say {{" + str(question.id) + "}}"},
        ],
        "model": [{"name": "tts-1", "config": {"voice": "alloy"}}],
        "configuration": {},
    }


@pytest.fixture
def presnapshot(auth_client, user, dataset, question, answer):
    """An experiment as the older create route made it (its create, field for
    field), after its run finished: its output is on the dataset itself and
    it has no snapshot."""
    return seeded_older(user, dataset, answer, older_prompt(question), "gpt-4o-mini")


@pytest.fixture
def voice_presnapshot(auth_client, user, dataset, question, answer):
    """The same, for a text-to-speech prompt."""
    return seeded_older(user, dataset, answer, older_voice_prompt(question), "tts-1")


def seeded_older(user, dataset, answer, prompt, model):
    Row.objects.create(dataset=dataset, order=0)
    name = "before-snapshots-" + uuid.uuid4().hex[:8]
    experiment = ExperimentsTable.objects.create(
        name=name,
        dataset=dataset,
        column=answer,
        prompt_config=[prompt],
        user=user,
    )
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        name=f"{name}-{prompt['name']}-{model}",
        experiment=experiment,
        legacy_prompt_config=prompt,
        status=StatusType.COMPLETED.value,
    )
    output = Column.objects.create(
        name=variant.name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.COMPLETED.value
    )
    experiment.refresh_from_db()
    return experiment


@pytest.fixture
def with_snapshot(db, organization, workspace, user, dataset):
    """An experiment as the create of today makes one: with a snapshot."""
    snapshot = Dataset.objects.create(
        name="Questions snapshot",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    return ExperimentsTable.objects.create(
        name="with-snapshot-" + uuid.uuid4().hex[:8],
        dataset=dataset,
        snapshot_dataset=snapshot,
        user=user,
        status=StatusType.COMPLETED.value,
        prompt_config=[],
    )


@pytest.fixture
def template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="answer-judge-" + uuid.uuid4().hex[:8],
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        criteria="Judge {{output}}",
        model="gpt-4o-mini",
        config={
            "output": "Pass/Fail",
            "required_keys": ["output"],
            "eval_type_id": "AgentEvaluator",
        },
    )


def assert_refused(response):
    assert response.status_code == 400, response.content
    assert response.json() == GeneralMethods().bad_request(SENTENCE).data
    assert "has started" not in response.content.decode()


def status_of(experiment):
    experiment.refresh_from_db()
    return experiment.status


def swept():
    """What the evaluation sweeper's tick dispatches now, as the scheduled
    worker runs it (no request context)."""
    dispatched = []

    def dispatch(args=None, kwargs=None, **options):
        dispatched.append(dict(args[0]))

    with (
        patch.object(user_evaluation, "close_old_connections"),
        patch.object(
            user_evaluation.process_evaluation_single_task,
            "apply_async",
            side_effect=dispatch,
        ),
        workspace_context(None),
    ):
        user_evaluation.execute_evaluation._original_func()
    return dispatched


# --- the whole rerun: refused --------------------------------------------------


@pytest.mark.django_db
def test_the_seeded_experiment_has_the_older_shape(presnapshot):
    assert presnapshot.snapshot_dataset_id is None
    assert presnapshot.prompt_config[0]["name"] == "Prompt A"
    assert presnapshot.status == StatusType.COMPLETED.value


# --- the older create: refused -------------------------------------------------


@pytest.mark.django_db
def test_the_older_create_route_refuses_and_names_the_v2_create(
    auth_client, dataset, question, answer
):
    name = "older-create-" + uuid.uuid4().hex[:8]
    body = {
        "name": name,
        "dataset_id": str(dataset.id),
        "column_id": str(answer.id),
        "prompt_config": [older_prompt(question)],
    }
    with patch(OLDER_START, return_value="older-workflow") as started:
        response = auth_client.post(OLDER_CREATE, body, format="json")

    assert response.status_code == 400, response.content
    assert response.json() == GeneralMethods().bad_request(RETIRED).data
    started.assert_not_called()
    assert not ExperimentsTable.no_workspace_objects.filter(name=name).exists()


@pytest.mark.django_db
def test_the_older_create_route_still_answers_a_scope_error_first(
    auth_client, organization, workspace, dataset, question
):
    elsewhere = Dataset.objects.create(
        name="Elsewhere",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    other_column = Column.objects.create(
        name="other",
        dataset=elsewhere,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    body = {
        "name": "older-create-elsewhere",
        "dataset_id": str(dataset.id),
        "column_id": str(other_column.id),
        "prompt_config": [older_prompt(question)],
    }
    with patch(OLDER_START, return_value="older-workflow") as started:
        response = auth_client.post(OLDER_CREATE, body, format="json")

    assert response.status_code == 404, response.content
    started.assert_not_called()


@pytest.mark.django_db
def test_the_service_create_refuses_with_the_same_sentence(
    user, dataset, question, answer
):
    name = "service-create-" + uuid.uuid4().hex[:8]
    with patch(OLDER_START, return_value="older-workflow") as started:
        refused = create_experiment(
            name=name,
            dataset_id=dataset.id,
            column_id=answer.id,
            prompt_config=[older_prompt(question)],
            user=user,
        )

    assert refused == ServiceError(RETIRED, "ROUTE_RETIRED")
    started.assert_not_called()
    assert not ExperimentsTable.no_workspace_objects.filter(name=name).exists()


@pytest.mark.django_db
def test_rerun_refuses_an_experiment_from_before_snapshots_before_any_work(
    auth_client, presnapshot
):
    with patch(START, return_value="experiment-workflow") as started:
        response = auth_client.post(
            f"{V2}/re-run/", {"experiment_ids": [str(presnapshot.id)]}, format="json"
        )

    assert_refused(response)
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_a_batch_with_one_such_experiment_is_refused_whole(
    auth_client, presnapshot, with_snapshot
):
    ids = [str(presnapshot.id), str(with_snapshot.id)]
    with patch(START, return_value="experiment-workflow") as started:
        response = auth_client.post(
            f"{V2}/re-run/", {"experiment_ids": ids}, format="json"
        )

    assert_refused(response)
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value
    assert status_of(with_snapshot) == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_an_experiment_with_a_snapshot_still_reruns(auth_client, with_snapshot):
    with patch(START, return_value="experiment-workflow") as started:
        response = auth_client.post(
            f"{V2}/re-run/", {"experiment_ids": [str(with_snapshot.id)]}, format="json"
        )

    assert response.status_code == 200, response.content
    assert response.json() == GeneralMethods().success_response(STARTED).data
    started.assert_called_once()


@pytest.mark.django_db
def test_the_older_rerun_route_refuses_it_too(auth_client, presnapshot):
    with patch(OLDER_START, return_value="older-workflow") as started:
        response = auth_client.post(
            OLDER_RERUN, {"experiment_ids": [str(presnapshot.id)]}, format="json"
        )

    assert_refused(response)
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_rerun_cells_refuses_it_with_the_same_sentence(auth_client, presnapshot):
    with patch(CELLS_START, return_value="cells-workflow") as started:
        response = auth_client.post(
            f"{V2}/{presnapshot.id}/rerun-cells/",
            {"source_ids": [str(uuid.uuid4())]},
            format="json",
        )

    assert_refused(response)
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_the_older_update_with_re_run_refuses_it_before_any_write(
    auth_client, presnapshot, answer
):
    variant = presnapshot.experiment_datasets.get()
    body = {
        "experiment_id": str(presnapshot.id),
        "name": presnapshot.name + "-renamed",
        "dataset_id": str(presnapshot.dataset_id),
        "column_id": str(answer.id),
        "prompt_config": presnapshot.prompt_config,
        "re_run": True,
    }
    with patch(OLDER_START, return_value="older-workflow") as started:
        response = auth_client.put(OLDER_CREATE, body, format="json")

    assert_refused(response)
    started.assert_not_called()
    presnapshot.refresh_from_db()
    assert not presnapshot.name.endswith("-renamed")
    assert presnapshot.status == StatusType.COMPLETED.value
    variant.refresh_from_db()
    assert variant.deleted is False


@pytest.mark.django_db
def test_the_older_update_with_re_run_checks_what_it_is_sent_first(
    auth_client, presnapshot, answer, user
):
    metric = create_or_reuse_external_metric(
        dataset=presnapshot.dataset,
        definition=ExternalMetricDefinition(
            name="correct", output_type="pass_fail", source_hash="a" * 64
        ),
        user=user,
    ).metric
    variant = presnapshot.experiment_datasets.get()
    body = {
        "experiment_id": str(presnapshot.id),
        "name": presnapshot.name,
        "dataset_id": str(presnapshot.dataset_id),
        "column_id": str(answer.id),
        "prompt_config": presnapshot.prompt_config,
        "user_eval_template_ids": [str(metric.id)],
        "re_run": True,
    }
    with patch(OLDER_START, return_value="older-workflow") as started:
        response = auth_client.put(OLDER_CREATE, body, format="json")

    # The external metric is refused by name, as the older create refuses it;
    # nothing is written or started.
    assert response.status_code == 400, response.content
    assert response.json() == GeneralMethods().bad_request(EXTERNAL).data
    started.assert_not_called()
    assert not presnapshot.user_eval_template_ids.exists()
    variant.refresh_from_db()
    assert variant.deleted is False


@pytest.mark.django_db
def test_edit_refuses_a_configuration_change_before_any_write(
    auth_client, presnapshot, question
):
    before = presnapshot.column_id
    with patch(START, return_value="experiment-workflow") as started:
        response = auth_client.put(
            f"{V2}/{presnapshot.id}/", {"column_id": str(question.id)}, format="json"
        )

    assert_refused(response)
    started.assert_not_called()
    presnapshot.refresh_from_db()
    assert presnapshot.column_id == before
    assert presnapshot.status == StatusType.COMPLETED.value


# --- a voice or image experiment: its own sentence, and it still opens --------


def door_requests(experiment, question, answer):
    """Each door that refuses an experiment from before snapshots."""
    older_update = {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "dataset_id": str(experiment.dataset_id),
        "column_id": str(answer.id),
        "prompt_config": experiment.prompt_config,
        "re_run": True,
    }
    return {
        "rerun": ("post", f"{V2}/re-run/", {"experiment_ids": [str(experiment.id)]}),
        "older-rerun": ("post", OLDER_RERUN, {"experiment_ids": [str(experiment.id)]}),
        "rerun-cells": (
            "post",
            f"{V2}/{experiment.id}/rerun-cells/",
            {"source_ids": [str(uuid.uuid4())]},
        ),
        "edit": ("put", f"{V2}/{experiment.id}/", {"column_id": str(question.id)}),
        "older-update": ("put", OLDER_CREATE, older_update),
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "door", ["rerun", "older-rerun", "rerun-cells", "edit", "older-update"]
)
def test_a_voice_experiment_is_refused_with_its_own_sentence_and_still_opens(
    auth_client, voice_presnapshot, question, answer, door
):
    method, url, body = door_requests(voice_presnapshot, question, answer)[door]
    variant = voice_presnapshot.experiment_datasets.get()
    starts = SimpleNamespace()
    with (
        patch(START) as starts.v2,
        patch(OLDER_START) as starts.older,
        patch(CELLS_START) as starts.cells,
    ):
        response = getattr(auth_client, method)(url, body, format="json")

    assert response.status_code == 400, response.content
    assert response.json() == GeneralMethods().bad_request(NOT_DUPLICABLE).data
    for start in (starts.v2, starts.older, starts.cells):
        start.assert_not_called()
    assert status_of(voice_presnapshot) == StatusType.COMPLETED.value
    # It still opens, with its results; it offers no prompt to copy.
    opened = auth_client.get(f"{V2}/{voice_presnapshot.id}/")
    assert opened.status_code == 200, opened.content
    shown = opened.json()["result"]
    assert shown["name"] == voice_presnapshot.name
    assert shown["inline_prompt_configs"] == []
    variant.refresh_from_db()
    assert variant.deleted is False
    assert variant.columns.filter(deleted=False).count() == 1


# --- its evaluations: run, by the sweeper ------------------------------------


@pytest.mark.django_db
def test_run_evaluations_leaves_its_evaluations_to_the_sweeper(
    auth_client, presnapshot, template, answer, user, organization, workspace
):
    metric = UserEvalMetric.objects.create(
        name="answer-judge-" + uuid.uuid4().hex[:8],
        organization=organization,
        workspace=workspace,
        dataset=presnapshot.dataset,
        template=template,
        config={"mapping": {"output": str(answer.id)}, "config": {}},
        status=StatusType.COMPLETED.value,
        user=user,
    )

    with patch(START, return_value="experiment-workflow") as started, patch(RUNNER):
        response = auth_client.post(
            f"/model-hub/experiments/{presnapshot.id}/run-evaluations/",
            {"eval_template_ids": [str(metric.id)]},
            format="json",
        )

    assert response.status_code == 200, response.content
    # No run of the experiment is started: it would do nothing.
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value
    metric.refresh_from_db()
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value
    # The sweeper takes it, as it takes an evaluation group's.
    assert swept() == [{"type": "experiment", "eval_id": str(metric.id)}]


@pytest.mark.django_db
def test_add_evaluation_with_run_leaves_it_to_the_sweeper(
    auth_client, presnapshot, template, answer
):
    body = {
        "name": "added-" + uuid.uuid4().hex[:8],
        "template_id": str(template.id),
        "config": {"mapping": {"output": str(answer.id)}},
        "run": True,
    }
    with patch(START, return_value="experiment-workflow") as started, patch(RUNNER):
        response = auth_client.post(
            f"/model-hub/experiments/{presnapshot.id}/add-eval/", body, format="json"
        )

    assert response.status_code == 200, response.content
    # Not set running, and no run of the experiment started.
    started.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value
    metric = UserEvalMetric.no_workspace_objects.get(
        pk=response.data["result"]["eval_id"]
    )
    assert metric.dataset_id == presnapshot.dataset_id
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value
    assert swept() == [{"type": "experiment", "eval_id": str(metric.id)}]


# --- Stop and Resume: nothing is started --------------------------------------


@pytest.mark.django_db
@override_settings(EXPERIMENT_RUN_CONTROL_ENABLED=False)
def test_stop_and_resume_start_nothing(auth_client, presnapshot):
    starts = SimpleNamespace()
    with (
        patch(START) as starts.v2,
        patch(OLDER_START) as starts.older,
        patch(CELLS_START) as starts.cells,
    ):
        stop = auth_client.post(f"{V2}/{presnapshot.id}/stop/", {}, format="json")
        resume = auth_client.post(f"{V2}/{presnapshot.id}/resume/", {}, format="json")

    # Stop on a finished experiment says it is not running; Resume is off.
    assert stop.status_code == 400, stop.content
    assert NOT_RUNNING in stop.content.decode()
    assert "Experiment stopped." not in stop.content.decode()
    assert resume.status_code == 404, resume.content
    assert RUN_CONTROL_OFF in resume.content.decode()
    for start in (starts.v2, starts.older, starts.cells):
        start.assert_not_called()
    assert status_of(presnapshot) == StatusType.COMPLETED.value
