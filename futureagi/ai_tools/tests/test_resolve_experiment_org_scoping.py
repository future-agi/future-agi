"""Regression test for the org-scoping bug in resolve_experiment.

test_resolvers.py deliberately mocks every Django ORM call (see its own
module docstring), so a wrong field name in a `.get()`/`.filter()` kwarg
never surfaces there — a MagicMock accepts any keyword silently. This
file hits a real test database instead, which is the only way to catch
`resolve_experiment` filtering `ExperimentsTable` by `organization=`, a
field that model doesn't have (it's reachable only via `dataset__organization`).
"""

import pytest

from accounts.models.organization import Organization
from ai_tools.resolvers import resolve_experiment
from ai_tools.tests.fixtures import make_dataset
from model_hub.models.experiments import ExperimentsTable


@pytest.mark.django_db
class TestResolveExperimentOrgScoping:
    def test_resolve_by_uuid(self, tool_context):
        dataset = make_dataset(tool_context)
        experiment = ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset)

        result, err = resolve_experiment(str(experiment.id), tool_context.organization)

        assert err is None
        assert result == experiment

    def test_resolve_by_name(self, tool_context):
        dataset = make_dataset(tool_context)
        experiment = ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset)

        result, err = resolve_experiment("Baseline v1", tool_context.organization)

        assert err is None
        assert result == experiment

    def test_resolve_by_uuid_not_found_for_other_org(self, tool_context):
        """A UUID that exists but belongs to a different org must not resolve."""
        dataset = make_dataset(tool_context)
        experiment = ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset)
        other_org = Organization.objects.create(name="Other Org")

        result, err = resolve_experiment(str(experiment.id), other_org)

        assert result is None
        assert "not found" in err.lower()
