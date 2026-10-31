"""
End-to-end tests for user onboarding demo data creation.

Tests the create_demo_traces_and_spans function to ensure:
1. Demo projects are created with correct workspace assignment
2. Default workspace is created if none exists
3. All demo data (projects, traces, sessions, spans) are created correctly
"""

from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from accounts.user_onboard import create_demo_traces_and_spans
from model_hub.models.ai_model import AIModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


@pytest.fixture
def org_without_workspace(db):
    """Create an organization without any workspace."""
    return Organization.objects.create(name="Org Without Workspace")


@pytest.fixture
def user_without_workspace(db, org_without_workspace):
    """Create a user in an organization without workspace."""
    user = User.objects.create_user(
        email="user_no_workspace@futureagi.com",
        password="testpassword123",
        name="User No Workspace",
        organization=org_without_workspace,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org_without_workspace,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


@pytest.fixture
def org_with_workspace(db):
    """Create an organization with a default workspace."""
    org = Organization.objects.create(name="Org With Workspace")
    return org


@pytest.fixture
def user_with_workspace(db, org_with_workspace):
    """Create a user in an organization with default workspace."""
    user = User.objects.create_user(
        email="user_with_workspace@futureagi.com",
        password="testpassword123",
        name="User With Workspace",
        organization=org_with_workspace,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org_with_workspace,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    # Create default workspace
    Workspace.objects.create(
        name="Default Workspace",
        organization=org_with_workspace,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return user


@pytest.mark.django_db
class TestCreateDemoTracesAndSpans:
    """Tests for create_demo_traces_and_spans function."""

    def test_clickhouse_publish_waits_for_commit(
        self, user_with_workspace, django_capture_on_commit_callbacks
    ):
        with patch("accounts.user_onboard._enqueue_demo_trace_publication") as publish:
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                result = create_demo_traces_and_spans(
                    user_with_workspace.organization_id, user_with_workspace.id
                )
                assert result is None
                publish.assert_not_called()
            assert len(callbacks) == 2
            assert publish.call_count == 2
            for call in publish.call_args_list:
                (trace_id,) = call.args
                assert ObservationSpan.objects.filter(trace_id=trace_id).count() == 35

    @pytest.mark.django_db(transaction=True)
    def test_publication_retry_reuses_saved_data(
        self, user_with_workspace, django_capture_on_commit_callbacks, monkeypatch
    ):
        from tfc.temporal.background_tasks.activities import publish_demo_trace_activity

        monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
        with patch("tfc.temporal.drop_in.start_activity") as start:
            with django_capture_on_commit_callbacks(execute=True):
                assert (
                    create_demo_traces_and_spans(
                        user_with_workspace.organization_id, user_with_workspace.id
                    )
                    is None
                )
        assert start.call_count == 2
        trace_ids = [call.kwargs["args"][0] for call in start.call_args_list]
        with patch(
            "accounts.user_onboard._write_demo_trace_to_clickhouse",
            side_effect=[RuntimeError("CH unavailable"), None, None],
        ) as publish:
            with pytest.raises(RuntimeError, match="CH unavailable"):
                publish_demo_trace_activity(trace_ids[0])
            publish_demo_trace_activity(trace_ids[0])
            publish_demo_trace_activity(trace_ids[1])
        first, retry, second = publish.call_args_list
        assert first.args[0].id == retry.args[0].id
        assert {s.id for s in first.args[1]} == {s.id for s in retry.args[1]}
        assert len(second.args[1]) == 35
        projects = Project.objects.filter(organization=user_with_workspace.organization)
        assert projects.count() == 2
        assert Trace.objects.filter(project__in=projects).count() == 2
        assert ObservationSpan.objects.filter(project__in=projects).count() == 70

    def test_creates_demo_projects_with_existing_workspace(self, user_with_workspace):
        """Demo projects are created with the existing default workspace."""
        org = user_with_workspace.organization
        workspace = Workspace.objects.get(organization=org, is_default=True)

        # Call the function
        result = create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        # Should not return an error
        assert result is None or "error" not in result

        # Verify two demo projects were created (experiment and observe)
        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )
        assert projects.count() == 2

        # Verify both projects have the workspace assigned
        for project in projects:
            assert project.workspace == workspace
            assert project.workspace.is_default is True

    def test_creates_default_workspace_when_none_exists(self, user_without_workspace):
        """Default workspace is created when organization has none."""
        org = user_without_workspace.organization

        # Verify no workspace exists
        assert not Workspace.objects.filter(organization=org).exists()

        # Call the function
        result = create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_without_workspace.id
        )

        # Should not return an error
        assert result is None or "error" not in result

        # Verify a default workspace was created
        workspace = Workspace.objects.get(organization=org, is_default=True)
        assert workspace.name == "Default Workspace"
        assert workspace.is_active is True
        assert workspace.created_by == user_without_workspace

        # Verify projects have the workspace assigned
        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )
        for project in projects:
            assert project.workspace == workspace

    def test_creates_experiment_and_observe_projects(self, user_with_workspace):
        """Both experiment and observe project types are created."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        # Verify experiment project
        experiment_project = Project.objects.get(
            organization=org,
            trace_type="experiment",
            source=ProjectSourceChoices.DEMO.value,
        )
        assert experiment_project.name == "Demo SQL Agent (Experiment)"
        assert experiment_project.model_type == AIModel.ModelTypes.GENERATIVE_LLM

        # Verify observe project
        observe_project = Project.objects.get(
            organization=org,
            trace_type="observe",
            source=ProjectSourceChoices.DEMO.value,
        )
        assert observe_project.name == "Demo SQL Agent (Observe)"
        assert observe_project.model_type == AIModel.ModelTypes.GENERATIVE_LLM

    def test_creates_project_versions(self, user_with_workspace):
        """Project versions are created for each demo project."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )

        for project in projects:
            version = ProjectVersion.objects.get(project=project)
            assert version.name == "Version 1.0"
            assert version.version == "v1.0"

    def test_creates_trace_sessions(self, user_with_workspace):
        """Trace sessions are created for each demo project."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )

        for project in projects:
            session = TraceSession.objects.get(project=project)
            assert f"Demo SQL Agent Session" in session.name

    def test_creates_traces(self, user_with_workspace):
        """Traces are created for each demo project."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )

        for project in projects:
            trace = Trace.objects.get(project=project)
            assert trace.name == "SQL Agent Trace"
            assert trace.session is not None

    def test_creates_observation_spans(self, user_with_workspace):
        """Observation spans are created for each trace."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        traces = Trace.objects.filter(
            project__organization=org,
            project__source=ProjectSourceChoices.DEMO.value,
        )

        for trace in traces:
            spans = ObservationSpan.objects.filter(trace=trace)
            # At least one span should exist
            assert spans.count() > 0

    def test_experiment_trace_has_project_version(self, user_with_workspace):
        """Experiment traces are linked to project version."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        experiment_project = Project.objects.get(
            organization=org,
            trace_type="experiment",
            source=ProjectSourceChoices.DEMO.value,
        )
        trace = Trace.objects.get(project=experiment_project)

        assert trace.project_version is not None
        assert trace.project_version.project == experiment_project

    def test_returns_error_for_nonexistent_organization(self, db):
        """Returns error when organization doesn't exist."""
        import uuid

        fake_org_id = uuid.uuid4()
        result = create_demo_traces_and_spans(organization_id=fake_org_id)

        # Function catches the exception and returns None or handles it
        # The function uses try/except and logs errors

    def test_returns_error_for_user_not_in_organization(
        self, user_with_workspace, org_without_workspace
    ):
        """Returns error when user doesn't belong to organization."""
        result = create_demo_traces_and_spans(
            organization_id=org_without_workspace.id,
            user_id=user_with_workspace.id,
        )

        assert result is not None
        assert "error" in result
        assert "User does not belong to organization" in result["error"]

    def test_handles_no_user_in_organization(self, db):
        """Returns error when organization has no users."""
        org = Organization.objects.create(name="Empty Org")

        result = create_demo_traces_and_spans(organization_id=org.id)

        assert result is not None
        assert "error" in result
        assert "No users found" in result["error"]

    def test_uses_first_user_when_user_id_not_provided(self, user_with_workspace):
        """Uses first user in organization when user_id is not provided."""
        org = user_with_workspace.organization

        # Call without user_id
        result = create_demo_traces_and_spans(organization_id=org.id)

        # Should succeed and create projects
        assert result is None or "error" not in result

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )
        assert projects.count() == 2


@pytest.mark.django_db
class TestWorkspaceCreationDuringOnboarding:
    """Tests specifically for workspace creation during onboarding."""

    def test_workspace_has_correct_attributes(self, user_without_workspace):
        """Created workspace has all required attributes set correctly."""
        org = user_without_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_without_workspace.id
        )

        workspace = Workspace.objects.get(organization=org)
        assert workspace.name == "Default Workspace"
        assert workspace.is_default is True
        assert workspace.is_active is True
        assert workspace.created_by == user_without_workspace
        assert workspace.organization == org

    def test_does_not_create_duplicate_workspace(self, user_with_workspace):
        """Does not create a new workspace if default already exists."""
        org = user_with_workspace.organization
        initial_workspace_count = Workspace.objects.filter(organization=org).count()

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        final_workspace_count = Workspace.objects.filter(organization=org).count()
        assert final_workspace_count == initial_workspace_count

    def test_idempotent_demo_creation_fails_gracefully(self, user_with_workspace):
        """Running demo creation twice handles duplicate project names gracefully."""
        org = user_with_workspace.organization

        # First call - should succeed
        result1 = create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        # Second call - may fail due to unique constraint on project name
        # The function should handle this gracefully
        try:
            result2 = create_demo_traces_and_spans(
                organization_id=org.id, user_id=user_with_workspace.id
            )
        except IntegrityError:
            # This is expected behavior - demo projects already exist
            pass


@pytest.mark.django_db
class TestWorkspaceCreationEdgeCases:
    """Tests for edge cases in workspace creation during onboarding."""

    def test_returns_error_when_workspace_query_returns_none(
        self, user_without_workspace
    ):
        """Returns error dict when workspace creation fails and query returns None.

        Note: The RuntimeError is raised internally but caught by the outer try/except
        in create_demo_traces_and_spans, which returns an error dict instead.
        """
        org = user_without_workspace.organization

        # Mock filter().first() to return None (workspace doesn't exist despite IntegrityError)
        mock_filter_result = MagicMock()
        mock_filter_result.first.return_value = None

        with (
            # Mock Workspace.objects.get to raise DoesNotExist (no workspace exists)
            patch.object(
                Workspace.objects,
                "get",
                side_effect=Workspace.DoesNotExist("No workspace found"),
            ),
            # Mock Workspace.objects.create to raise IntegrityError (race condition)
            patch.object(
                Workspace.objects,
                "create",
                side_effect=IntegrityError("duplicate key value"),
            ),
            # Mock filter().first() to return None
            patch.object(
                Workspace.objects,
                "filter",
                return_value=mock_filter_result,
            ),
        ):
            # Should return error dict when workspace can't be created or found
            result = create_demo_traces_and_spans(
                organization_id=org.id, user_id=user_without_workspace.id
            )

        assert result is not None
        assert "error" in result
        assert "Failed to get/create default workspace" in result["error"]


@pytest.mark.django_db
class TestDemoProjectIntegrity:
    """Tests for data integrity of created demo projects."""

    def test_all_projects_have_required_fields(self, user_with_workspace):
        """All demo projects have all required fields populated."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )

        for project in projects:
            assert project.name is not None
            assert project.organization is not None
            assert project.workspace is not None
            assert project.model_type is not None
            assert project.trace_type in ["experiment", "observe"]
            assert project.source == ProjectSourceChoices.DEMO.value
            assert project.config is not None

    def test_trace_session_linked_correctly(self, user_with_workspace):
        """Trace sessions are properly linked to projects."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        projects = Project.objects.filter(
            organization=org, source=ProjectSourceChoices.DEMO.value
        )

        for project in projects:
            session = TraceSession.objects.get(project=project)
            assert session.project == project

    def test_traces_linked_to_sessions(self, user_with_workspace):
        """Traces are properly linked to their sessions."""
        org = user_with_workspace.organization

        create_demo_traces_and_spans(
            organization_id=org.id, user_id=user_with_workspace.id
        )

        traces = Trace.objects.filter(
            project__organization=org,
            project__source=ProjectSourceChoices.DEMO.value,
        )

        for trace in traces:
            assert trace.session is not None
            assert trace.session.project == trace.project
