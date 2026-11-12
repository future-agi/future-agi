"""Preserve scoped references and pinned-version semantics together."""

import uuid
from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    CREATE_URL,
    _body,
    _created_id,
    _detail_url,
    _metric,
    _template,
)
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    judge_a as judge_a,
)
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    tenant_b as tenant_b,
)
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    world_a as world_a,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_temporal():
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="scope-pin-union-test",
    ):
        yield


@pytest.fixture
def other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        name=f"other-{uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )


def create_pinned(auth_client, world_a, judge_a):
    template, _versions = judge_a
    body = _body(world_a, template, config={"version_pin": "v1"})
    body["column_id"] = str(world_a["column"].id)
    experiment_id = _created_id(auth_client.post(CREATE_URL, body, format="json"))
    metric = _metric(experiment_id)
    return (
        experiment_id,
        metric,
        {
            "id": str(metric.id),
            "template_id": str(metric.template_id),
            "name": metric.name,
            "config": dict(metric.config),
            "model": metric.model,
            "kb_id": str(metric.kb_id) if metric.kb_id else None,
        },
    )


def kb(org, workspace):
    # Set ownership explicitly after save signals stamp request-local context.
    obj = KnowledgeBaseFile.no_workspace_objects.create(
        name=f"scope-pin-kb-{uuid.uuid4().hex[:8]}",
        organization=org,
        workspace=workspace,
    )
    KnowledgeBaseFile.no_workspace_objects.filter(id=obj.id).update(
        organization=org, workspace=workspace
    )
    return obj


@pytest.mark.parametrize("revoke", ["workspace", "organization"])
def test_unchanged_revoked_template_and_pin_are_kept(
    auth_client, world_a, judge_a, other_workspace, tenant_b, revoke
):
    experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
    template, (v1, _v2) = judge_a
    changes = (
        {"workspace": other_workspace}
        if revoke == "workspace"
        else {"organization": tenant_b["org"], "workspace": tenant_b["workspace"]}
    )
    EvalTemplate.no_workspace_objects.filter(id=template.id).update(**changes)
    entry["name"] = "changed name, retained reference"
    response = auth_client.put(
        _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.template_id == template.id
    assert metric.pinned_version_id == v1.id
    assert metric.name == entry["name"]


@pytest.mark.parametrize("pin", ["v2", "clear", "explicit"])
def test_changed_pin_on_revoked_template_is_refused_before_any_write(
    auth_client, world_a, judge_a, other_workspace, pin
):
    experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
    template, (v1, v2) = judge_a
    EvalTemplate.no_workspace_objects.filter(id=template.id).update(
        workspace=other_workspace
    )
    if pin == "clear":
        entry["config"].pop("version_pin")
    elif pin == "explicit":
        entry["config"].pop("version_pin")
        entry["pinned_version_id"] = str(v2.id)
    else:
        entry["config"]["version_pin"] = pin
    original_name = metric.name
    entry["name"] = "must not persist"
    response = auth_client.put(
        _detail_url(experiment_id),
        {"column_id": None, "user_eval_metrics": [entry]},
        format="json",
    )
    assert 400 <= response.status_code < 500, response.content
    metric.refresh_from_db()
    assert metric.pinned_version_id == v1.id
    assert metric.name == original_name
    assert ExperimentsTable.objects.get(id=experiment_id).column_id is not None


@pytest.mark.parametrize("operation", ["create", "new-update", "changed-update"])
def test_template_from_another_workspace_is_refused(
    auth_client, world_a, judge_a, organization, other_workspace, operation
):
    foreign_template, _ = _template(organization, other_workspace, "outside")
    # Fixture save signals can stamp the active workspace.
    EvalTemplate.no_workspace_objects.filter(id=foreign_template.id).update(
        workspace=other_workspace
    )
    if operation == "create":
        body = _body(world_a, foreign_template, name="outside-scope-template")
        response = auth_client.post(CREATE_URL, body, format="json")
        assert not ExperimentsTable.objects.filter(name=body["name"]).exists()
    else:
        experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
        entry["template_id"] = str(foreign_template.id)
        if operation == "new-update":
            entry.pop("id")
        response = auth_client.put(
            _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
        )
        metric.refresh_from_db()
        assert metric.template_id == judge_a[0].id
        assert list(
            ExperimentsTable.objects.get(
                id=experiment_id
            ).user_eval_template_ids.values_list("id", flat=True)
        ) == [metric.id]
    assert 400 <= response.status_code < 500, response.content


@pytest.mark.parametrize("operation", ["create", "new-update", "changed-update"])
@pytest.mark.parametrize("scope", ["organization", "workspace"])
def test_new_or_changed_kb_requires_full_scope_before_writes(
    auth_client,
    world_a,
    judge_a,
    organization,
    other_workspace,
    tenant_b,
    operation,
    scope,
):
    foreign = (
        kb(tenant_b["org"], tenant_b["workspace"])
        if scope == "organization"
        else kb(organization, other_workspace)
    )
    if operation == "create":
        body = _body(
            world_a, judge_a[0], name="outside-scope-kb", kb_id=str(foreign.id)
        )
        response = auth_client.post(CREATE_URL, body, format="json")
        assert not ExperimentsTable.objects.filter(name=body["name"]).exists()
    else:
        experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
        entry["kb_id"] = str(foreign.id)
        if operation == "new-update":
            entry.pop("id")
        response = auth_client.put(
            _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
        )
        metric.refresh_from_db()
        assert metric.kb_id is None
        assert (
            ExperimentsTable.objects.get(
                id=experiment_id
            ).user_eval_template_ids.count()
            == 1
        )
    assert 400 <= response.status_code < 500, response.content


@pytest.mark.parametrize("change_name", [False, True])
def test_unchanged_forged_foreign_org_kb_is_refused(
    auth_client, world_a, judge_a, tenant_b, change_name
):
    experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
    foreign = kb(tenant_b["org"], tenant_b["workspace"])
    UserEvalMetric.no_workspace_objects.filter(id=metric.id).update(kb_id=foreign.id)
    entry["kb_id"] = str(foreign.id)
    if change_name:
        entry["name"] = "must not persist"
    response = auth_client.put(
        _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
    )
    assert 400 <= response.status_code < 500, response.content
    metric.refresh_from_db()
    assert metric.name != "must not persist"


def test_unchanged_same_org_kb_survives_workspace_revocation(
    auth_client, world_a, judge_a, organization, other_workspace
):
    experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
    retained = kb(organization, other_workspace)
    UserEvalMetric.no_workspace_objects.filter(id=metric.id).update(kb_id=retained.id)
    entry["kb_id"] = str(retained.id)
    entry["name"] = "retained same-organization KB"
    response = auth_client.put(
        _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.kb_id == retained.id


def test_scope_validation_precedes_pin_resolution_and_all_writes(
    auth_client, world_a, judge_a
):
    from model_hub.views import experiments

    events = []
    original_resolve = experiments.resolve_eval_entries
    original_require = getattr(experiments, "_require_eval_metric_entries", None)

    def require(*args, **kwargs):
        if not events:
            assert not ExperimentsTable.objects.filter(
                name="ordered-eval-validation"
            ).exists()
        events.append("scope")
        if original_require:
            return original_require(*args, **kwargs)

    def resolve(*args, **kwargs):
        events.append("pin")
        assert "scope" in events[:-1]
        assert kwargs.get("accessible_templates") is not None
        assert not ExperimentsTable.objects.filter(
            name="ordered-eval-validation"
        ).exists()
        return original_resolve(*args, **kwargs)

    with (
        patch.object(experiments, "_require_eval_metric_entries", require, create=True),
        patch.object(experiments, "resolve_eval_entries", resolve),
    ):
        response = auth_client.post(
            CREATE_URL,
            _body(
                world_a,
                judge_a[0],
                name="ordered-eval-validation",
                config={"version_pin": "v1"},
            ),
            format="json",
        )
    assert response.status_code == 200, response.content
    assert events[:2] == ["scope", "pin"]


def test_pin_must_belong_to_newly_resolved_template(
    auth_client, world_a, judge_a, organization, workspace
):
    experiment_id, metric, entry = create_pinned(auth_client, world_a, judge_a)
    replacement, _ = _template(organization, workspace, "replacement")
    entry["template_id"] = str(replacement.id)
    entry["config"].pop("version_pin")
    entry["pinned_version_id"] = str(judge_a[1][0].id)
    response = auth_client.put(
        _detail_url(experiment_id), {"user_eval_metrics": [entry]}, format="json"
    )
    assert 400 <= response.status_code < 500, response.content
    metric.refresh_from_db()
    assert metric.template_id == judge_a[0].id
    assert metric.pinned_version_id == judge_a[1][0].id
