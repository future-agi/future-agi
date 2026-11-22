"""Scope validation is read-only and independent of worker readiness."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenOrigin, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.saml_credential import validate_saml_credential
from ai_tools.base import ToolContext
from ai_tools.tools.tracing.access import get_trace_project
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.ee_gating import EEFeature, FeatureUnavailable
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.services.falcon_execution_scope import (
    BackgroundScopeDenied,
    capture_background_execution_scope,
    revalidate_background_execution_scope,
)
from tracer.tests._falcon_analysis import install_reader


@pytest.fixture
def assignment(observe_project, user, monkeypatch):
    project = observe_project
    idp = SAMLMetadataModel.objects.create(
        id=str(uuid4()), organization=project.organization, is_enabled=True,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA, relay_state=str(uuid4()),
    )
    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS, auth_origin=AuthTokenOrigin.SAML,
        scoped_organization=project.organization, origin_idp=idp,
        origin_idp_generation=idp.security_generation, last_used_at=timezone.now(),
    )
    credential = validate_saml_credential(
        AuthToken.objects.values().get(pk=token.pk), str(user.pk),
        now=timezone.now(), transport="http",
    )
    foreground = ToolContext(user, project.organization, project.workspace, auth_scope=credential)
    attempt = TraceErrorAnalysis.objects.create(trace_id=uuid4(), project=project)
    reader = install_reader(monkeypatch, project, str(attempt.trace_id))
    entitlement = Mock()
    monkeypatch.setattr("tracer.services.falcon_execution_scope.check_ee_feature", entitlement)
    return foreground, attempt, reader, entitlement


def revalidate(scope, attempt, **overrides):
    job = {
        "project_id": str(attempt.project_id),
        "trace_id": str(attempt.trace_id),
        "analysis_attempt_id": attempt.pk,
    }
    return revalidate_background_execution_scope(scope, **(job | overrides))


@pytest.mark.django_db
def test_capture_revalidate_and_reconstruct_keep_exact_credential(assignment):
    foreground, attempt, reader, entitlement = assignment
    assert foreground.background_execution_scope is None
    scope = capture_background_execution_scope(foreground, attempt=attempt)
    with pytest.raises(FrozenInstanceError):
        scope.analysis_attempt_id = uuid4()
    ctx = revalidate(scope, attempt)
    assert (ctx.user.pk, ctx.organization.pk, ctx.workspace.pk) == (
        foreground.user.pk, foreground.organization.pk, foreground.workspace.pk,
    )
    assert ctx.project.pk == attempt.project_id
    assert ctx.assigned_trace_id == str(attempt.trace_id)
    assert ctx.background_execution_scope is scope
    assert ctx.auth_scope is scope.auth_scope is foreground.auth_scope
    assert entitlement.call_count == reader.root_ids_by_trace_ids.call_count == 2
    entitlement.assert_called_with(EEFeature.FALCON_AI, org_id=str(foreground.organization_id))
    reader.root_ids_by_trace_ids.assert_called_with([str(attempt.trace_id)], project_ids=[str(attempt.project_id)])

    # Use a current public field for replace; falcon_conversation_id is not on
    # this public branch. Ordinary init fields survive replace and reconstruction.
    copied = replace(ctx, project=attempt.project)
    rebuilt = ToolContext(**{f.name: getattr(copied, f.name) for f in fields(copied)})
    assert rebuilt.background_execution_scope is scope and rebuilt.auth_scope is ctx.auth_scope
    assert next(f for f in fields(ctx) if f.name == "background_execution_scope").kw_only
    with pytest.raises(TypeError):
        ToolContext(ctx.user, ctx.organization, ctx.workspace, ctx.project, ctx.assigned_trace_id, scope)
    foreground.project = attempt.project  # The public outer context is still mutable.


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_accounting_hold_also_denies_a_valid_scope(assignment, monkeypatch):
    from ee.usage.deployment import DeploymentMode
    from tracer.tasks.error_analysis import _falcon_analyze_single_trace

    foreground, attempt, _, _ = assignment
    scope = capture_background_execution_scope(foreground, attempt=attempt)
    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: False))
    forbidden = Mock(side_effect=AssertionError("Worker passed the accounting hold"))
    for path in (
        "tracer.services.falcon_execution_scope.revalidate_background_execution_scope",
        "ee.falcon_ai.agent.AgentLoop.__init__",
        "litellm.completion",
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        "tracer.tasks.error_analysis.cluster_project_errors.delay",
    ):
        monkeypatch.setattr(path, forbidden)
    result = _falcon_analyze_single_trace(
        str(attempt.trace_id),
        str(attempt.project_id),
        background_execution_scope=scope,
        analysis_attempt_id=attempt.pk,
    )
    assert result["success"] is False
    assert result["error_code"] == "FALCON_BACKGROUND_ACCOUNTING_UNAVAILABLE"
    forbidden.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    ["no_user", "ordinary_bearer", "unknown_scope", "user_binding", "org_binding",
     "project", "trace", "other_workspace", "nested"],
)
def test_capture_denies_missing_identity_or_outside_foreground(assignment, change):
    ctx, attempt, reader, _ = assignment
    if change == "no_user":
        ctx.user = None
    elif change == "ordinary_bearer":
        ctx.auth_scope = None
    elif change == "unknown_scope":
        ctx.auth_scope = {"org_id": str(ctx.organization_id)}
    elif change == "user_binding":
        ctx.auth_scope = replace(ctx.auth_scope, user_id=uuid4())
    elif change == "org_binding":
        ctx.auth_scope = replace(ctx.auth_scope, org_id=uuid4())
    elif change == "project":
        ctx.project = Project(id=uuid4())
    elif change == "trace":
        ctx.assigned_trace_id = str(uuid4())
    elif change == "other_workspace":
        other = Workspace.objects.create(
            organization=ctx.organization, name="Other workspace", created_by=ctx.user
        )
        # The org Owner could switch to it, but did not initiate this job there.
        Project.objects.filter(pk=attempt.project_id).update(workspace=other)
    else:
        ctx.background_execution_scope = capture_background_execution_scope(ctx, attempt=attempt)
        reader.root_ids_by_trace_ids.reset_mock()
    with pytest.raises(BackgroundScopeDenied):
        capture_background_execution_scope(ctx, attempt=attempt)
    reader.root_ids_by_trace_ids.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    ["token", "expired", "generation", "rebound_token", "user", "membership",
     "workspace", "viewer", "entitlement", "project", "trace", "attempt"],
)
def test_live_revocation_after_capture(assignment, change):
    ctx, attempt, reader, entitlement = assignment
    scope = capture_background_execution_scope(ctx, attempt=attempt)
    tokens = AuthToken.objects.filter(pk=scope.auth_scope.token_id)
    memberships = OrganizationMembership.no_workspace_objects.filter(
        user=ctx.user, organization=ctx.organization
    )
    if change == "token":
        tokens.update(is_active=False)
    elif change == "expired":
        tokens.update(last_used_at=timezone.now() - timedelta(days=10))
    elif change in ("generation", "rebound_token"):
        generation = scope.auth_scope.generation + 1
        SAMLMetadataModel.objects.filter(pk=scope.auth_scope.idp_id).update(
            security_generation=generation
        )
        if change == "rebound_token":
            # Live by itself, but no longer the credential captured at enqueue.
            tokens.update(origin_idp_generation=generation)
    elif change == "user":
        User.objects.filter(pk=ctx.user_id).update(is_active=False)
    elif change == "membership":
        memberships.update(is_active=False)
    elif change == "workspace":
        Workspace.objects.filter(pk=ctx.workspace_id).update(is_active=False)
    elif change == "viewer":
        memberships.update(role="Viewer", level=Level.VIEWER)
        WorkspaceMembership.no_workspace_objects.filter(
            user=ctx.user, workspace=ctx.workspace
        ).update(role="workspace_viewer", level=Level.WORKSPACE_VIEWER)
    elif change == "entitlement":
        entitlement.side_effect = FeatureUnavailable(EEFeature.FALCON_AI)
    elif change == "project":
        Project.objects.filter(pk=attempt.project_id).update(deleted=True)
    elif change == "trace":
        reader.root_ids_by_trace_ids.side_effect = None
        reader.root_ids_by_trace_ids.return_value = {str(attempt.trace_id): ("root", str(uuid4()))}
    else:
        attempt.delete()
    with pytest.raises(FeatureUnavailable if change == "entitlement" else BackgroundScopeDenied):
        revalidate(scope, attempt)


@pytest.mark.django_db
def test_no_widening_on_replace_or_worker_assignment_mismatch(assignment):
    ctx, attempt, reader, _ = assignment
    scope = capture_background_execution_scope(ctx, attempt=attempt)
    bound = revalidate(scope, attempt)
    reader.root_ids_by_trace_ids.reset_mock()
    for overrides in (
        {"project_id": str(uuid4())}, {"trace_id": str(uuid4())},
        {"analysis_attempt_id": uuid4()}, {"analysis_attempt_id": None},
    ):
        with pytest.raises(BackgroundScopeDenied, match="assignment mismatch"):
            revalidate(scope, attempt, **overrides)
    with pytest.raises(BackgroundScopeDenied, match="initiating user"):
        revalidate(None, attempt)
    for overrides in (
        {"project": None}, {"assigned_trace_id": None}, {"auth_scope": None},
        {"user": None}, {"workspace": None},
    ):
        assert get_trace_project(str(attempt.trace_id), replace(bound, **overrides)) is None
    reader.root_ids_by_trace_ids.assert_not_called()
