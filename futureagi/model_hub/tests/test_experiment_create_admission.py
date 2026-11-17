"""Create admission on real ORM paths; only the Temporal RPC is substituted."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import connections

from model_hub.management.commands.seed_system_evals import _system_version_fields
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentPromptConfig, ExperimentsTable
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_submit import STATE_KEY
from model_hub.services.run_metadata_public import public_run_metadata
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy
from model_hub.tests.test_experiment_append_variant import entry, prompt

pytestmark = pytest.mark.django_db(transaction=True)
tenants = tenancy.tenants
V2 = "/model-hub/experiments/v2/"
START = "tfc.temporal.experiments.start_experiment_v2_workflow"


def payload(actor):
    template, version = prompt(actor, "Admission prompt")
    evaluator = EvalTemplate.no_workspace_objects.create(
        name="admission-output-check",
        organization=actor["org"],
        workspace=actor["workspace"],
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    # Published evaluators have a version that native admission can pin.
    EvalTemplateVersion.objects.create_version(
        eval_template=evaluator,
        config_snapshot=evaluator.config,
        criteria=evaluator.criteria,
        model=evaluator.model,
        user=actor["user"],
        organization=actor["org"],
        workspace=actor["workspace"],
    )
    return {
        "name": "Admitted experiment",
        "dataset_id": str(actor["exp"]["dataset"].id),
        "prompt_config": [entry(template, version)],
        "user_eval_metrics": [
            {
                "name": "admission-output-check",
                "template_id": str(evaluator.id),
                "config": {"mapping": {"output": "output"}},
            }
        ],
    }


def counts():
    return tuple(
        model.no_workspace_objects.count()
        for model in (
            ExperimentsTable,
            ExperimentPromptConfig,
            Dataset,
            Column,
            Row,
            Cell,
        )
    )


def persisted(body):
    return ExperimentsTable.no_workspace_objects.get(
        dataset_id=body["dataset_id"], name=body["name"]
    )


def intent(experiment):
    experiment.refresh_from_db()
    return experiment.snapshot_dataset.dataset_config[STATE_KEY]


@pytest.mark.parametrize(
    ("initial_scope_enabled", "extra"),
    [
        pytest.param(False, {}, id="off-to-off-metadata-omitted"),
        pytest.param(False, {"metadata": {}}, id="off-to-off-metadata-empty"),
        pytest.param(True, {}, id="on-to-off-metadata-omitted"),
        pytest.param(True, {"metadata": {}}, id="on-to-off-metadata-empty"),
    ],
)
def test_failed_dispatch_returns_recovery_handle_and_retries_same_intent(
    tenants, settings, initial_scope_enabled, extra
):
    owner = tenants["a"]
    body = payload(owner)
    body.update(extra)
    settings.EXPERIMENT_SCOPE_ENABLED = initial_scope_enabled
    with patch(START, side_effect=TimeoutError("unconfirmed start")) as failed:
        response = owner["client"].post(V2, body, format="json")
    assert response.status_code == 503, response.content
    assert response.json()["code"] == "experiment_admission_unconfirmed"
    failed.assert_called_once()
    experiment = persisted(body)
    assert str(experiment.id) in response.json()["detail"]
    pending = intent(experiment)
    assert pending["state"] == "pending"
    assert experiment.status == "Queued"
    assert experiment.run_scope == {}
    assert experiment.run_policy == {}
    # Snapshot creation stores its own identity; caller metadata is not stored.
    assert public_run_metadata(experiment.run_metadata) == {}
    assert "native_snapshot" in experiment.run_metadata
    snapshot_id = experiment.snapshot_dataset_id
    before = counts()
    # Empty metadata is a legacy basic-create no-op, even across ON -> OFF.
    settings.EXPERIMENT_SCOPE_ENABLED = False
    with patch(START, return_value="admitted") as start:
        retried = owner["client"].post(V2, body, format="json")
        assert retried.status_code == 200, retried.content
        assert str(experiment.id) in retried.json()["result"]
        assert start.call_args == failed.call_args
        assert counts() == before
        accepted = intent(experiment)
        assert accepted["admission_id"] == pending["admission_id"]
        assert accepted["request_hash"] == pending["request_hash"]
        assert accepted["state"] == "accepted"
        assert experiment.snapshot_dataset_id == snapshot_id
        assert owner["client"].post(V2, body, format="json").status_code == 200
        start.assert_called_once()
    assert counts() == before


def test_successful_submit_retry_is_idempotent_and_changed_payload_is_refused(tenants):
    owner = tenants["a"]
    body = payload(owner)
    with patch(START, return_value="admitted") as start:
        first = owner["client"].post(V2, body, format="json")
        assert first.status_code == 200, first.content
        before = counts()
        again = owner["client"].post(V2, body, format="json")
        assert again.status_code == 200, again.content
        assert again.json() == first.json()
        changed = deepcopy(body)
        changed["prompt_config"][0]["model"] = "gpt-4o"
        refused = owner["client"].post(V2, changed, format="json")
        assert refused.status_code == 400, refused.content
        start.assert_called_once()
    assert counts() == before


@pytest.mark.parametrize("phase", ["snapshot", "evals", "ordering"])
def test_creation_failure_rolls_back_every_write_and_never_dispatches(tenants, phase):
    owner = tenants["a"]
    body = payload(owner)
    before = counts()
    target = {
        "snapshot": "create_dataset_snapshot",
        "evals": "_create_eval_metrics_inline",
        "ordering": "_build_and_save_v2_column_order",
    }[phase]
    with (
        patch(
            f"model_hub.views.experiments.{target}",
            side_effect=ValueError("invalid configuration"),
        ),
        patch(START) as start,
    ):
        response = owner["client"].post(V2, body, format="json")
    assert response.status_code == 400, response.content
    assert counts() == before
    start.assert_not_called()


def test_concurrent_identical_creates_share_one_admission(tenants):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from model_hub.views.experiments import ExperimentsTableV2View

    owner = tenants["a"]
    body = payload(owner)
    ready = Barrier(2)

    def create():
        from tfc.middleware.workspace_context import (
            clear_workspace_context,
            set_workspace_context,
        )

        try:
            set_workspace_context(
                workspace=owner["workspace"], organization=owner["org"]
            )
            request = APIRequestFactory().post(V2, deepcopy(body), format="json")
            force_authenticate(request, user=owner["user"])
            request.organization = owner["org"]
            request.workspace = owner["workspace"]
            ready.wait(timeout=10)
            response = ExperimentsTableV2View.as_view()(request)
            return response.status_code, response.data
        finally:
            clear_workspace_context()
            connections.close_all()

    with patch(START, return_value="admitted") as start:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create)
            second = pool.submit(create)
            results = [first.result(timeout=30), second.result(timeout=30)]
        assert [status for status, _ in results] == [200, 200], results
        assert results[0] == results[1]
        start.assert_called_once()
    experiment = persisted(body)
    assert intent(experiment)["state"] == "accepted"
    assert (
        ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=experiment
        ).count()
        == 1
    )


def test_intent_and_configs_are_committed_before_dispatch(tenants):
    owner = tenants["a"]
    body = payload(owner)

    def observe_from_another_connection(experiment_id):
        try:
            experiment = ExperimentsTable.no_workspace_objects.get(id=experiment_id)
            state = experiment.snapshot_dataset.dataset_config[STATE_KEY]
            configs = ExperimentPromptConfig.no_workspace_objects.filter(
                experiment_dataset__experiment=experiment
            ).count()
            return state, configs
        finally:
            connections.close_all()

    def dispatch(**kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            state, configs = pool.submit(
                observe_from_another_connection, kwargs["experiment_id"]
            ).result(timeout=10)
        assert state["state"] == "pending"
        assert state["admission_id"] == kwargs["admission_id"]
        assert configs == 1
        return "admitted"

    with patch(START, side_effect=dispatch):
        response = owner["client"].post(V2, body, format="json")
    assert response.status_code == 200, response.content
    assert intent(persisted(body))["state"] == "accepted"


@pytest.mark.parametrize("scope", ["foreign", "workspace"])
def test_dataset_scope_denial_precedes_writes_and_dispatch(tenants, scope):
    from accounts.models.workspace import Workspace

    owner = tenants["a"]
    body = payload(owner)
    if scope == "foreign":
        body["dataset_id"] = str(tenants["b"]["exp"]["dataset"].id)
    else:
        workspace = Workspace.objects.create(
            name="Other admission workspace",
            organization=owner["org"],
            created_by=owner["user"],
        )
        dataset = Dataset.no_workspace_objects.create(
            name="Other workspace dataset",
            organization=owner["org"],
            workspace=workspace,
            user=owner["user"],
        )
        body["dataset_id"] = str(dataset.id)
    before = counts()
    with patch(START) as start:
        response = owner["client"].post(V2, body, format="json")
    assert response.status_code == 404, response.content
    assert counts() == before
    start.assert_not_called()


def test_foreign_actor_cannot_resume_owner_pending_admission(tenants):
    owner, foreign = tenants["a"], tenants["b"]
    body = payload(owner)
    with patch(START, side_effect=TimeoutError("unconfirmed start")):
        assert owner["client"].post(V2, body, format="json").status_code == 503
    before = counts()
    experiment = persisted(body)
    pending = intent(experiment)
    with patch(START, return_value="admitted") as start:
        refused = foreign["client"].post(V2, body, format="json")
        assert refused.status_code == 404, refused.content
        start.assert_not_called()
        assert intent(experiment) == pending
        assert owner["client"].post(V2, body, format="json").status_code == 200
        start.assert_called_once()
    assert counts() == before


def builtin_evaluator(version_organization):
    """A built-in evaluator as seed_system_evals writes it.

    The template has the row shape of _yaml_to_template_fields: owner system,
    no organization and no workspace. Its one default version is made from the
    seeder's own fields with the seeder's create_version call, which names no
    organization or workspace. The guard case passes another organization.
    """
    template = EvalTemplate.no_workspace_objects.create(
        eval_id=9001,
        name="builtin-output-check",
        description="Built-in output check",
        criteria="Is {{output}} correct?",
        eval_tags=[],
        config={
            "required_keys": ["output"],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
        },
        choices=None,
        multi_choice=False,
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="code",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
        choice_scores=None,
        allow_edit=False,
        allow_copy=True,
    )
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        organization=version_organization,
        workspace=None,
        **_system_version_fields(template),
    )
    return template, version


@pytest.mark.parametrize(
    "version_tenant",
    [
        pytest.param(None, id="global-default-version-is-pinned"),
        pytest.param("b", id="version-of-another-organization-is-refused"),
    ],
)
def test_create_with_builtin_system_evaluator(tenants, version_tenant):
    owner = tenants["a"]
    version_org = tenants[version_tenant]["org"] if version_tenant else None
    template, version = builtin_evaluator(version_org)
    # No request context is set here, so the save signals stamp nothing.
    assert (template.organization_id, template.workspace_id) == (None, None)
    assert version.organization_id == getattr(version_org, "id", None)
    assert version.workspace_id is None
    assert EvalTemplateVersion.objects.get_default(template) == version
    prompt_template, prompt_version = prompt(owner, "Built-in evaluator prompt")
    body = {
        "name": "Built-in evaluator experiment",
        "dataset_id": str(owner["exp"]["dataset"].id),
        "prompt_config": [entry(prompt_template, prompt_version)],
        "user_eval_metrics": [
            {
                "name": "builtin-output-check",
                "template_id": str(template.id),
                "config": {"mapping": {"output": "output"}},
            }
        ],
    }
    before = counts()
    with patch(START, return_value="admitted") as start:
        response = owner["client"].post(V2, body, format="json")
    if version_org is not None:
        # Native admission never pins a version that names another
        # organization, even when its template is a system template. This is
        # the exact refusal the caller's organization receives today.
        refusal = "native evaluator version is unavailable in this tenant"
        assert response.status_code == 400, response.content
        assert response.json()["detail"] == refusal
        assert response.json()["message"] == refusal
        assert counts() == before
        start.assert_not_called()
        return
    assert response.status_code == 200, response.content
    start.assert_called_once()
    experiment = persisted(body)
    assert intent(experiment)["state"] == "accepted"
    metric = experiment.user_eval_template_ids.get()
    assert (metric.template_id, metric.pinned_version_id) == (template.id, None)
    binding = NativeRunBinding.objects.select_related("run").get(
        snapshot__experiment=experiment
    )
    (pin,) = binding.run.evaluator_versions
    assert pin["metric_id"] == str(metric.id)
    assert pin["template_id"] == str(template.id)
    assert pin["evaluator_version_id"] == str(version.id)
    assert pin["version_number"] == version.version_number == 1
