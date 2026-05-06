"""
Tests for the Graph model.
"""

import pytest
from django.core.exceptions import ValidationError

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agent_playground.models import Graph
from tfc.utils.audit import mute_audit_signals


@pytest.mark.unit
class TestGraphCreation:
    """Tests for Graph model creation."""

    def test_graph_creation_success(self, db, organization, workspace, user):
        """Basic creation with valid data."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="My Graph",
            description="A test graph",
            created_by=user,
        )
        assert graph.id is not None
        assert graph.name == "My Graph"
        assert graph.organization == organization
        assert graph.workspace == workspace
        assert graph.created_by == user
        assert graph.is_template is False

    def test_graph_str(self, db, organization, workspace, user):
        """__str__ returns graph name."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="My Graph",
            created_by=user,
        )
        assert str(graph) == "My Graph"


@pytest.mark.unit
class TestGraphCascadeDelete:
    """Tests for Graph cascade delete behavior."""

    def test_graph_cascade_delete_organization(self, db, organization, workspace, user):
        """Deleting org cascades to graphs."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="To Be Deleted",
            created_by=user,
        )
        graph_id = graph.id

        # Hard delete the organization via queryset (bypasses soft delete).
        # Mute audit signals: the post_delete signal on OrganizationMembership
        # tries to create an AuditLog referencing the org that's being deleted.
        with mute_audit_signals():
            Organization.objects.filter(id=organization.id).delete()

        # Graph should be gone (cascade)
        assert not Graph.all_objects.filter(id=graph_id).exists()

    def test_graph_cascade_delete_workspace(self, db, organization, workspace, user):
        """Deleting workspace cascades to graphs."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="To Be Deleted",
            created_by=user,
        )
        graph_id = graph.id

        # Hard delete workspace via queryset (bypasses soft delete)
        Workspace.all_objects.filter(id=workspace.id).delete()

        # Graph should be gone (cascade)
        assert not Graph.all_objects.filter(id=graph_id).exists()


@pytest.mark.unit
class TestGraphCreatedBy:
    """Tests for Graph.created_by field."""

    def test_created_by_set_on_creation(self, graph, user):
        """created_by is set correctly on the fixture graph."""
        assert graph.created_by == user

    def test_created_by_wrong_org_raises(self, db, organization, workspace):
        """created_by from different org raises ValidationError."""
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@test.com",
            password="testpassword123",
            name="Other",
            organization=other_org,
        )
        graph = Graph(
            organization=organization,
            workspace=workspace,
            name="X",
            created_by=other_user,
        )
        with pytest.raises(ValidationError, match="same organization"):
            graph.full_clean()

    def test_created_by_cascade_delete(self, db, organization, workspace, user):
        """Deleting user cascades to graphs."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Cascade Test Graph",
            created_by=user,
        )
        graph_id = graph.id

        User.objects.filter(id=user.id).delete()
        assert not Graph.all_objects.filter(id=graph_id).exists()


@pytest.mark.unit
class TestGraphCollaborators:
    """Tests for Graph.collaborators M2M field."""

    def test_add_collaborators(self, db, graph, organization):
        """A graph can have multiple collaborators (creator + 2 added)."""
        collab1 = User.objects.create_user(
            email="c1@test.com",
            password="testpassword123",
            name="Collab 1",
            organization=organization,
        )
        collab2 = User.objects.create_user(
            email="c2@test.com",
            password="testpassword123",
            name="Collab 2",
            organization=organization,
        )
        graph.collaborators.add(collab1, collab2)
        assert graph.collaborators.count() == 3

    def test_creator_is_collaborator_by_default(self, graph, user):
        """Creator is auto-added as collaborator."""
        assert graph.collaborators.count() == 1
        assert user in graph.collaborators.all()

    def test_remove_collaborator(self, db, graph, organization):
        """Collaborators can be removed (creator remains)."""
        collab = User.objects.create_user(
            email="c1@test.com",
            password="testpassword123",
            name="Collab 1",
            organization=organization,
        )
        graph.collaborators.add(collab)
        graph.collaborators.remove(collab)
        assert graph.collaborators.count() == 1

    def test_collaborator_delete_does_not_delete_graph(self, db, graph, organization):
        """Deleting a collaborator user removes M2M link but not the graph."""
        collab = User.objects.create_user(
            email="c1@test.com",
            password="testpassword123",
            name="Collab 1",
            organization=organization,
        )
        graph.collaborators.add(collab)
        User.objects.filter(id=collab.id).delete()
        graph.refresh_from_db()  # graph still exists
        assert graph.collaborators.count() == 1  # creator remains

    def test_add_collaborator(self, db, graph, organization):
        """add_collaborator adds user to collaborators."""
        collab = User.objects.create_user(
            email="c1@test.com",
            password="testpassword123",
            name="Collab",
            organization=organization,
        )
        graph.add_collaborator(collab)
        assert collab in graph.collaborators.all()

    def test_reverse_relation(self, db, graph, organization):
        """Collaborators can access graphs via reverse relation."""
        collab = User.objects.create_user(
            email="c1@test.com",
            password="testpassword123",
            name="Collab 1",
            organization=organization,
        )
        graph.collaborators.add(collab)
        assert graph in collab.collaborated_agent_playground_graphs.all()


@pytest.mark.unit
class TestGraphIsTemplate:
    """Tests for Graph.is_template field and validation."""

    def test_template_graph_creation_success(self, db):
        """Template graph with org=None, workspace=None, created_by=None succeeds."""
        graph = Graph.no_workspace_objects.create(
            organization=None,
            workspace=None,
            name="System Template",
            description="A system template",
            is_template=True,
            created_by=None,
        )
        assert graph.id is not None
        assert graph.is_template is True
        assert graph.organization is None
        assert graph.created_by is None
        assert graph.collaborators.count() == 0

    def test_template_with_org_raises(self, db, organization):
        """Template graph with organization set raises ValidationError."""
        graph = Graph(
            organization=organization,
            workspace=None,
            name="Bad Template",
            is_template=True,
            created_by=None,
        )
        with pytest.raises(ValidationError, match="Template graphs must not have"):
            graph.full_clean()

    def test_template_with_created_by_raises(self, db, user):
        """Template graph with created_by set raises ValidationError."""
        graph = Graph(
            organization=None,
            workspace=None,
            name="Bad Template",
            is_template=True,
            created_by=user,
        )
        with pytest.raises(ValidationError, match="Template graphs must not have"):
            graph.full_clean()

    def test_non_template_without_org_raises(self, db):
        """Non-template graph without organization raises ValidationError."""
        graph = Graph(
            organization=None,
            workspace=None,
            name="Bad Graph",
            is_template=False,
            created_by=None,
        )
        with pytest.raises(
            ValidationError, match="Non-template graphs must have an organization"
        ):
            graph.full_clean()

    def test_non_template_without_created_by_raises(self, db, organization):
        """Non-template graph without created_by raises ValidationError."""
        graph = Graph(
            organization=organization,
            workspace=None,
            name="Bad Graph",
            is_template=False,
            created_by=None,
        )
        with pytest.raises(
            ValidationError, match="Non-template graphs must have a created_by"
        ):
            graph.full_clean()

    def test_non_template_with_org_and_user_succeeds(
        self, db, organization, workspace, user
    ):
        """Non-template graph with org and created_by succeeds."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Normal Graph",
            is_template=False,
            created_by=user,
        )
        graph.full_clean()
        assert graph.is_template is False
        assert graph.organization == organization
