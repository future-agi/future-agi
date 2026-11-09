"""DSX launch L5: get_evals_list says which version runs and the pass rule.

Each dataset eval item carries version / version_number (the version a run
uses: a live pin, else the template default), pinned_version_number,
latest_version / latest_version_number, pass_threshold (the threshold a run
applies) and higher_is_better (dev's rule: a score below the threshold fails).
Two organizations: org B never reads org A's evals or their versions.
"""

import pytest
from rest_framework import status

from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.tests.test_dsx_l5_experiment_defaults import Tenant

FIELDS = (
    "version",
    "version_number",
    "pinned_version_number",
    "latest_version",
    "latest_version_number",
    "pass_threshold",
    "higher_is_better",
)


@pytest.fixture
def tenants(db):
    a = Tenant("a")
    b = Tenant("b")
    yield a, b
    a.client.stop_workspace_injection()
    b.client.stop_workspace_injection()


def _items(tenant, dataset):
    response = tenant.eval_list(dataset)
    assert response.status_code == status.HTTP_200_OK, response.content
    return {item["name"]: item for item in response.json()["result"]["evals"]}


def _view(item):
    return {key: item[key] for key in FIELDS}


def _versions(tenant, *thresholds):
    return [
        EvalTemplateVersion.objects.create_version(
            tenant.template,
            criteria=f"Is {{{{output}}}} polite? ({n})",
            organization=tenant.org,
            workspace=tenant.workspace,
            output_type_normalized="percentage",
            pass_threshold=threshold,
        )
        for n, threshold in enumerate(thresholds, start=1)
    ]


@pytest.mark.django_db
def test_the_version_that_runs_the_newest_and_the_pin(tenants):
    a, _ = tenants
    unpinned, pinned = a.evals
    v1, v2 = _versions(a, 0.6, 0.7)
    pinned.pinned_version = v1
    pinned.save()

    items = _items(a, a.dataset)

    assert _view(items[unpinned.name]) == {
        "version": "v2",
        "version_number": 2,
        "pinned_version_number": None,
        "latest_version": "v2",
        "latest_version_number": 2,
        "pass_threshold": 0.7,
        "higher_is_better": True,
    }
    assert _view(items[pinned.name]) == {
        "version": "v1",
        "version_number": 1,
        "pinned_version_number": 1,
        "latest_version": "v2",
        "latest_version_number": 2,
        "pass_threshold": 0.6,
        "higher_is_better": True,
    }

    # A new version becomes the default: the unpinned eval runs it, the pin holds.
    _versions(a, 0.8)
    items = _items(a, a.dataset)
    assert items[unpinned.name]["version"] == "v3"
    assert items[unpinned.name]["pass_threshold"] == 0.8
    assert items[pinned.name]["version"] == "v1"
    assert items[pinned.name]["latest_version"] == "v3"


@pytest.mark.django_db
def test_a_deleted_pin_falls_back_to_the_default(tenants):
    a, _ = tenants
    _, pinned = a.evals
    v1, _v2 = _versions(a, 0.6, 0.7)
    pinned.pinned_version = v1
    pinned.save()
    EvalTemplateVersion.objects.filter(id=v1.id).update(deleted=True)

    item = _items(a, a.dataset)[pinned.name]

    assert item["version"] == "v2"
    assert item["pinned_version_number"] == 1
    assert item["latest_version"] == "v2"
    assert item["pass_threshold"] == 0.7


@pytest.mark.django_db
def test_the_binding_threshold_wins_over_the_version(tenants):
    a, _ = tenants
    one, _ = a.evals
    _versions(a, 0.6)
    one.config = {"mapping": {}, "run_config": {"pass_threshold": 0.9}}
    one.save()

    item = _items(a, a.dataset)[one.name]

    assert item["pass_threshold"] == 0.9
    assert item["higher_is_better"] is True
    # The existing run_config view is unchanged by the new fields.
    assert item["run_config"]["pass_threshold"] == 0.9


@pytest.mark.django_db
def test_a_template_without_versions_reports_none(tenants):
    a, _ = tenants
    item = _items(a, a.dataset)[a.evals[0].name]

    assert item["version"] is None
    assert item["latest_version"] is None
    assert item["pinned_version_number"] is None
    assert item["is_experiment_default"] is False


@pytest.mark.django_db
def test_org_b_never_reads_org_a_evals_or_versions(tenants):
    a, b = tenants
    _versions(a, 0.6, 0.7, 0.75)
    _versions(b, 0.5)

    # Positive control: A reads her versions.
    a_items = _items(a, a.dataset)
    assert {item["latest_version"] for item in a_items.values()} == {"v3"}

    # B on A's dataset: refused, nothing of A's in the body.
    response = b.eval_list(a.dataset)
    assert response.status_code >= 400
    body = response.content.decode()
    for e in a.evals:
        assert e.name not in body and str(e.id) not in body
    assert a.template.name not in body

    # B's own list: B's evals with B's versions only.
    b_items = _items(b, b.dataset)
    assert set(b_items) == {e.name for e in b.evals}
    assert {item["latest_version"] for item in b_items.values()} == {"v1"}
    assert {item["template_id"] for item in b_items.values()} == {str(b.template.id)}
