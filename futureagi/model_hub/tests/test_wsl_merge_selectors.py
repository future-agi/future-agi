"""N09/N10: central workspace rules cannot bypass authenticated-empty scope."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile, Row
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.utils import workspace_scope as scope
from tfc.middleware.workspace_context import clear_workspace_context, set_workspace_context, workspace_context

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.mark.parametrize('field', ['workspace', 'dataset__workspace'])
@pytest.mark.parametrize('selection', ['named', 'default', 'none'])
@pytest.mark.parametrize('empty', [False, True])
def test_central_workspace_predicate_and_empty_guard_are_both_required(landing, field, selection, empty):
    foreign_org = Organization.objects.create(name='Foreign NULL owner')
    datasets = [Dataset.objects.create(name=name, organization=org, workspace=ws) for name, org, ws in (
        ('named', landing.org, landing.other), ('default', landing.org, landing.default),
        ('legacy', landing.org, None), ('foreign', foreign_org, None),
    )]
    rows = [Row.objects.create(dataset=d, order=0) for d in datasets]
    model = Dataset if field == 'workspace' else Row
    targets = datasets if model is Dataset else rows
    owner_field = 'organization' if model is Dataset else 'dataset__organization'
    prebuilt = model.no_workspace_objects.filter(pk__in=[r.pk for r in targets])
    selected = {'named': landing.other, 'default': landing.default, 'none': None}[selection]
    with workspace_context(None if empty else selected, landing.org, landing.user):
        set_workspace_context(require_workspace=empty)
        with patch.object(scope, 'workspace_scope_q', wraps=scope.workspace_scope_q) as central:
            predicate = scope.explicit_workspace_filter(selected, field)
        assert isinstance(predicate, Q)
        if empty:
            central.assert_not_called()
            expected = set()
        else:
            central.assert_called_once_with(selected, field)
            indices = {'named': [0], 'default': [1, 2], 'none': [0, 1, 2]}[selection]
            expected = {targets[i].pk for i in indices}
        assert set(prebuilt.filter(predicate, **{owner_field: landing.org}).values_list('pk', flat=True)) == expected
        request = SimpleNamespace(user=landing.user, organization=landing.org, workspace=selected)
        assert set(prebuilt.filter(scope.request_workspace_filter(request, field), **{owner_field: landing.org}).values_list('pk', flat=True)) == expected


@pytest.mark.parametrize('stale_workspace', [False, True])
def test_empty_scope_cannot_escape_optimize_or_or_explicit_kb_path(landing, stale_workspace):
    optimize = OptimizeDataset.objects.create(
        name='Direct org only', organization=landing.org, workspace=landing.other,
        optimize_type=OptimizeDataset.OptimizeType.choices[0][0],
        environment=OptimizeDataset.EnvTypes.choices[0][0], version='1',
    )
    kb = KnowledgeBaseFile.objects.create(name='Visible otherwise', organization=landing.org, workspace=landing.other)
    foreign_org = Organization.objects.create(name='Other KB owner')
    foreign = KnowledgeBaseFile.objects.create(name='Foreign legacy', organization=foreign_org)
    optimized = OptimizeDataset.no_workspace_objects.filter(pk=optimize.pk)
    knowledge = KnowledgeBaseFile.no_workspace_objects.filter(pk__in=[kb.pk, foreign.pk])
    assert optimized.exists() and knowledge.count() == 2
    # Use real pre-auth QuerySets as the manager facade: .filter and related
    # traversal remain real SQL, but no fresh manager can hide a missing guard.
    with patch.object(OptimizeDataset, 'no_workspace_objects', optimized), patch.object(KnowledgeBaseFile, 'no_workspace_objects', knowledge):
        with workspace_context(None, landing.org, landing.user):
            set_workspace_context(require_workspace=True)
            selected = landing.other if stale_workspace else None
            request = SimpleNamespace(user=landing.user, organization=landing.org, workspace=selected)
            assert not scope.scoped_optimize_dataset_queryset(request).exists()
            assert not scope.knowledge_base_queryset_for(landing.org, selected).exists()
        with workspace_context(landing.other, landing.org, landing.user):
            set_workspace_context(require_workspace=True)
            request = SimpleNamespace(user=landing.user, organization=landing.org, workspace=landing.other)
            assert list(scope.scoped_optimize_dataset_queryset(request)) == [optimize]
            assert list(scope.knowledge_base_queryset_for(landing.org, landing.other)) == [kb]
