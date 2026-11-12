"""The SRV-5 external-template refusal names only a template the caller may use.

FA dev integration (SC1). External metrics (SRV-5, integration)
are scored by the caller and never run on the server, so the paths that attach
a template for execution refuse an external one with 400 ``external``. Those
refusals looked the id up in every organization: another organization's (or
another workspace's) external template got the refusal, while an unknown id
got the not-found answer. Now only a template the caller may use is refused
by name; any other id gets exactly the answer an unknown id gets
(non-enumeration). Covered paths:

- ``develops/<dataset>/add_user_eval/`` (``AddUserEvalView``);
- ``experiments/<id>/add-eval/`` (``AddExperimentEvalView``);
- ``experiments/v2/`` create (``EvalMetricEntrySerializer.validate_template_id``).
"""

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status

import model_hub.views.develop_dataset  # noqa: F401  (imported with no workspace bound)
import model_hub.views.experiments  # noqa: F401
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.tests.two_tenants import make_tenant, same_answer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.api,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

MODEL_HUB = "/model-hub"


# --------------------------------------------------------------------------
# Builders. Rows are created with no workspace context bound.
# --------------------------------------------------------------------------


@pytest.fixture
def tenants(db):
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    yield tenant_a, tenant_b
    tenant_a.client.stop_workspace_injection()
    tenant_b.client.stop_workspace_injection()


@pytest.fixture
def team(tenants):
    """A second, non-default workspace of tenant B and a client bound to it."""
    _, tenant_b = tenants
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        name="B team workspace",
        organization=tenant_b.organization,
        is_default=False,
        is_active=True,
        created_by=tenant_b.user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=tenant_b.user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=tenant_b.user)
    client.set_workspace(workspace)
    clear_workspace_context()
    yield SimpleNamespace(
        organization=tenant_b.organization,
        workspace=workspace,
        user=tenant_b.user,
        client=client,
    )
    client.stop_workspace_injection()


@pytest.fixture
def no_background():
    """No experiment runner and no workflow start during a request."""
    with (
        patch("model_hub.views.experiment_runner.ExperimentRunner") as runner,
        patch("model_hub.views.experiments.ExperimentRunner"),
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        start.return_value = "wf-test"
        runner.create_agent_output_columns.return_value = ({}, [])
        yield


def _post(client, url, payload):
    response = client.post(url, payload, format="json")
    clear_workspace_context()
    return response


def _dataset(tenant, name):
    clear_workspace_context()
    return Dataset.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


def _external_template(tenant, label):
    """An external template of ``tenant``'s workspace, made by the external
    metrics API as the SDK makes it."""
    dataset = _dataset(tenant, f"{label} external data")
    source = f"def {label.lower().replace(' ', '_')}(output): ...  # synthetic"
    response = _post(
        tenant.client,
        f"{MODEL_HUB}/datasets/{dataset.id}/external-metrics/",
        {
            "name": f"external-{uuid.uuid4().hex[:6]}",
            "output_type": "pass_fail",
            "higher_is_better": True,
            "pass_threshold": 0.5,
            "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.content[:300]
    template = EvalTemplate.no_workspace_objects.get(
        id=response.json()["result"]["template_id"]
    )
    assert template.eval_type == "external"
    return template


def _experiment(tenant, name):
    """A V2-shaped LLM experiment (with a snapshot dataset)."""
    dataset = _dataset(tenant, f"{name} data")
    snapshot = _dataset(tenant, f"{name} snapshot")
    return ExperimentsTable.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        snapshot_dataset=snapshot,
        status=StatusType.COMPLETED.value,
        experiment_type="llm",
        user=tenant.user,
    )


def _prompt_version(tenant, name):
    clear_workspace_context()
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        created_by=tenant.user,
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=[],
        is_draft=False,
    )
    return template, version


def _unknown():
    return uuid.uuid4()


def _refused(response):
    return (
        response.status_code == status.HTTP_400_BAD_REQUEST
        and "external" in response.content.decode().lower()
    )


# --------------------------------------------------------------------------
# AddUserEvalView: develops/<dataset>/add_user_eval/
# --------------------------------------------------------------------------


def _add_user_eval(tenant, dataset, template_id, name):
    return _post(
        tenant.client,
        f"{MODEL_HUB}/develops/{dataset.id}/add_user_eval/",
        {
            "name": name,
            "template_id": str(template_id),
            "config": {"mapping": {}},
            "run": False,
        },
    )


class TestAddUserEvalExternalTemplateScope:
    def test_add_user_eval_refuses_the_callers_external_template(self, tenants):
        _, tenant_b = tenants
        own = _external_template(tenant_b, "B own")
        dataset = _dataset(tenant_b, "B add user eval")

        response = _add_user_eval(tenant_b, dataset, own.id, "own-sdk-metric")

        assert _refused(response), (response.status_code, response.content[:300])
        assert not UserEvalMetric.no_workspace_objects.filter(
            name="own-sdk-metric"
        ).exists()

    def test_add_user_eval_answers_another_orgs_external_template_like_unknown(
        self, tenants
    ):
        tenant_a, tenant_b = tenants
        foreign = _external_template(tenant_a, "A private")
        dataset = _dataset(tenant_b, "B add user eval")
        unknown = _unknown()

        named = _add_user_eval(tenant_b, dataset, foreign.id, "named")
        missing = _add_user_eval(tenant_b, dataset, unknown, "named")

        assert named.status_code == status.HTTP_404_NOT_FOUND, named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown)
        assert not UserEvalMetric.no_workspace_objects.filter(name="named").exists()

    def test_add_user_eval_answers_another_workspaces_external_template_like_unknown(
        self, tenants, team
    ):
        _, tenant_b = tenants
        foreign = _external_template(team, "B team")
        dataset = _dataset(tenant_b, "B add user eval")
        unknown = _unknown()

        named = _add_user_eval(tenant_b, dataset, foreign.id, "named")
        missing = _add_user_eval(tenant_b, dataset, unknown, "named")

        assert named.status_code == status.HTTP_404_NOT_FOUND, named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown)


# --------------------------------------------------------------------------
# AddExperimentEvalView: experiments/<uuid>/add-eval/
# --------------------------------------------------------------------------


def _add_experiment_eval(tenant, experiment, template_id, name, **extra):
    return _post(
        tenant.client,
        f"{MODEL_HUB}/experiments/{experiment.id}/add-eval/",
        {
            "name": name,
            "template_id": str(template_id),
            "config": {"mapping": {"output": "output_column"}},
            **extra,
        },
    )


class TestAddExperimentEvalExternalTemplateScope:
    @pytest.mark.parametrize("save_as_template", [False, True])
    def test_add_eval_refuses_the_callers_external_template(
        self, tenants, no_background, save_as_template
    ):
        _, tenant_b = tenants
        own = _external_template(tenant_b, "B own")
        experiment = _experiment(tenant_b, "B add eval")
        templates_before = EvalTemplate.no_workspace_objects.count()

        response = _add_experiment_eval(
            tenant_b,
            experiment,
            own.id,
            "own-sdk-metric",
            save_as_template=save_as_template,
        )

        assert _refused(response), (response.status_code, response.content[:300])
        assert EvalTemplate.no_workspace_objects.count() == templates_before
        assert not UserEvalMetric.no_workspace_objects.filter(
            name="own-sdk-metric"
        ).exists()

    @pytest.mark.parametrize("save_as_template", [False, True])
    def test_add_eval_answers_another_orgs_external_template_like_unknown(
        self, tenants, no_background, save_as_template
    ):
        tenant_a, tenant_b = tenants
        foreign = _external_template(tenant_a, "A private")
        experiment = _experiment(tenant_b, "B add eval")
        unknown = _unknown()

        named = _add_experiment_eval(
            tenant_b, experiment, foreign.id, "x1", save_as_template=save_as_template
        )
        missing = _add_experiment_eval(
            tenant_b, experiment, unknown, "x1", save_as_template=save_as_template
        )

        assert not _refused(named), named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown), (
            named.status_code,
            named.content[:300],
        )
        assert not UserEvalMetric.no_workspace_objects.filter(name="x1").exists()

    def test_add_eval_answers_another_workspaces_external_template_like_unknown(
        self, tenants, team, no_background
    ):
        _, tenant_b = tenants
        foreign = _external_template(team, "B team")
        experiment = _experiment(tenant_b, "B add eval")
        unknown = _unknown()

        named = _add_experiment_eval(tenant_b, experiment, foreign.id, "x2")
        missing = _add_experiment_eval(tenant_b, experiment, unknown, "x2")

        assert not _refused(named), named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown), (
            named.status_code,
            named.content[:300],
        )
        assert not UserEvalMetric.no_workspace_objects.filter(name="x2").exists()


# --------------------------------------------------------------------------
# ExperimentsTableV2View.post: experiments/v2/ (EvalMetricEntrySerializer)
# --------------------------------------------------------------------------


def _create_v2(tenant, dataset, prompt, version, template_id):
    return _post(
        tenant.client,
        f"{MODEL_HUB}/experiments/v2/",
        {
            "name": "scope refs run",
            "dataset_id": str(dataset.id),
            "experiment_type": "llm",
            "prompt_config": [
                {
                    "prompt_id": str(prompt.id),
                    "prompt_version": str(version.id),
                    "model": "gpt-4o-mini",
                }
            ],
            "user_eval_metrics": [
                {
                    "template_id": str(template_id),
                    "name": "scope refs metric",
                    "config": {"mapping": {"output": "output_column"}},
                }
            ],
        },
    )


class TestExperimentV2ExternalTemplateScope:
    def test_v2_create_refuses_the_callers_external_template(
        self, tenants, no_background
    ):
        _, tenant_b = tenants
        own = _external_template(tenant_b, "B own")
        prompt, version = _prompt_version(tenant_b, "B prompt")
        dataset = _dataset(tenant_b, "B source")

        response = _create_v2(tenant_b, dataset, prompt, version, own.id)

        assert _refused(response), (response.status_code, response.content[:300])
        assert not ExperimentsTable.no_workspace_objects.filter(
            dataset=dataset
        ).exists()

    def test_v2_create_answers_another_orgs_external_template_like_unknown(
        self, tenants, no_background
    ):
        tenant_a, tenant_b = tenants
        foreign = _external_template(tenant_a, "A private")
        prompt, version = _prompt_version(tenant_b, "B prompt")
        dataset = _dataset(tenant_b, "B source")
        unknown = _unknown()

        named = _create_v2(tenant_b, dataset, prompt, version, foreign.id)
        missing = _create_v2(tenant_b, dataset, prompt, version, unknown)

        assert named.status_code == status.HTTP_400_BAD_REQUEST, named.content[:300]
        assert not _refused(named), named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown)
        assert not ExperimentsTable.no_workspace_objects.filter(
            dataset=dataset
        ).exists()

    def test_v2_create_answers_another_workspaces_external_template_like_unknown(
        self, tenants, team, no_background
    ):
        _, tenant_b = tenants
        foreign = _external_template(team, "B team")
        prompt, version = _prompt_version(tenant_b, "B prompt")
        dataset = _dataset(tenant_b, "B source")
        unknown = _unknown()

        named = _create_v2(tenant_b, dataset, prompt, version, foreign.id)
        missing = _create_v2(tenant_b, dataset, prompt, version, unknown)

        assert named.status_code == status.HTTP_400_BAD_REQUEST, named.content[:300]
        assert not _refused(named), named.content[:300]
        assert same_answer(named, foreign.id, missing, unknown)


class TestEvalMetricEntrySerializerExternalScope:
    """The serializer on its own: the caller is the bound context."""

    def _valid(self, template_id):
        from model_hub.serializers.experiments import EvalMetricEntrySerializer

        return EvalMetricEntrySerializer(
            data={"template_id": str(template_id), "name": "x", "config": {}}
        ).is_valid()

    def test_with_a_caller_only_the_callers_external_template_is_refused(
        self, tenants, team
    ):
        tenant_a, tenant_b = tenants
        own = _external_template(tenant_b, "B own")
        other_org = _external_template(tenant_a, "A private")
        other_workspace = _external_template(team, "B team")

        set_workspace_context(
            workspace=tenant_b.workspace, organization=tenant_b.organization
        )
        try:
            assert not self._valid(own.id)
            assert self._valid(other_org.id)
            assert self._valid(other_workspace.id)
            assert self._valid(_unknown())
        finally:
            clear_workspace_context()

    def test_without_a_caller_every_external_template_is_refused(self, tenants):
        tenant_a, _ = tenants
        other_org = _external_template(tenant_a, "A private")
        ordinary = EvalTemplate.no_workspace_objects.create(
            name=f"ordinary-{uuid.uuid4().hex[:8]}",
            organization=tenant_a.organization,
            workspace=tenant_a.workspace,
            owner=OwnerChoices.USER.value,
            eval_type="code",
        )
        clear_workspace_context()

        assert not self._valid(other_org.id)
        assert self._valid(ordinary.id)
