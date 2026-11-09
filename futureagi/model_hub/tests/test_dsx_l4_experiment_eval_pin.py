"""Experiment evals run the eval version their card pins (DSX launch lane L4).

The DSX experiment wizard pins an eval card to a version ("V1 · pinned",
``config.version_pin``). Dev kept the label inside ``config`` and never set
``UserEvalMetric.pinned_version``, so the run used the template default while
the card claimed the pin. These tests drive the real V2 create / edit / detail
endpoints and prove, with two organizations, that a tenant can pin only
versions of eval templates it can see.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

CREATE_URL = "/model-hub/experiments/v2/"


def _detail_url(experiment_id):
    return f"/model-hub/experiments/v2/{experiment_id}/"


@pytest.fixture(autouse=True)
def _no_temporal():
    """The create/edit views start a Temporal workflow; no worker runs here."""
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-l4-test",
    ):
        yield


def _second_tenant(label="Tenant B"):
    """A separate organization with its own owner, workspace and API client."""
    from conftest import WorkspaceAwareAPIClient

    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} {suffix}")
    user = User.objects.create_user(
        email=f"l4-pin-b-{suffix}@futureagi.com",
        password="testpassword123",
        name=f"{label} Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"{label} Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
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
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return org, workspace, user, client


def _world(org, workspace, user, label):
    """A dataset with one input column and a row, plus a published prompt."""
    dataset = Dataset.no_workspace_objects.create(
        name=f"l4-pin-{label}-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="What is 2+2?"
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save()
    prompt = PromptTemplate.no_workspace_objects.create(
        name=f"l4-prompt-{label}-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        created_by=user,
    )
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "Answer {{question}}"}],
            "configuration": {},
        },
        variable_names={},
        is_default=True,
    )
    return {
        "dataset": dataset,
        "column": column,
        "prompt": prompt,
        "pv": prompt_version,
    }


def _template(org, workspace, label, owner=OwnerChoices.USER.value, versions=2):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"l4-judge-{label}-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        owner=owner,
        config={
            "required_keys": ["output"],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
        },
        criteria="Is {{output}} correct?",
        model="turing_small",
        eval_type="code",
    )
    if org is None:
        # The save signals (tfc/utils/signals.py) stamp a new row with the
        # current organization and workspace; a seeded system template has
        # neither. Its versions keep tenant A's stamp on purpose: that is what
        # the signals do to a system template's versions in production, and
        # another organization must still be able to pin them.
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(
            organization=None, workspace=None
        )
        template.refresh_from_db()
    made = [
        EvalTemplateVersion.objects.create_version(
            eval_template=template,
            prompt_messages=[{"role": "system", "content": f"rubric v{n}"}],
            config_snapshot=dict(template.config),
            criteria=f"rubric v{n}",
            model="turing_small",
            organization=org,
            workspace=workspace,
        )
        for n in range(1, versions + 1)
    ]
    return template, made


def _body(world, template, *, name=None, **eval_extra):
    config = {"mapping": {"output": str(world["column"].id)}}
    config.update(eval_extra.pop("config", {}))
    entry = {
        "template_id": str(template.id),
        "name": "judge",
        "config": config,
        "model": "turing_small",
        **eval_extra,
    }
    return {
        "name": name or f"l4-exp-{uuid.uuid4().hex[:6]}",
        "dataset_id": str(world["dataset"].id),
        "experiment_type": "llm",
        "prompt_config": [
            {
                "name": "answer",
                "prompt_id": str(world["prompt"].id),
                "prompt_version": str(world["pv"].id),
                "model": "gpt-4o-mini",
            }
        ],
        "user_eval_metrics": [entry],
    }


def _created_id(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"].split("experiment_id=")[1].strip()


def _metric(experiment_id):
    return ExperimentsTable.objects.get(id=experiment_id).user_eval_template_ids.get()


@pytest.fixture
def world_a(organization, workspace, user):
    return _world(organization, workspace, user, "a")


@pytest.fixture
def judge_a(organization, workspace):
    return _template(organization, workspace, "a")


@pytest.fixture
def tenant_b(db):
    org, workspace, user, client = _second_tenant()
    yield {"org": org, "workspace": workspace, "user": user, "client": client}
    client.stop_workspace_injection()


@pytest.mark.django_db
def test_create_pins_the_version_the_card_names(auth_client, world_a, judge_a):
    template, (v1, v2) = judge_a
    assert EvalTemplateVersion.objects.get_default(template) == v2

    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "v1"}),
            format="json",
        )
    )

    metric = _metric(experiment_id)
    assert metric.pinned_version_id == v1.id
    # The runner resolves the pin, not the template default.
    assert EvalTemplateVersion.objects.resolve_for_metric(metric) == v1

    detail = auth_client.get(_detail_url(experiment_id))
    assert detail.status_code == status.HTTP_200_OK, detail.content
    (row,) = detail.json()["result"]["user_eval_metrics"]
    assert row["pinned_version_id"] == str(v1.id)
    assert row["pinned_version_number"] == 1
    assert row["config"]["version_pin"] == "v1"


@pytest.mark.django_db
def test_create_pins_by_version_id(auth_client, world_a, judge_a):
    template, (v1, _v2) = judge_a

    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, pinned_version_id=str(v1.id)),
            format="json",
        )
    )

    metric = _metric(experiment_id)
    assert metric.pinned_version_id == v1.id
    assert metric.config["version_pin"] == "v1"


@pytest.mark.django_db
def test_latest_pins_the_newest_version_even_when_default_is_older(
    auth_client, world_a, judge_a
):
    template, (v1, v2) = judge_a
    EvalTemplateVersion.objects.filter(eval_template=template).update(is_default=False)
    EvalTemplateVersion.objects.filter(id=v1.id).update(is_default=True)

    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "latest"}),
            format="json",
        )
    )

    metric = _metric(experiment_id)
    assert metric.pinned_version_id == v2.id
    # Stored as the concrete version, so a reopened card names what runs.
    assert metric.config["version_pin"] == "v2"


@pytest.mark.django_db
def test_no_pin_runs_the_default_version(auth_client, world_a, judge_a):
    template, (_v1, v2) = judge_a

    experiment_id = _created_id(
        auth_client.post(CREATE_URL, _body(world_a, template), format="json")
    )

    metric = _metric(experiment_id)
    assert metric.pinned_version_id is None
    assert EvalTemplateVersion.objects.resolve_for_metric(metric) == v2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra",
    [
        {"config": {"version_pin": "v9"}},
        {"config": {"version_pin": "pinned-ish"}},
        {"pinned_version_id": str(uuid.uuid4())},
    ],
    ids=["unknown-number", "garbage-label", "unknown-id"],
)
def test_a_pin_that_names_no_version_is_refused_before_anything_is_written(
    auth_client, world_a, judge_a, extra
):
    template, _versions = judge_a

    response = auth_client.post(
        CREATE_URL, _body(world_a, template, name="l4-refused", **extra), format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert not ExperimentsTable.objects.filter(name="l4-refused").exists()


@pytest.mark.django_db
def test_two_different_pins_are_refused(auth_client, world_a, judge_a):
    template, (v1, _v2) = judge_a

    response = auth_client.post(
        CREATE_URL,
        _body(
            world_a,
            template,
            name="l4-conflict",
            pinned_version_id=str(v1.id),
            config={"version_pin": "v2"},
        ),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert not ExperimentsTable.objects.filter(name="l4-conflict").exists()


@pytest.mark.django_db
def test_edit_switches_keeps_and_clears_the_pin(auth_client, world_a, judge_a):
    template, (v1, v2) = judge_a
    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "v1"}),
            format="json",
        )
    )
    (row,) = auth_client.get(_detail_url(experiment_id)).json()["result"][
        "user_eval_metrics"
    ]
    entry = {
        "id": row["id"],
        "template_id": row["template_id"],
        "name": row["name"],
        "config": {**row["config"], "version_pin": "v2"},
        "model": row["model"],
    }

    # The editor picks V2: the eval re-runs on V2.
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-l4-edit",
    ) as started:
        switched = auth_client.put(
            _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
        )
    assert switched.status_code == status.HTTP_200_OK, switched.content
    metric = UserEvalMetric.objects.get(id=row["id"])
    assert metric.pinned_version_id == v2.id
    assert started.call_args.kwargs["rerun_eval_template_ids"] == [row["id"]]
    (after,) = switched.json()["result"]["user_eval_metrics"]
    assert after["pinned_version_number"] == 2

    # An edit that sends the saved config back (as a reopened card does)
    # keeps the pin.
    (saved,) = switched.json()["result"]["user_eval_metrics"]
    resent = {**entry, "config": saved["config"]}
    kept = auth_client.put(
        _detail_url(experiment_id), {"user_eval_metrics": [resent]}, format="json"
    )
    assert kept.status_code == status.HTTP_200_OK, kept.content
    assert UserEvalMetric.objects.get(id=row["id"]).pinned_version_id == v2.id

    # pinned_version_id null with a config that names no version clears it:
    # the template default runs again.
    unpinned_config = {k: v for k, v in saved["config"].items() if k != "version_pin"}
    cleared = auth_client.put(
        _detail_url(experiment_id),
        {
            "user_eval_metrics": [
                {**entry, "config": unpinned_config, "pinned_version_id": None}
            ]
        },
        format="json",
    )
    assert cleared.status_code == status.HTTP_200_OK, cleared.content
    metric = UserEvalMetric.objects.get(id=row["id"])
    assert metric.pinned_version_id is None
    assert "version_pin" not in metric.config
    assert v1.id != v2.id


@pytest.mark.django_db
def test_an_edit_whose_config_names_no_pin_runs_the_default(
    auth_client, world_a, judge_a
):
    """The saved config and the pin never disagree after an edit.

    An edit replaces the eval's config. One that changes another field (here
    the model) and names no version must not keep a pin its saved config no
    longer shows: the card would read "default" while the run used V1.
    """
    template, (v1, v2) = judge_a
    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "v1"}),
            format="json",
        )
    )
    (row,) = auth_client.get(_detail_url(experiment_id)).json()["result"][
        "user_eval_metrics"
    ]
    assert row["config"]["version_pin"] == "v1"
    no_pin = {k: v for k, v in row["config"].items() if k != "version_pin"}

    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-l4-unpin",
    ) as started:
        response = auth_client.put(
            _detail_url(experiment_id),
            {
                "user_eval_metrics": [
                    {
                        "id": row["id"],
                        "template_id": row["template_id"],
                        "name": row["name"],
                        "config": no_pin,
                        "model": "turing_large",
                    }
                ]
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    metric = UserEvalMetric.objects.get(id=row["id"])
    assert metric.model == "turing_large"
    assert metric.pinned_version_id is None
    assert "version_pin" not in metric.config
    assert EvalTemplateVersion.objects.resolve_for_metric(metric) == v2
    assert started.call_args.kwargs["rerun_eval_template_ids"] == [row["id"]]
    (after,) = response.json()["result"]["user_eval_metrics"]
    assert after["pinned_version_id"] is None
    assert after["pinned_version_number"] is None
    assert "version_pin" not in after["config"]

    # The same model change that resends the saved pin keeps it, and the saved
    # config still names it.
    response = auth_client.put(
        _detail_url(experiment_id),
        {
            "user_eval_metrics": [
                {
                    "id": row["id"],
                    "template_id": row["template_id"],
                    "name": row["name"],
                    "config": {**no_pin, "version_pin": "v1"},
                    "model": "turing_small",
                }
            ]
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    metric = UserEvalMetric.objects.get(id=row["id"])
    assert metric.model == "turing_small"
    assert metric.pinned_version_id == v1.id
    assert metric.config["version_pin"] == "v1"


@pytest.mark.django_db
def test_an_edit_with_an_unknown_pin_changes_nothing(auth_client, world_a, judge_a):
    template, _versions = judge_a
    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "v1"}),
            format="json",
        )
    )
    experiment = ExperimentsTable.objects.get(id=experiment_id)
    assert experiment.snapshot_dataset_id
    snapshot_question = Column.no_workspace_objects.get(
        dataset_id=experiment.snapshot_dataset_id, name="question", deleted=False
    )
    column_before = experiment.column_id
    assert column_before != snapshot_question.id
    (row,) = auth_client.get(_detail_url(experiment_id)).json()["result"][
        "user_eval_metrics"
    ]
    bad = {
        "id": row["id"],
        "template_id": row["template_id"],
        "name": row["name"],
        "config": {**row["config"], "version_pin": "v9"},
        "model": row["model"],
    }

    # The edit also changes the experiment's column; the unknown pin must
    # refuse the whole edit before the column (or anything else) changes.
    response = auth_client.put(
        _detail_url(experiment_id),
        {"column_id": str(snapshot_question.id), "user_eval_metrics": [bad]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert "no version V9" in response.content.decode()
    experiment.refresh_from_db()
    assert experiment.column_id == column_before
    metric = UserEvalMetric.objects.get(id=row["id"])
    assert metric.pinned_version.version_number == 1


@pytest.mark.django_db
def test_switching_the_template_drops_the_old_templates_pin(
    organization, workspace, user, world_a, judge_a
):
    from model_hub.views.experiments import _diff_and_update_evals

    template, (v1, _v2) = judge_a
    other, _other_versions = _template(organization, workspace, "a2")
    experiment = ExperimentsTable.objects.create(
        name="l4-switch",
        dataset=world_a["dataset"],
        snapshot_dataset=world_a["dataset"],
    )
    metric = UserEvalMetric.objects.create(
        name="judge",
        organization=organization,
        workspace=workspace,
        dataset=world_a["dataset"],
        template=template,
        config={"mapping": {"output": str(world_a["column"].id)}},
        source_id=str(experiment.id),
        user=user,
        pinned_version=v1,
    )
    experiment.user_eval_template_ids.add(metric)

    # Column pre-creation needs a run config this bare experiment lacks.
    with patch("model_hub.views.experiment_runner.ExperimentRunner"):
        _diff_and_update_evals(
            experiment,
            [
                {
                    "id": metric.id,
                    "template_id": other.id,
                    "name": "judge",
                    "config": {"mapping": {"output": str(world_a["column"].id)}},
                    "model": "turing_small",
                }
            ],
            organization,
            user,
            workspace=workspace,
        )

    metric.refresh_from_db()
    assert metric.template_id == other.id
    assert metric.pinned_version_id is None


@pytest.mark.django_db
def test_other_org_cannot_use_a_private_template_or_its_versions(
    world_a, judge_a, tenant_b
):
    template_a, (v1_a, _v2_a) = judge_a
    b = tenant_b
    world_b = _world(b["org"], b["workspace"], b["user"], "b")

    # A's private template, by id, with or without a pin.
    for extra in ({}, {"config": {"version_pin": "v1"}}):
        response = b["client"].post(
            CREATE_URL,
            _body(world_b, template_a, name="l4-b-steals-template", **extra),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert template_a.name not in response.content.decode()
    assert not ExperimentsTable.objects.filter(name="l4-b-steals-template").exists()
    assert not UserEvalMetric.objects.filter(
        organization=b["org"], template=template_a
    ).exists()


@pytest.mark.django_db
def test_other_org_cannot_pin_another_templates_version(world_a, judge_a, tenant_b):
    _template_a, (v1_a, _v2_a) = judge_a
    b = tenant_b
    world_b = _world(b["org"], b["workspace"], b["user"], "b")
    system, _system_versions = _template(
        None, None, "system", owner=OwnerChoices.SYSTEM.value, versions=1
    )
    assert system.organization_id is None and system.workspace_id is None

    response = b["client"].post(
        CREATE_URL,
        _body(world_b, system, name="l4-b-foreign-pin", pinned_version_id=str(v1_a.id)),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert not ExperimentsTable.objects.filter(name="l4-b-foreign-pin").exists()

    # Positive control: B pins the system template's own version.
    (sv1,) = _system_versions
    ok = b["client"].post(
        CREATE_URL,
        _body(world_b, system, name="l4-b-own-pin", pinned_version_id=str(sv1.id)),
        format="json",
    )
    metric = _metric(_created_id(ok))
    assert metric.pinned_version_id == sv1.id
    assert metric.organization_id == b["org"].id


@pytest.mark.django_db
def test_other_org_cannot_read_or_edit_the_pin(auth_client, world_a, judge_a, tenant_b):
    template, (v1, v2) = judge_a
    experiment_id = _created_id(
        auth_client.post(
            CREATE_URL,
            _body(world_a, template, config={"version_pin": "v1"}),
            format="json",
        )
    )
    metric = _metric(experiment_id)
    client_b = tenant_b["client"]

    read_b = client_b.get(_detail_url(experiment_id))
    assert read_b.status_code == status.HTTP_404_NOT_FOUND
    assert str(v1.id) not in read_b.content.decode()

    edit_b = client_b.put(
        _detail_url(experiment_id),
        {
            "user_eval_metrics": [
                {
                    "id": str(metric.id),
                    "template_id": str(template.id),
                    "name": "judge",
                    "config": {"mapping": metric.config["mapping"]},
                    "pinned_version_id": str(v2.id),
                }
            ]
        },
        format="json",
    )
    assert edit_b.status_code == status.HTTP_404_NOT_FOUND
    metric.refresh_from_db()
    assert metric.pinned_version_id == v1.id
