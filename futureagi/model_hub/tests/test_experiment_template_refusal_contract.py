"""Missing and inaccessible run templates have one typed, bounded refusal."""

import uuid

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_eval_pins import PIN_UNSET
from model_hub.tests.test_external_template_refusal_scope import (
    _create_v2,
    _dataset,
    _prompt_version,
    no_background,  # noqa: F401
    team,  # noqa: F401
    tenants,  # noqa: F401
)
from model_hub.views.experiments import _resolve_experiment_eval_entries
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [
    pytest.mark.integration,
    pytest.mark.api,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def _template(tenant, *, deleted=False):
    clear_workspace_context()
    return EvalTemplate.no_workspace_objects.create(
        name=f"refusal-{uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        deleted=deleted,
    )


@pytest.mark.parametrize("kind", ["unknown", "other_org", "other_workspace", "deleted"])
def test_unusable_template_has_typed_non_enumerating_refusal(tenants, team, kind):
    tenant_a, tenant_b = tenants
    ids = {
        "unknown": uuid.uuid4(),
        "other_org": _template(tenant_a).id,
        "other_workspace": _template(team).id,
        "deleted": _template(tenant_b, deleted=True).id,
    }
    before = UserEvalMetric.no_workspace_objects.count()

    with pytest.raises(EvalTemplate.DoesNotExist) as refused:
        _resolve_experiment_eval_entries(
            [{"template_id": ids[kind], "name": "refused", "config": {}}],
            tenant_b.organization,
            tenant_b.workspace,
        )

    assert str(refused.value) == "Eval template not found."
    assert str(ids[kind]) not in str(refused.value)
    assert UserEvalMetric.no_workspace_objects.count() == before


def test_accessible_template_still_resolves(tenants):
    _, tenant_b = tenants
    template = _template(tenant_b)

    resolved = _resolve_experiment_eval_entries(
        [{"template_id": template.id, "name": "allowed", "config": {}}],
        tenant_b.organization,
        tenant_b.workspace,
    )

    # An omitted pin is not the same as an explicit null pin at this layer.
    assert resolved == [(template, PIN_UNSET)]


def test_v2_create_returns_clear_identical_refusal_without_writes(tenants, no_background):
    tenant_a, tenant_b = tenants
    template = _template(tenant_a)
    dataset = _dataset(tenant_b, "refused experiment")
    prompt, version = _prompt_version(tenant_b, "refusal prompt")
    before = UserEvalMetric.no_workspace_objects.count()

    foreign = _create_v2(tenant_b, dataset, prompt, version, template.id)
    unknown = _create_v2(tenant_b, dataset, prompt, version, uuid.uuid4())

    assert foreign.status_code == unknown.status_code == 400
    assert foreign.json() == unknown.json()
    assert "Eval template not found." in foreign.content.decode()
    assert str(template.id) not in foreign.content.decode()
    assert UserEvalMetric.no_workspace_objects.count() == before
    assert not ExperimentsTable.no_workspace_objects.filter(dataset=dataset).exists()
