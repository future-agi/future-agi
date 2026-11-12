# Pytest fixtures are imported from a sibling sweep module.
# ruff: noqa: F811
"""A user eval with no workspace opens wherever the eval list shows it.

The eval list and the picker (``build_eval_list_queryset`` in
model_hub/utils/eval_list.py) show a USER template of the organization in
every workspace when its workspace is empty. Every screen that opens it must
admit the same template and nothing more: a template of another workspace or
of another organization stays hidden. Writes keep the earlier production build's
rule: update and delete go through the workspace manager, and a ground-truth
upload keeps the default-workspace rule, so a no-workspace template is not
writable from a second workspace.

Only the external evaluator and the vector store are replaced. Routing,
authentication, membership and the template lookups run their real code.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import OwnerChoices, SourceChoices
from model_hub.models.evals_metric import (
    EvalGroundTruth,
    EvalSettings,
    EvalTemplate,
    EvalTemplateVersion,
    Feedback,
)
from model_hub.tests.test_tenancy_sweep_eval_templates_r1 import (
    assert_missing_parity,
    eval_factory,  # noqa: F401 - shared fixture
    sweep_scope,  # noqa: F401 - shared fixture
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

SPACES = ["default", "second"]


@pytest.fixture
def listed(sweep_scope, eval_factory):
    scope = sweep_scope
    assert scope.workspace.is_default, "setup: first workspace is default"
    second = Workspace.no_workspace_objects.create(
        name=f"workspace-two-{uuid4().hex}",
        organization=scope.organization,
        is_active=True,
        created_by=scope.user,
    )
    org_membership = OrganizationMembership.no_workspace_objects.get(
        user=scope.user, organization=scope.organization
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=scope.user,
        workspace=second,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
            "organization_membership": org_membership,
        },
    )
    assert scope.user.can_access_workspace(second)
    assert scope.user.can_write_to_workspace(second)
    scope.spaces = {"default": scope.workspace, "second": second}

    template = eval_factory("own")
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(workspace=None)
    template.refresh_from_db()
    assert (
        template.workspace_id is None
        and template.organization_id == scope.organization.pk
        and template.owner == OwnerChoices.USER.value
    ), "setup: a no-workspace user eval of this organization"
    scope.template = template
    # A template of another workspace of this organization (no access) and
    # one of another organization: both stay hidden everywhere.
    scope.elsewhere = eval_factory("workspace")
    scope.foreign = eval_factory("organization")
    return scope


def routed_in(scope, space, method, path, data=None):
    """``routed`` from the sweep module, bound to the chosen workspace."""
    workspace = scope.spaces[space]
    clear_workspace_context()
    try:
        response = getattr(scope.client, method)(
            path,
            {} if data is None else data,
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.pk),
            HTTP_X_ORGANIZATION_ID=str(scope.organization.pk),
        )
        request = response.renderer_context["request"]
        assert request.user.pk == scope.user.pk
        assert request.organization.pk == scope.organization.pk
        assert request.workspace.pk == workspace.pk, (
            "setup: request bound to the chosen workspace"
        )
        return response
    finally:
        clear_workspace_context()


READS = [
    ("detail", "/model-hub/eval-templates/{id}/detail/", None),
    ("versions", "/model-hub/eval-templates/{id}/versions/", None),
    ("feedback-list", "/model-hub/eval-templates/{id}/feedback-list/", None),
    ("ground-truth", "/model-hub/eval-templates/{id}/ground-truth/", None),
    ("sdk-code", "/model-hub/eval-sdk-code/", "template_id"),
    ("get-eval-config", "/model-hub/get-eval-config", "eval_id"),
]
READ_IDS = [site[0] for site in READS]


def read(scope, space, site, template_id):
    _name, path, key = site
    if key:
        return routed_in(scope, space, "get", path, {key: str(template_id)})
    return routed_in(scope, space, "get", path.format(id=template_id))


def _require_ee():
    from model_hub.views import separate_evals

    assert separate_evals.APICallLog is not None, "requires the EE usage models"
    return separate_evals.APICallLog


@pytest.mark.parametrize("site", READS, ids=READ_IDS)
@pytest.mark.parametrize("space", SPACES)
def test_no_workspace_eval_opens_in_every_workspace(listed, space, site):
    name = site[0]
    template = listed.template
    if name == "versions":
        for number in (1, 2):
            EvalTemplateVersion.no_workspace_objects.create(
                eval_template=template,
                version_number=number,
                is_default=number == 1,
                organization=template.organization,
                workspace=None,
                created_by=listed.user,
                config_snapshot=template.config,
                criteria=template.criteria,
            )
    if name == "feedback-list":
        _require_ee()
    response = read(listed, space, site, template.pk)
    assert response.status_code == 200, (name, space, response.data)
    result = response.data["result"]
    if name == "detail":
        assert result["id"] == str(template.pk)
        assert result["instructions"] == template.criteria
    elif name in ("versions", "feedback-list", "ground-truth"):
        assert result["template_id"] == str(template.pk)
    elif name == "sdk-code":
        assert template.name in result["python"]
    else:
        assert result["eval"]["id"] == str(template.pk)
        assert result["owner"] == "user"


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
@pytest.mark.parametrize("site", READS, ids=READ_IDS)
@pytest.mark.parametrize("space", SPACES)
def test_other_workspace_and_other_org_evals_stay_hidden(
    listed, space, site, boundary
):
    template = listed.elsewhere if boundary == "workspace" else listed.foreign
    if site[0] == "feedback-list":
        _require_ee()
    missing_id = uuid4()
    missing = read(listed, space, site, missing_id)
    denied = read(listed, space, site, template.pk)
    assert_missing_parity(denied, missing, template.pk, missing_id, statuses=(400, 404))
    assert template.criteria not in json.dumps(denied.json())


@pytest.mark.parametrize("space", SPACES)
def test_a_workspace_eval_stays_in_its_own_workspace(listed, eval_factory, space):
    other_space = "second" if space == "default" else "default"
    sibling = eval_factory("own", workspace=listed.spaces[other_space])
    for site in READS:
        if site[0] == "feedback-list":
            continue
        missing_id = uuid4()
        missing = read(listed, space, site, missing_id)
        denied = read(listed, space, site, sibling.pk)
        assert_missing_parity(
            denied, missing, sibling.pk, missing_id, statuses=(400, 404)
        )


@pytest.mark.parametrize("space", SPACES)
def test_template_helpers_follow_the_list_rule(listed, eval_factory, space):
    """Covers every caller of the shared lookup that has no route test here:
    composite detail and execute, the eval log table, test-evaluation, and the
    eval metrics lookup."""
    from model_hub.views import separate_evals

    request = SimpleNamespace(
        workspace=listed.spaces[space],
        organization=listed.organization,
        user=listed.user,
    )
    system = eval_factory(
        "own", owner=OwnerChoices.SYSTEM.value, organization=None, workspace=None
    )
    other_space = "second" if space == "default" else "default"
    sibling = eval_factory("own", workspace=listed.spaces[other_space])
    clear_workspace_context()
    for visible in (listed.template, system):
        opened = separate_evals._get_accessible_eval_template_for_request(
            visible.pk, request
        )
        assert opened.pk == visible.pk, (space, visible.owner)
        metric_template = separate_evals._get_eval_metric_template(request, visible.pk)
        assert metric_template is not None and metric_template.pk == visible.pk
    for hidden in (listed.elsewhere, listed.foreign, sibling):
        with pytest.raises(EvalTemplate.DoesNotExist):
            separate_evals._get_accessible_eval_template_for_request(hidden.pk, request)
        assert separate_evals._get_eval_metric_template(request, hidden.pk) is None


@pytest.fixture
def execution(listed):
    from model_hub.views import separate_evals

    # No provider work; autospec catches a drift in the execution call.
    with patch.object(
        separate_evals,
        "run_eval_func",
        autospec=True,
        return_value={"score": 1, "reason": "synthetic execution boundary"},
    ) as execute:
        yield execute


@pytest.mark.parametrize("space", SPACES)
def test_no_workspace_eval_runs_in_the_playground_of_every_workspace(
    listed, execution, space
):
    response = routed_in(
        listed,
        space,
        "post",
        "/model-hub/eval-playground/",
        {
            "template_id": str(listed.template.pk),
            "model": None,
            "mapping": {"input": "synthetic"},
        },
    )
    assert response.status_code == 200, (space, response.data)
    execution.assert_called_once()
    args, kwargs = execution.call_args
    assert args[2].pk == listed.template.pk
    assert args[3].pk == listed.organization.pk
    assert kwargs["workspace"].pk == listed.spaces[space].pk


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
@pytest.mark.parametrize("space", SPACES)
def test_other_workspace_and_other_org_evals_never_run(
    listed, execution, space, boundary
):
    template = listed.elsewhere if boundary == "workspace" else listed.foreign
    response = routed_in(
        listed,
        space,
        "post",
        "/model-hub/eval-playground/",
        {"template_id": str(template.pk), "model": None, "mapping": {"input": "x"}},
    )
    assert response.status_code in (400, 404), response.data
    execution.assert_not_called()


@pytest.mark.parametrize("space", SPACES)
def test_no_workspace_eval_log_columns_save_in_every_workspace(listed, space):
    columns = [{"id": "score", "name": "Score", "is_visible": True}]
    response = routed_in(
        listed,
        space,
        "patch",
        "/model-hub/get-eval-logs",
        {
            "eval_id": str(listed.template.pk),
            "column_config": columns,
            "source": "eval_playground",
        },
    )
    assert response.status_code == 200, (space, response.data)
    setting = EvalSettings.no_workspace_objects.get(
        user=listed.user, eval_id=listed.template.pk, source="eval_playground"
    )
    assert setting.column_config == columns


class _VectorRecorder:
    calls = []

    def data_formatter(self, *args, **kwargs):
        type(self).calls.append(kwargs)
        return [], []

    def close(self):
        return None


def _playground_log(scope, space, template):
    api_call_log = _require_ee()
    from tfc.constants.api_calls import APICallStatusChoices

    return api_call_log.objects.create(
        organization=scope.organization,
        workspace=scope.spaces[space],
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.pk),
        config=json.dumps(
            {
                "required_keys": ["input"],
                "mappings": {"input": f"synthetic input {uuid4().hex}"},
                "input_data_types": {"input": "text"},
                "output": {"output": "Failed", "reason": "synthetic"},
                "model": "",
            }
        ),
    )


@pytest.mark.parametrize("space", SPACES)
def test_playground_feedback_links_the_eval_and_stamps_the_request_workspace(
    listed, space
):
    """The feedback links the no-workspace eval in every workspace, and new
    feedback, and its vector, carry the request's workspace."""
    workspace = listed.spaces[space]
    log = _playground_log(listed, space, listed.template)
    _VectorRecorder.calls = []
    with patch("model_hub.views.separate_evals.EmbeddingManager", _VectorRecorder):
        response = routed_in(
            listed,
            space,
            "post",
            "/model-hub/eval-playground/feedback/",
            {
                "log_id": str(log.log_id),
                "action_type": "retune",
                "value": "Passed",
                "explanation": f"retune correction {space}",
            },
        )
    assert response.status_code == 200, (space, response.data)
    feedback = Feedback.no_workspace_objects.get(
        source=SourceChoices.EVAL_PLAYGROUND.value, source_id=str(log.log_id)
    )
    assert feedback.organization_id == listed.organization.pk
    assert feedback.eval_template_id == listed.template.pk, (
        "playground feedback links the no-workspace eval"
    )
    assert feedback.workspace_id == workspace.pk, (
        "new playground feedback carries the request workspace"
    )
    assert [call.get("workspace_id") for call in _VectorRecorder.calls] == [
        str(workspace.pk)
    ], "the feedback vector carries the request workspace"
    assert [call.get("organization_id") for call in _VectorRecorder.calls] == [
        str(listed.organization.pk)
    ]


# Writes keep the earlier production build's rule. Production updated and deleted a
# template through EvalTemplate.objects (the workspace manager: the default
# workspace sees no-workspace rows, another workspace does not), and checked a
# ground-truth upload with the default-workspace rule. These are guards: they
# hold before and after the fix.


def _update(scope, space, template, description):
    return routed_in(
        scope,
        space,
        "post",
        "/model-hub/update-eval-template/",
        {"eval_template_id": str(template.pk), "description": description},
    )


@pytest.mark.parametrize("space", SPACES)
def test_update_of_a_no_workspace_eval_follows_production(listed, space):
    before = listed.template.description
    marker = f"update-marker-{space}-{uuid4().hex[:8]}"
    response = _update(listed, space, listed.template, marker)
    listed.template.refresh_from_db()
    if space == "default":
        assert response.status_code == 200, response.data
        assert listed.template.description == marker
    else:
        assert response.status_code in (400, 404), response.data
        assert listed.template.description == before


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
@pytest.mark.parametrize("space", SPACES)
def test_update_of_another_workspace_eval_stays_refused(listed, space, boundary):
    template = listed.elsewhere if boundary == "workspace" else listed.foreign
    before = template.description
    response = _update(listed, space, template, f"denied-update-{uuid4().hex[:8]}")
    template.refresh_from_db()
    assert response.status_code in (400, 404), response.data
    assert template.description == before


@pytest.mark.parametrize("route", ["single", "bulk"])
@pytest.mark.parametrize("space", SPACES)
def test_delete_of_a_no_workspace_eval_follows_production(listed, space, route):
    if route == "single":
        response = routed_in(
            listed,
            space,
            "post",
            "/model-hub/delete-eval-template/",
            {"eval_template_id": str(listed.template.pk)},
        )
    else:
        response = routed_in(
            listed,
            space,
            "post",
            "/model-hub/eval-templates/bulk-delete/",
            {"template_ids": [str(listed.template.pk)]},
        )
    deleted = EvalTemplate.all_objects.get(pk=listed.template.pk).deleted
    if space == "default":
        assert response.status_code == 200, response.data
        assert deleted is True
    else:
        assert deleted is False, (route, response.data)
        if route == "single":
            assert response.status_code == 404, response.data


@pytest.mark.parametrize("route", ["single", "bulk"])
@pytest.mark.parametrize("boundary", ["workspace", "organization"])
@pytest.mark.parametrize("space", SPACES)
def test_delete_of_another_workspace_eval_stays_refused(
    listed, space, boundary, route
):
    template = listed.elsewhere if boundary == "workspace" else listed.foreign
    if route == "single":
        response = routed_in(
            listed,
            space,
            "post",
            "/model-hub/delete-eval-template/",
            {"eval_template_id": str(template.pk)},
        )
        assert response.status_code == 404, response.data
    else:
        routed_in(
            listed,
            space,
            "post",
            "/model-hub/eval-templates/bulk-delete/",
            {"template_ids": [str(template.pk)]},
        )
    assert EvalTemplate.all_objects.get(pk=template.pk).deleted is False


@pytest.mark.parametrize("target", ["no-workspace", "elsewhere", "foreign"])
def test_ground_truth_upload_keeps_production_rule_in_a_second_workspace(
    listed, target
):
    template = {
        "no-workspace": listed.template,
        "elsewhere": listed.elsewhere,
        "foreign": listed.foreign,
    }[target]
    before = EvalGroundTruth.no_workspace_objects.filter(eval_template=template).count()
    response = routed_in(
        listed,
        "second",
        "post",
        f"/model-hub/eval-templates/{template.pk}/ground-truth/upload/",
        {
            "name": f"gt-upload-{uuid4().hex[:8]}",
            "columns": ["input"],
            "data": [{"input": "synthetic"}],
        },
    )
    assert response.status_code == 404, response.data
    after = EvalGroundTruth.no_workspace_objects.filter(eval_template=template).count()
    assert after == before
