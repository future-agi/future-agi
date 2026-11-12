"""A member removed from an organization, signed in through the login
endpoint, sees no organization's rows and can reference none of them.

Removal deactivates the user's memberships and leaves the user's legacy
``organization`` field naming the former organization; authentication then
resolves no organization for the user. Every list below must come back empty
of that organization's rows and of an unrelated organization's rows, and a
reference to the former organization's objects must get the same answer as an
id that does not exist.
"""

import uuid

import pytest
from rest_framework import status

import model_hub.serializers.develop_annotations  # noqa: F401  (imported with no workspace bound)
import model_hub.views.develop_annotations  # noqa: F401
import model_hub.views.experiments  # noqa: F401
import model_hub.views.kb  # noqa: F401
import tracer.serializers.custom_eval_config  # noqa: F401
import tracer.serializers.eval_task  # noqa: F401
import tracer.views.custom_eval_config  # noqa: F401
import tracer.views.eval_task  # noqa: F401
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices, StatusType
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.kb import KnowledgeBase
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import (
    former_member,
    make_tenant,
    same_answer,
    signed_in_client,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.project import Project

EXPERIMENTS_URL = "/model-hub/experiments/data/"
LABELS_URL = "/model-hub/annotations-labels/"
KB_URL = "/model-hub/kb/"
CONFIG_URL = "/tracer/custom-eval-config/"
TASK_URL = "/tracer/eval-task/"

LABEL_SETTINGS = {
    "options": [{"label": "yes"}, {"label": "no"}],
    "multi_choice": False,
    "rule_prompt": "",
    "auto_annotate": False,
    "strategy": None,
}
TEMPLATE_CONFIG = {"type": "pass_fail", "criteria": "Is the output correct?"}
MAPPING = {"input": "input", "output": "output"}


def _project(tenant, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _template(tenant, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        owner=OwnerChoices.USER.value,
        config=TEMPLATE_CONFIG,
    )


def _config(project, template, name):
    return CustomEvalConfig.no_workspace_objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping=MAPPING,
        filters={},
    )


def _label(tenant, project, name, **extra):
    return AnnotationsLabels.no_workspace_objects.create(
        name=name,
        type="categorical",
        settings=LABEL_SETTINGS,
        organization=tenant.organization,
        workspace=tenant.workspace,
        project=project,
        **extra,
    )


def _tenant_rows(tenant, tag):
    """One of each row the endpoints under test list or reference."""
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name=f"{tag} data",
        organization=tenant.organization,
        workspace=tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    project = _project(tenant, f"{tag} project")
    template = _template(tenant, f"{tag.lower()}-template")
    config = _config(project, template, f"{tag} config")
    task = EvalTask.no_workspace_objects.create(
        project=project,
        name=f"{tag} task",
        filters={},
        sampling_rate=50.0,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(config)
    return {
        "dataset": dataset,
        "experiment": ExperimentsTable.no_workspace_objects.create(
            name=f"{tag} run", dataset=dataset, status=StatusType.COMPLETED.value
        ),
        "project": project,
        "other_project": _project(tenant, f"{tag} other project"),
        "template": template,
        "other_template": _template(tenant, f"{tag.lower()}-other-template"),
        "kb_file": KnowledgeBaseFile.no_workspace_objects.create(
            name=f"{tag} kb file",
            organization=tenant.organization,
            workspace=tenant.workspace,
        ),
        "knowledge_base": KnowledgeBase.no_workspace_objects.create(
            name=f"{tag} knowledge base",
            chunk_size=256,
            organization=tenant.organization,
            workspace=tenant.workspace,
        ),
        "label": _label(tenant, project, f"{tag} label"),
        "archived_label": _label(
            tenant, project, f"{tag} archived label", deleted=True
        ),
        "config": config,
        "other_config": _config(project, template, f"{tag} other config"),
        "task": task,
    }


@pytest.fixture
def setup(db):
    former_org = make_tenant("Former")
    unrelated_org = make_tenant("Unrelated")
    former = _tenant_rows(former_org, "Former")
    unrelated = _tenant_rows(unrelated_org, "Unrelated")
    system_template = EvalTemplate.no_workspace_objects.create(
        name="former-member-system-template",
        owner=OwnerChoices.SYSTEM.value,
        config=TEMPLATE_CONFIG,
    )
    _, client = former_member(former_org, "Former")
    data = {
        "client": client,
        "former_org": former_org,
        "unrelated_org": unrelated_org,
        "former": former,
        "unrelated": unrelated,
        "system_template": system_template,
    }
    yield data
    former_org.client.stop_workspace_injection()
    unrelated_org.client.stop_workspace_injection()


def _refused(response):
    """Authentication's answer to a signed-in account with no organization."""
    return (
        response.status_code == status.HTTP_403_FORBIDDEN
        and response.json().get("code") == "no_active_organization"
    )


def _assert_hides(response, *rows):
    assert response.status_code == status.HTTP_200_OK or _refused(response), (
        response.content
    )
    body = response.content.decode()
    for row in rows:
        assert str(row.id) not in body, (row, body[:500])


def _config_body(setup, **refs):
    body = {
        "name": "former-member-config",
        "project": setup["former"]["project"].id,
        "eval_template": setup["system_template"].id,
    }
    body.update(refs)
    body = {key: str(value) for key, value in body.items()}
    body.update({"config": {}, "mapping": MAPPING})
    return body


def _config_state(config):
    config.refresh_from_db()
    return config.project_id, config.eval_template_id, config.kb_id_id, config.name


# Each foreign reference, the field it goes in, and the rest of a body that
# names the former organization's objects only where it must.
CREATE_REFERENCES = {
    "project": lambda setup: {"project": setup["former"]["project"].id},
    "eval_template": lambda setup: {"eval_template": setup["former"]["template"].id},
    "kb_id": lambda setup: {"kb_id": setup["former"]["kb_file"].id},
}
PATCH_REFERENCES = {
    "project": lambda setup: setup["former"]["other_project"].id,
    "eval_template": lambda setup: setup["former"]["other_template"].id,
    "kb_id": lambda setup: setup["former"]["kb_file"].id,
}


@pytest.mark.integration
@pytest.mark.api
class TestFormerMemberTenantScope:
    def test_former_member_experiment_list_hides_former_organization(self, setup):
        client = setup["client"]
        former_dataset = setup["former"]["dataset"]

        _assert_hides(
            client.get(EXPERIMENTS_URL),
            setup["former"]["experiment"],
            setup["unrelated"]["experiment"],
        )
        _assert_hides(
            client.get(EXPERIMENTS_URL, {"dataset_id": str(former_dataset.id)}),
            setup["former"]["experiment"],
        )

    @pytest.mark.parametrize("field", sorted(CREATE_REFERENCES))
    def test_former_member_custom_eval_config_create_answers_former_reference_as_unknown(
        self, setup, field
    ):
        foreign_refs = CREATE_REFERENCES[field](setup)
        foreign_id = foreign_refs[field]
        unknown_id = uuid.uuid4()
        before = CustomEvalConfig.no_workspace_objects.count()

        foreign = setup["client"].post(
            CONFIG_URL, _config_body(setup, **foreign_refs), format="json"
        )
        unknown = setup["client"].post(
            CONFIG_URL, _config_body(setup, **{field: unknown_id}), format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST or _refused(
            foreign
        ), foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert CustomEvalConfig.no_workspace_objects.count() == before

    @pytest.mark.parametrize("field", sorted(PATCH_REFERENCES))
    def test_former_member_custom_eval_config_patch_answers_former_reference_as_unknown(
        self, setup, field
    ):
        config = setup["former"]["config"]
        before = _config_state(config)
        foreign_id = PATCH_REFERENCES[field](setup)
        unknown_id = uuid.uuid4()
        detail = f"{CONFIG_URL}{config.id}/"

        foreign = setup["client"].patch(detail, {field: str(foreign_id)}, format="json")
        unknown = setup["client"].patch(detail, {field: str(unknown_id)}, format="json")

        assert foreign.status_code >= 400, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert _config_state(config) == before

    def test_former_member_custom_eval_config_put_answers_former_project_as_unknown(
        self, setup
    ):
        config = setup["former"]["config"]
        before = _config_state(config)
        foreign_id = setup["former"]["other_project"].id
        unknown_id = uuid.uuid4()
        detail = f"{CONFIG_URL}{config.id}/"

        foreign = setup["client"].put(
            detail, _config_body(setup, project=foreign_id), format="json"
        )
        unknown = setup["client"].put(
            detail, _config_body(setup, project=unknown_id), format="json"
        )

        assert foreign.status_code >= 400, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert _config_state(config) == before

    def test_former_member_eval_task_patch_refuses_former_eval_config(self, setup):
        task = setup["former"]["task"]
        foreign_id = setup["former"]["other_config"].id
        unknown_id = uuid.uuid4()
        detail = f"{TASK_URL}{task.id}/"

        foreign = setup["client"].patch(
            detail, {"evals": [str(foreign_id)]}, format="json"
        )
        unknown = setup["client"].patch(
            detail, {"evals": [str(unknown_id)]}, format="json"
        )

        assert foreign.status_code >= 400, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert set(
            EvalTask.no_workspace_objects.get(id=task.id).evals.values_list(
                "id", flat=True
            )
        ) == {setup["former"]["config"].id}

    def test_former_member_annotation_label_create_answers_former_project_as_unknown(
        self, setup
    ):
        foreign_id = setup["former"]["project"].id
        unknown_id = uuid.uuid4()

        def body(project_id):
            return {
                "name": "former-member-label",
                "type": "categorical",
                "settings": LABEL_SETTINGS,
                "project": str(project_id),
            }

        foreign = setup["client"].post(LABELS_URL, body(foreign_id), format="json")
        unknown = setup["client"].post(LABELS_URL, body(unknown_id), format="json")

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST or _refused(
            foreign
        ), foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert not AnnotationsLabels.all_objects.filter(
            name="former-member-label"
        ).exists()

    @pytest.mark.parametrize(
        "params",
        [{}, {"include_archived": "true"}, {"archived": "true"}],
        ids=["default", "include_archived", "archived"],
    )
    def test_former_member_annotation_labels_list_hides_every_organization(
        self, setup, params
    ):
        _assert_hides(
            setup["client"].get(LABELS_URL, params),
            setup["former"]["label"],
            setup["former"]["archived_label"],
            setup["unrelated"]["label"],
            setup["unrelated"]["archived_label"],
        )

    def test_former_member_knowledge_base_list_hides_every_organization(self, setup):
        _assert_hides(
            setup["client"].get(KB_URL),
            setup["former"]["knowledge_base"],
            setup["unrelated"]["knowledge_base"],
        )

    def test_signed_in_member_still_lists_own_rows(self, setup):
        owner = setup["unrelated_org"].user
        client = signed_in_client(owner.email, "testpassword123")
        own = setup["unrelated"]

        for url, row in [
            (EXPERIMENTS_URL, own["experiment"]),
            (LABELS_URL, own["label"]),
            (KB_URL, own["knowledge_base"]),
            (CONFIG_URL, own["config"]),
        ]:
            response = client.get(url)
            assert response.status_code == status.HTTP_200_OK, (url, response.content)
            assert str(row.id) in response.content.decode(), url
