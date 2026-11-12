# Pytest fixtures are imported from a sibling sweep module.
# ruff: noqa: F811
"""Prompt tenancy regressions under real authentication.

Also a prompt's Evaluations drawer with a user eval that has no workspace.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate, PromptVersion
from model_hub.services.prompt_label import move_label
from model_hub.tests.test_tenancy_sweep_eval_templates_r1 import (
    assert_missing_parity,
    eval_factory,  # noqa: F401 - explicitly register the shared pytest fixture
    routed,
    sweep_scope,  # noqa: F401 - explicitly register the shared pytest fixture
    target_space,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.error_codes import get_error_message

pytestmark = pytest.mark.django_db


@pytest.fixture
def labeled_version_factory(sweep_scope, prompt_version):
    # Use IR's own same-tenant PromptTemplate/PromptVersion factories. Explicit
    # spaces on foreign parents avoid BaseModel.save's ambient default landing.
    clear_workspace_context()
    label, _ = PromptLabel.no_workspace_objects.get_or_create(
        name="Production",
        organization=None,
        type=LabelTypeChoices.SYSTEM.value,
        deleted=False,
        defaults={"workspace": None},
    )
    assert label.workspace_id is None

    def create(boundary):
        if boundary == "own":
            version = prompt_version
        else:
            space = target_space(sweep_scope, boundary)
            template = PromptTemplate.no_workspace_objects.create(
                name=f"sweep-prompt-{uuid4().hex}",
                organization=space.organization,
                workspace=space,
            )
            version = PromptVersion.no_workspace_objects.create(
                original_template=template,
                template_version="v1",
                prompt_config_snapshot=prompt_version.prompt_config_snapshot,
            )
        # The real writer establishes both the through row and label head.
        move_label(version, label, publish=False)
        assert PromptVersion.labels.through.objects.filter(
            promptversion_id=version.pk, promptlabel_id=label.pk
        ).exists()
        return version, label

    return create


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r01_foreign_version_label_is_unchanged(
    sweep_scope, labeled_version_factory, boundary
):
    version, label = labeled_version_factory(boundary)
    missing_id = uuid4()
    missing = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-labels/remove/",
        {"label_id": str(label.pk), "version_id": str(missing_id)},
    )
    denied = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-labels/remove/",
        {"label_id": str(label.pk), "version_id": str(version.pk)},
    )
    assert PromptVersion.labels.through.objects.filter(
        promptversion_id=version.pk, promptlabel_id=label.pk
    ).exists()
    # The sweep requires 404 for BOTH unknown and foreign-org ids. IR's two
    # distinct 400 bodies are intentionally not accepted as secure behavior.
    assert_missing_parity(denied, missing, version.pk, missing_id)


def test_r01_same_workspace_label_can_be_removed(sweep_scope, labeled_version_factory):
    version, label = labeled_version_factory("own")
    response = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-labels/remove/",
        {"label_id": str(label.pk), "version_id": str(version.pk)},
    )
    assert response.status_code == 200, response.data
    assert not PromptVersion.labels.through.objects.filter(
        promptversion_id=version.pk, promptlabel_id=label.pk
    ).exists()


def existing_prompt_eval(prompt_template, eval_template, user):
    return PromptEvalConfig.no_workspace_objects.create(
        name=f"existing-{uuid4().hex}",
        prompt_template=prompt_template,
        eval_template=eval_template,
        mapping={"input": "original"},
        config={},
        user=user,
    )


@pytest.mark.parametrize("mode", ["create", "update"])
@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r02_foreign_eval_cannot_be_linked(
    sweep_scope, eval_factory, prompt_template, mode, boundary
):
    foreign = eval_factory(boundary)
    payload = {"name": f"replacement-{uuid4().hex}", "mapping": {"input": "synthetic"}}
    if mode == "update":
        original = existing_prompt_eval(
            prompt_template, eval_factory(), sweep_scope.user
        )
        payload["user_eval_id"] = str(original.pk)
    before = list(
        PromptEvalConfig.no_workspace_objects.filter(prompt_template=prompt_template)
        .order_by("pk")
        .values()
    )
    missing_id = uuid4()
    path = (
        f"/model-hub/prompt-templates/{prompt_template.pk}/update-evaluation-configs/"
    )
    missing = routed(sweep_scope, "post", path, {**payload, "id": str(missing_id)})
    denied = routed(sweep_scope, "post", path, {**payload, "id": str(foreign.pk)})
    after = list(
        PromptEvalConfig.no_workspace_objects.filter(prompt_template=prompt_template)
        .order_by("pk")
        .values()
    )
    assert after == before
    # A foreign id answers 404, the same as an id that does not exist.
    assert_missing_parity(denied, missing, foreign.pk, missing_id)


@pytest.mark.parametrize("mode", ["create", "update"])
def test_r02_same_workspace_eval_can_be_linked(
    sweep_scope, eval_factory, prompt_template, mode
):
    target = eval_factory()
    payload = {
        "id": str(target.pk),
        "name": f"permitted-{uuid4().hex}",
        "mapping": {"input": "synthetic"},
    }
    original = None
    if mode == "update":
        original = existing_prompt_eval(
            prompt_template, eval_factory(), sweep_scope.user
        )
        payload["user_eval_id"] = str(original.pk)
    response = routed(
        sweep_scope,
        "post",
        f"/model-hub/prompt-templates/{prompt_template.pk}/update-evaluation-configs/",
        payload,
    )
    assert response.status_code == 200, response.data
    linked = PromptEvalConfig.no_workspace_objects.get(
        pk=response.data["result"]["prompt_eval_config_id"]
    )
    assert linked.prompt_template_id == prompt_template.pk
    assert linked.eval_template_id == target.pk
    assert linked.mapping == payload["mapping"]
    assert linked.name == payload["name"]
    if original is not None:
        assert linked.pk == original.pk


def folder_for(scope, boundary):
    space = target_space(scope, boundary)
    return PromptFolder.no_workspace_objects.create(
        name=f"sweep-folder-{uuid4().hex}",
        organization=space.organization,
        workspace=space,
    )


def draft_payload(folder_id, snapshot, name):
    # create_draft indexes prompt_config[0]; an empty default would produce an
    # unrelated 400 AFTER inserting the template, masking the tenancy defect.
    return {"prompt_folder": str(folder_id), "name": name, "prompt_config": [snapshot]}


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r03_foreign_folder_cannot_receive_draft(sweep_scope, prompt_version, boundary):
    folder = folder_for(sweep_scope, boundary)
    missing_id = uuid4()
    name = f"draft-{uuid4().hex}"
    before_templates = set(
        PromptTemplate.no_workspace_objects.values_list("pk", flat=True)
    )
    before_versions = set(
        PromptVersion.no_workspace_objects.values_list("pk", flat=True)
    )
    missing = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-templates/create-draft/",
        draft_payload(missing_id, prompt_version.prompt_config_snapshot, name),
    )
    denied = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-templates/create-draft/",
        draft_payload(folder.pk, prompt_version.prompt_config_snapshot, name),
    )
    assert (
        set(PromptTemplate.no_workspace_objects.values_list("pk", flat=True))
        == before_templates
    )
    assert (
        set(PromptVersion.no_workspace_objects.values_list("pk", flat=True))
        == before_versions
    )
    # A foreign folder may answer the unknown-folder refusal (400) or 404.
    assert_missing_parity(denied, missing, folder.pk, missing_id, statuses=(400, 404))


def test_r03_same_workspace_folder_receives_draft(sweep_scope, prompt_version):
    folder = folder_for(sweep_scope, "own")
    name = f"draft-{uuid4().hex}"
    response = routed(
        sweep_scope,
        "post",
        "/model-hub/prompt-templates/create-draft/",
        draft_payload(folder.pk, prompt_version.prompt_config_snapshot, name),
    )
    assert response.status_code == 200, response.data
    created = PromptTemplate.no_workspace_objects.get(pk=response.data["result"]["id"])
    assert created.name == name
    assert created.organization_id == sweep_scope.organization.pk
    # IR create_draft omits workspace; its UUID model can retain a legacy NULL
    # landing. The fixture's default W1 legitimately sees same-org NULL rows.
    assert sweep_scope.workspace.is_default
    assert created.workspace_id in (None, sweep_scope.workspace.pk)
    assert created.prompt_folder_id == folder.pk
    assert PromptVersion.no_workspace_objects.filter(
        original_template=created, template_version="v1", is_draft=True
    ).exists()


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_f03_existing_foreign_link_is_not_embedded(
    sweep_scope, eval_factory, prompt_template, boundary
):
    # Seed the old cross-workspace link directly in the database. The write
    # path no longer creates one, so going through it would stop exercising
    # this read, which must also be right for links saved before that change.
    foreign = eval_factory(boundary)
    link = existing_prompt_eval(prompt_template, foreign, sweep_scope.user)
    response = routed(
        sweep_scope,
        "get",
        f"/model-hub/prompt-templates/{prompt_template.pk}/evaluation-configs/",
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["template_id"] == str(prompt_template.pk)
    configs = result["evaluation_configs"]
    assert all(item["id"] != str(link.pk) for item in configs)
    assert str(foreign.pk) not in json.dumps(result)
    assert foreign.config["eval_type_id"] not in json.dumps(result)


def test_f03_same_workspace_link_is_embedded(
    sweep_scope, eval_factory, prompt_template
):
    own = eval_factory()
    link = existing_prompt_eval(prompt_template, own, sweep_scope.user)
    response = routed(
        sweep_scope,
        "get",
        f"/model-hub/prompt-templates/{prompt_template.pk}/evaluation-configs/",
    )
    assert response.status_code == 200, response.data
    configs = response.data["result"]["evaluation_configs"]
    assert len(configs) == 1
    assert configs[0]["id"] == str(link.pk)
    assert configs[0]["eval_template_id"] == str(own.pk)
    assert configs[0]["config"] == own.config


# A prompt's Evaluations drawer and a user eval with no workspace. The eval
# picker offers such an eval in every workspace of its organization
# (model_hub/utils/eval_list.py build_eval_list_queryset). The earlier production
# build listed, edited and removed its config on a prompt in any of them.


@pytest.fixture
def second_space(sweep_scope):
    """The actor in a second workspace of the same organization (not default).

    Unlike ``other_workspace``, which the actor must not reach, the actor is a
    member here, so requests run in it.
    """
    space = Workspace.no_workspace_objects.create(
        name=f"sweep-second-{uuid4().hex}",
        organization=sweep_scope.organization,
        is_active=True,
        created_by=sweep_scope.user,
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=sweep_scope.user, organization=sweep_scope.organization
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=sweep_scope.user,
        workspace=space,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    clear_workspace_context()
    assert not space.is_default
    assert sweep_scope.user.can_access_workspace(space)
    assert sweep_scope.user.can_write_to_workspace(space)
    return SimpleNamespace(**{**vars(sweep_scope), "workspace": space})


@pytest.fixture(params=["default", "second"])
def drawer(request, sweep_scope, second_space, prompt_template):
    """A prompt, the workspace its drawer runs in, and the other workspace."""
    if request.param == "default":
        assert sweep_scope.workspace.is_default
        return SimpleNamespace(
            space=sweep_scope,
            prompt=prompt_template,
            elsewhere=second_space.workspace,
        )
    prompt = PromptTemplate.no_workspace_objects.create(
        name=f"sweep-prompt-{uuid4().hex}",
        organization=second_space.organization,
        workspace=second_space.workspace,
    )
    return SimpleNamespace(
        space=second_space, prompt=prompt, elsewhere=sweep_scope.workspace
    )


def no_workspace_eval(eval_factory):
    """A user eval of the organization with no workspace, as production has."""
    clear_workspace_context()
    template = eval_factory(workspace=None)
    # Saving fills an empty workspace from a bound context; store NULL the way
    # production rows hold it.
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(workspace=None)
    template.refresh_from_db()
    assert template.workspace_id is None
    assert template.organization_id is not None
    return template


def drawer_configs(space, prompt):
    response = routed(
        space, "get", f"/model-hub/prompt-templates/{prompt.pk}/evaluation-configs/"
    )
    assert response.status_code == 200, response.data
    return response.data["result"]["evaluation_configs"]


def save_eval_config(space, prompt, payload):
    return routed(
        space,
        "post",
        f"/model-hub/prompt-templates/{prompt.pk}/update-evaluation-configs/",
        payload,
    )


def remove_eval_config(space, prompt, config_id):
    return routed(
        space,
        "delete",
        f"/model-hub/prompt-templates/{prompt.pk}/delete-evaluation-config/"
        f"?id={config_id}",
    )


def prompt_eval_rows(prompt):
    return list(
        PromptEvalConfig.no_workspace_objects.filter(prompt_template=prompt)
        .order_by("pk")
        .values()
    )


def test_no_workspace_eval_is_listed(drawer, eval_factory):
    picked = no_workspace_eval(eval_factory)
    link = existing_prompt_eval(drawer.prompt, picked, drawer.space.user)
    configs = drawer_configs(drawer.space, drawer.prompt)
    assert [item["id"] for item in configs] == [str(link.pk)]
    assert configs[0]["eval_template_id"] == str(picked.pk)
    assert configs[0]["name"] == link.name
    assert configs[0]["config"] == picked.config


def test_no_workspace_eval_config_can_be_edited(drawer, eval_factory):
    picked = no_workspace_eval(eval_factory)
    link = existing_prompt_eval(drawer.prompt, picked, drawer.space.user)
    payload = {
        "id": str(picked.pk),
        "user_eval_id": str(link.pk),
        "name": f"edited-{uuid4().hex}",
        "mapping": {"input": "edited"},
    }
    response = save_eval_config(drawer.space, drawer.prompt, payload)
    assert response.status_code == 200, response.data
    assert response.data["result"]["prompt_eval_config_id"] == str(link.pk)
    link.refresh_from_db()
    assert not link.deleted
    assert link.eval_template_id == picked.pk
    assert link.name == payload["name"]
    assert link.mapping == payload["mapping"]


def test_no_workspace_eval_can_be_added(drawer, eval_factory):
    picked = no_workspace_eval(eval_factory)
    name = f"added-{uuid4().hex}"
    response = save_eval_config(
        drawer.space,
        drawer.prompt,
        {"id": str(picked.pk), "name": name, "mapping": {"input": "added"}},
    )
    assert response.status_code == 200, response.data
    added = PromptEvalConfig.no_workspace_objects.get(
        pk=response.data["result"]["prompt_eval_config_id"]
    )
    assert added.prompt_template_id == drawer.prompt.pk
    assert added.eval_template_id == picked.pk
    assert added.name == name
    configs = drawer_configs(drawer.space, drawer.prompt)
    assert [item["id"] for item in configs] == [str(added.pk)]


def test_no_workspace_eval_config_can_be_removed(drawer, eval_factory):
    picked = no_workspace_eval(eval_factory)
    link = existing_prompt_eval(drawer.prompt, picked, drawer.space.user)
    response = remove_eval_config(drawer.space, drawer.prompt, link.pk)
    assert response.status_code == 200, response.data
    link.refresh_from_db()
    assert link.deleted
    assert drawer_configs(drawer.space, drawer.prompt) == []


def test_clashing_name_is_refused_with_the_existing_config_listed(drawer, eval_factory):
    picked = no_workspace_eval(eval_factory)
    link = existing_prompt_eval(drawer.prompt, picked, drawer.space.user)
    other = eval_factory(workspace=drawer.space.workspace)
    before = prompt_eval_rows(drawer.prompt)
    response = save_eval_config(
        drawer.space,
        drawer.prompt,
        {"id": str(other.pk), "name": link.name, "mapping": {"input": "clash"}},
    )
    assert response.status_code == 400, response.data
    message = get_error_message("PROMPT_EVAL_TEMPLATE_EXISTS")
    assert message in json.dumps(response.json())
    assert prompt_eval_rows(drawer.prompt) == before
    # The config that holds the name is on screen next to the refusal.
    configs = drawer_configs(drawer.space, drawer.prompt)
    assert str(link.pk) in [item["id"] for item in configs]


def test_eval_of_another_workspace_stays_hidden_and_refused(drawer, eval_factory):
    foreign = eval_factory(workspace=drawer.elsewhere)
    # A seeded link, as test_f03 does: the read must stay closed on its own.
    link = existing_prompt_eval(drawer.prompt, foreign, drawer.space.user)
    before = prompt_eval_rows(drawer.prompt)
    configs = drawer_configs(drawer.space, drawer.prompt)
    assert all(item["id"] != str(link.pk) for item in configs)
    assert str(foreign.pk) not in json.dumps(configs)
    edited = save_eval_config(
        drawer.space,
        drawer.prompt,
        {
            "id": str(foreign.pk),
            "user_eval_id": str(link.pk),
            "name": f"edited-{uuid4().hex}",
            "mapping": {"input": "edited"},
        },
    )
    added = save_eval_config(
        drawer.space,
        drawer.prompt,
        {
            "id": str(foreign.pk),
            "name": f"added-{uuid4().hex}",
            "mapping": {"input": "added"},
        },
    )
    assert edited.status_code == 404, edited.data
    assert added.status_code == 404, added.data
    assert prompt_eval_rows(drawer.prompt) == before


def test_remove_needs_the_prompt_in_the_request_workspace(
    second_space, prompt_template, eval_factory
):
    # Removal is bound to the prompt: from the second workspace the default
    # workspace's prompt is not found, so its config stays.
    picked = no_workspace_eval(eval_factory)
    link = existing_prompt_eval(prompt_template, picked, second_space.user)
    response = remove_eval_config(second_space, prompt_template, link.pk)
    assert response.status_code in (400, 404), response.data
    link.refresh_from_db()
    assert not link.deleted


@pytest.mark.parametrize("drawer", ["second"], indirect=True)
def test_no_workspace_eval_of_another_organization_stays_hidden_and_refused(
    drawer, eval_factory
):
    # Another organization's user eval that has no workspace, seeded on the
    # prompt as test_f03 does: from a second workspace the drawer does not
    # list it, and edit and add refuse it.
    clear_workspace_context()
    foreign = eval_factory("organization", workspace=None)
    EvalTemplate.no_workspace_objects.filter(pk=foreign.pk).update(workspace=None)
    foreign.refresh_from_db()
    assert foreign.workspace_id is None
    assert foreign.organization_id == drawer.space.foreign_org.pk
    assert foreign.organization_id != drawer.prompt.organization_id
    assert not drawer.space.workspace.is_default
    link = existing_prompt_eval(drawer.prompt, foreign, drawer.space.user)
    before = prompt_eval_rows(drawer.prompt)
    configs = drawer_configs(drawer.space, drawer.prompt)
    assert all(item["id"] != str(link.pk) for item in configs)
    assert str(foreign.pk) not in json.dumps(configs)
    assert foreign.config["eval_type_id"] not in json.dumps(configs)
    edited = save_eval_config(
        drawer.space,
        drawer.prompt,
        {
            "id": str(foreign.pk),
            "user_eval_id": str(link.pk),
            "name": f"edited-{uuid4().hex}",
            "mapping": {"input": "edited"},
        },
    )
    added = save_eval_config(
        drawer.space,
        drawer.prompt,
        {
            "id": str(foreign.pk),
            "name": f"added-{uuid4().hex}",
            "mapping": {"input": "added"},
        },
    )
    assert edited.status_code == 404, edited.data
    assert added.status_code == 404, added.data
    assert prompt_eval_rows(drawer.prompt) == before
