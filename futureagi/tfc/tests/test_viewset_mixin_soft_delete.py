"""Real annotation-label routes exclude deleted rows even with all_objects."""
import uuid
from types import SimpleNamespace

import pytest

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.views.develop_annotations import AnnotationsLabelsViewSet
from tfc.tests.test_viewset_mixin_union_scope import _tenant
from tfc.utils.tests.two_tenants import signed_in_client
from tracer.models.project import Project

URL = "/model-hub/annotations-labels/"
pytestmark = pytest.mark.django_db


def _label(tenant, name, *, deleted=False, null_workspace=False):
    project = Project.no_workspace_objects.create(
        name=name + " project", organization=tenant.org,
        workspace=None if null_workspace else tenant.workspace,
        model_type="GenerativeLLM", trace_type="observe",
    )
    return AnnotationsLabels.all_objects.create(
        name=name, type="text", settings={}, organization=tenant.org,
        workspace=None if null_workspace else tenant.workspace,
        project=project, deleted=deleted,
    )


@pytest.fixture
def labels(monkeypatch):
    mine, other = _tenant("Soft-delete A"), _tenant("Soft-delete B")
    live = _label(mine, "visible label")
    soft = _label(mine, "archived unique label", deleted=True)
    null_ws = _label(mine, "legacy project label", null_workspace=True)
    foreign = _label(other, "other label")
    # This is the production viewset and production route, not a probe view.
    # all_objects makes the mixin's deleted=False condition load-bearing.
    monkeypatch.setattr(
        AnnotationsLabelsViewSet, "queryset", AnnotationsLabels.all_objects.all(),
        raising=False,
    )
    client = signed_in_client(mine.user.email, "viewset-union-scope-pw-123")
    client.defaults["HTTP_X_WORKSPACE_ID"] = str(mine.workspace.id)
    return SimpleNamespace(mine=mine, client=client, live=live, soft=soft,
                           null_ws=null_ws, foreign=foreign)


def _rows(response):
    assert response.status_code == 200, response.content
    data = response.json()
    payload = data.get("result", data) if isinstance(data, dict) else data
    if isinstance(payload, dict):
        return payload.get("results", payload.get("data", [])), payload.get("count")
    return payload, None


def test_list_and_search_exclude_soft_deleted_rows(labels):
    rows, count = _rows(labels.client.get(URL))
    expected = {str(labels.live.id), str(labels.null_ws.id)}
    assert {str(row["id"]) for row in rows} == expected
    if count is not None:
        assert count == len(expected)
    rows, count = _rows(labels.client.get(URL, {"search": labels.soft.name}))
    assert rows == []
    if count is not None:
        assert count == 0


def test_deleted_and_foreign_retrieve_match_unknown(labels):
    unknown = labels.client.get(f"{URL}{uuid.uuid4()}/")
    soft = labels.client.get(f"{URL}{labels.soft.id}/")
    foreign = labels.client.get(f"{URL}{labels.foreign.id}/")
    assert unknown.status_code == soft.status_code == foreign.status_code == 404
    assert soft.json() == unknown.json()
    assert foreign.json() == unknown.json()


def test_explicit_no_organization_returns_no_rows(labels):
    view = AnnotationsLabelsViewSet()
    view.request = SimpleNamespace(
        user=labels.mine.user, organization=None,
        workspace=labels.mine.workspace, query_params={},
    )
    assert list(view.get_queryset()) == []
