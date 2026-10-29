"""Per-request list and related-field scoping, including warm imports."""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import StatusType
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.views.develop_annotations import AnnotationsLabelsViewSet
from model_hub.views.experiments import ExperimentListAPIView
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project

tenants = tenants_fixture
pytestmark = pytest.mark.django_db


def _request(caller, method, payload):
    clear_workspace_context()
    request = getattr(APIRequestFactory(), method)("/", payload, format="json")
    request.organization = caller["org"]
    request.workspace = caller["workspace"]
    force_authenticate(request, user=caller["user"])
    return request


def _experiment(dataset, name):
    return ExperimentsTable.objects.create(
        name=name, dataset=dataset, status=StatusType.COMPLETED.value,
    )


def test_experiment_list_and_dataset_filter_do_not_escape_scope(tenants):
    a, b, _ = tenants
    foreign = _experiment(a["dataset"], "foreign")
    own = _experiment(b["dataset"], "own")
    view = ExperimentListAPIView.as_view()
    response = view(_request(b, "get", {}))
    assert response.status_code == 200, response.data
    assert {x["id"] for x in response.data["results"]} == {str(own.id)}
    response = view(_request(b, "get", {"dataset_id": str(foreign.dataset_id)}))
    assert response.status_code == 200
    assert response.data["results"] == []
    assert ExperimentsTable.objects.count() == 2


def test_experiment_list_does_not_escape_workspace(tenants):
    a, _, _ = tenants
    workspace = Workspace.objects.create(
        name="private experiments", organization=a["org"], created_by=a["user"], is_default=False,
    )
    dataset, _, _, _ = _grid(a, workspace)
    _experiment(dataset, "private")
    own = _experiment(a["dataset"], "own")
    response = ExperimentListAPIView.as_view()(_request(a, "get", {}))
    assert response.status_code == 200, response.data
    assert {x["id"] for x in response.data["results"]} == {str(own.id)}


def _project(tenant, workspace=None):
    return Project.no_workspace_objects.create(
        name="project", organization=tenant["org"],
        workspace=workspace or tenant["workspace"],
        model_type=AIModel.ModelTypes.GENERATIVE_LLM, trace_type="observe",
    )


def _body(project):
    return {"name": "scope label", "type": "categorical", "project": str(project.id),
            "settings": {"options": [{"label": "yes"}, {"label": "no"}],
                         "multi_choice": False, "auto_annotate": False,
                         "rule_prompt": "", "strategy": None}}


@pytest.mark.parametrize("method", ["post", "patch", "put"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_annotation_project_assignment_rejects_foreign_ids(tenants, method, foreign_scope):
    a, b, _ = tenants
    own = _project(a)
    if foreign_scope == "organization":
        foreign = _project(b)
    else:
        workspace = Workspace.objects.create(
            name="private project", organization=a["org"], created_by=a["user"], is_default=False,
        )
        foreign = _project(a, workspace)
    label = AnnotationsLabels.no_workspace_objects.create(
        name="own label", type="categorical", organization=a["org"],
        workspace=a["workspace"], project=own, settings=_body(own)["settings"],
    )
    before = list(AnnotationsLabels.no_workspace_objects.order_by("id").values())
    actions = {"post": "create", "patch": "partial_update", "put": "update"}
    kwargs = {} if method == "post" else {"pk": str(label.id)}
    response = AnnotationsLabelsViewSet.as_view({method: actions[method]})(
        _request(a, method, _body(foreign)), **kwargs,
    )
    assert response.status_code in (403, 404), response.data
    assert list(AnnotationsLabels.no_workspace_objects.order_by("id").values()) == before


def test_annotation_project_assignment_accepts_own_project(tenants):
    a, _, _ = tenants
    own = _project(a)
    response = AnnotationsLabelsViewSet.as_view({"post": "create"})(
        _request(a, "post", _body(own)),
    )
    assert response.status_code == 200, response.data
    assert AnnotationsLabels.no_workspace_objects.filter(project=own, organization=a["org"]).count() == 1
