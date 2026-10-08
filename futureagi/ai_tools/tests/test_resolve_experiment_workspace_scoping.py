"""Regression test for the missing workspace filter in resolve_experiment.

`resolve_experiment` accepted a `workspace` kwarg but never used it, unlike
its siblings (`resolve_dataset`, `resolve_project`, `resolve_prompt_template`)
which all filter by workspace when it's provided. This was latent while the
org-scoping bug made every call crash before reaching the workspace check;
now that org-scoping works, two same-named experiments in different
workspaces of the same org would trip the `count() == 1` gate instead of
resolving to the one in the caller's workspace.
"""

import pytest

from accounts.models.workspace import Workspace
from ai_tools.resolvers import resolve_experiment
from ai_tools.tests.fixtures import make_dataset
from model_hub.models.experiments import ExperimentsTable


@pytest.mark.django_db
class TestResolveExperimentWorkspaceScoping:
    def test_resolve_by_name_scopes_to_workspace_when_provided(self, tool_context):
        """Same-named experiments in two workspaces of one org must not collide
        when a workspace is given -- resolve_experiment should return the one
        in the caller's workspace instead of "multiple matches"."""
        other_workspace = Workspace.objects.create(
            name="Other WS",
            organization=tool_context.organization,
            created_by=tool_context.user,
        )

        dataset_a = make_dataset(tool_context, name="Dataset A")
        dataset_b = make_dataset(tool_context, name="Dataset B")
        dataset_b.workspace = other_workspace
        dataset_b.save(update_fields=["workspace"])

        experiment_a = ExperimentsTable.objects.create(
            name="Baseline v1", dataset=dataset_a
        )
        ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset_b)

        result, err = resolve_experiment(
            "Baseline v1", tool_context.organization, workspace=tool_context.workspace
        )

        assert err is None
        assert result == experiment_a

    def test_resolve_by_name_without_workspace_sees_both(self, tool_context):
        """No workspace given -- org-wide behavior is unchanged: two matches
        across workspaces is still ambiguous, same as before this fix."""
        other_workspace = Workspace.objects.create(
            name="Other WS",
            organization=tool_context.organization,
            created_by=tool_context.user,
        )

        dataset_a = make_dataset(tool_context, name="Dataset A")
        dataset_b = make_dataset(tool_context, name="Dataset B")
        dataset_b.workspace = other_workspace
        dataset_b.save(update_fields=["workspace"])

        ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset_a)
        ExperimentsTable.objects.create(name="Baseline v1", dataset=dataset_b)

        result, err = resolve_experiment("Baseline v1", tool_context.organization)

        assert result is None
        assert err == "No experiment found matching 'Baseline v1'."
