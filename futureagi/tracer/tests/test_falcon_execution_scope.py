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
from tfc.capabilities import service
from tfc.capabilities.errors import CapabilityDenied
from tfc.constants.levels import Level
from tfc.ee_gating import EEFeature
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
)
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.services.falcon_execution_scope import (
    BackgroundScopeDenied,
    capture_background_execution_scope,
    revalidate_background_execution_scope,
)
from tracer.tests._falcon_analysis import finding_params, install_reader, score_params


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
    # Supply only license data. The foreground Falcon gate, registry and
    # capability service evaluate the snapshot on every capture and recheck.
    resolver = Mock(spec=service.LicenseResolver)
    resolver.get_snapshot.return_value = LicenseSnapshot(
        state=LicenseState.ACTIVE, features=frozenset({EEFeature.FALCON_AI.value})
    )
    monkeypatch.setattr(service, "_configured", True)
    monkeypatch.setattr(service, "_deployment_location", DeploymentLocation.SELF_HOSTED)
    monkeypatch.setattr(service, "_deployment_flavor", DeploymentFlavor.SELF_HOSTED_EE)
    monkeypatch.setattr(service, "_license_resolver", resolver)
    return foreground, attempt, reader, resolver


def revalidate(scope, attempt, **overrides):
    job = {
        "project_id": str(attempt.project_id),
        "trace_id": str(attempt.trace_id),
        "analysis_attempt_id": attempt.pk,
    }
    return revalidate_background_execution_scope(scope, **(job | overrides))


@pytest.mark.django_db
def test_capture_revalidate_and_reconstruct_keep_exact_credential(assignment):
    foreground, attempt, reader, resolver = assignment
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
    assert resolver.get_snapshot.call_count == reader.root_ids_by_trace_ids.call_count == 2
    reader.root_ids_by_trace_ids.assert_called_with([str(attempt.trace_id)], project_ids=[str(attempt.project_id)])

    # Use a current public field for replace; falcon_conversation_id is not on
    # this public branch. Ordinary init fields survive replace and reconstruction.
    copied = replace(ctx, project=attempt.project)
    rebuilt = ToolContext(**{f.name: getattr(copied, f.name) for f in fields(copied)})
    assert rebuilt.background_execution_scope is scope and rebuilt.auth_scope is ctx.auth_scope
    assert next(f for f in fields(ctx) if f.name == "background_execution_scope").kw_only
    with pytest.raises(TypeError):
        ToolContext(ctx.user, ctx.organization, ctx.workspace, ctx.project, ctx.assigned_trace_id, scope)


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
     "project", "trace", "assigned_without_project", "other_workspace", "nested", "api_key", "mcp"],
)
def test_capture_denies_missing_identity_or_outside_foreground(assignment, change):
    ctx, attempt, reader, _ = assignment
    if change == "no_user":
        ctx = replace(ctx, user=None)
    elif change == "ordinary_bearer":
        ctx = replace(ctx, auth_scope=None)
    elif change == "unknown_scope":
        ctx = replace(ctx, auth_scope={"org_id": str(ctx.organization_id)})
    elif change == "api_key":
        from accounts.models.user import OrgApiKey

        ctx = replace(ctx, auth_scope=OrgApiKey(organization=ctx.organization))
    elif change == "mcp":
        from mcp_server.oauth_provider import FutureAGIAccessToken

        token = FutureAGIAccessToken(
            token="test", client_id="test", scopes=["read"], user_id=str(ctx.user_id),
            organization_id=str(ctx.organization_id), workspace_id=str(ctx.workspace_id),
            expires_at=int((timezone.now() + timedelta(minutes=10)).timestamp()),
        )
        ctx = replace(ctx, auth_scope=token)
    elif change == "user_binding":
        ctx = replace(ctx, auth_scope=replace(ctx.auth_scope, user_id=uuid4()))
    elif change == "org_binding":
        ctx = replace(ctx, auth_scope=replace(ctx.auth_scope, org_id=uuid4()))
    elif change == "project":
        ctx = replace(ctx, project=Project(id=uuid4()))
    elif change == "trace":
        ctx = replace(ctx, project=attempt.project, assigned_trace_id=str(uuid4()))
    elif change == "assigned_without_project":
        ctx = replace(ctx, assigned_trace_id=str(attempt.trace_id))
        assert ctx.project is None
        assert get_trace_project(attempt.trace_id, ctx) is None
    elif change == "other_workspace":
        other = Workspace.objects.create(
            organization=ctx.organization, name="Other workspace", created_by=ctx.user
        )
        # The org Owner could switch to it, but did not initiate this job there.
        Project.objects.filter(pk=attempt.project_id).update(workspace=other)
    else:
        nested = capture_background_execution_scope(ctx, attempt=attempt)
        ctx = replace(ctx, background_execution_scope=nested)
        reader.root_ids_by_trace_ids.reset_mock()
    with pytest.raises(BackgroundScopeDenied):
        capture_background_execution_scope(ctx, attempt=attempt)
    reader.root_ids_by_trace_ids.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    ["token", "non_saml_origin", "expired", "generation", "rebound_token", "user", "membership",
     "workspace", "viewer", "entitlement", "project", "trace", "attempt"],
)
def test_live_revocation_after_capture(assignment, change):
    ctx, attempt, reader, resolver = assignment
    scope = capture_background_execution_scope(ctx, attempt=attempt)
    tokens = AuthToken.objects.filter(pk=scope.auth_scope.token_id)
    memberships = OrganizationMembership.no_workspace_objects.filter(
        user=ctx.user, organization=ctx.organization
    )
    if change == "token":
        tokens.update(is_active=False)
    elif change == "non_saml_origin":
        tokens.update(auth_origin=None)
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
        assert ctx.user.can_access_workspace(ctx.workspace)
        assert not ctx.user.can_write_to_workspace(ctx.workspace)
        with pytest.raises(BackgroundScopeDenied, match="workspace write access"):
            capture_background_execution_scope(ctx, attempt=attempt)
    elif change == "entitlement":
        resolver.get_snapshot.return_value = LicenseSnapshot(state=LicenseState.ACTIVE)
        with pytest.raises(CapabilityDenied) as denied:
            revalidate(scope, attempt)
        assert denied.value.feature == EEFeature.FALCON_AI.value
        assert denied.value.reason_code == DenialReason.LICENSE_FEATURE_MISSING.value
        return
    elif change == "project":
        Project.objects.filter(pk=attempt.project_id).update(deleted=True)
    elif change == "trace":
        reader.root_ids_by_trace_ids.side_effect = None
        reader.root_ids_by_trace_ids.return_value = {str(attempt.trace_id): ("root", str(uuid4()))}
    else:
        attempt.delete()
    with pytest.raises(BackgroundScopeDenied):
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


@pytest.fixture
def scoring_followups(monkeypatch):
    calls = []
    for path in (
        "tracer.queries.deep_analysis_state.clear",
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        "tracer.tasks.error_analysis.cluster_project_errors.delay",
    ):
        call = Mock()
        monkeypatch.setattr(path, call)
        calls.append(call)
    return calls


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["implicit", "explicit", "different", "deleted", "deleted_last"])
def test_background_tools_write_only_to_captured_attempt(assignment, scoring_followups, target):
    from ai_tools.tools.tracing.submit_trace_finding import SubmitTraceFindingTool
    from ai_tools.tools.tracing.submit_trace_scores import SubmitTraceScoresTool

    foreground, attempt, reader, _ = assignment
    trace_id = str(attempt.trace_id)
    foreground = replace(foreground, project=attempt.project, assigned_trace_id=trace_id)
    scope = capture_background_execution_scope(foreground, attempt=attempt)
    bound = revalidate(scope, attempt)
    reader.trace_ids_for_span_ids.return_value = {"shared-span": trace_id}
    rows = TraceErrorAnalysis.objects.filter(project=attempt.project, trace_id=trace_id)
    later = rows.create(project=attempt.project, trace_id=trace_id, insights="Leave alone")
    later_before = rows.values().get(pk=later.pk)
    assert rows.latest("analysis_date").pk == later.pk

    args = {}
    if target == "explicit":
        args = {"analysis_id": str(attempt.pk)}
    elif target == "different":
        args = {"analysis_id": str(later.pk)}
    elif target in ("deleted", "deleted_last"):
        attempt.delete()  # Disappeared after revalidation: never use later or create.
        if target == "deleted_last":
            later.delete()
    before = list(rows.order_by("pk").values())
    for tool, params in (
        (SubmitTraceFindingTool(), finding_params(trace_id)),
        (SubmitTraceScoresTool(), score_params(trace_id)),
    ):
        result = tool.run(params | args, bound)
        if target in ("different", "deleted", "deleted_last"):
            assert result.is_error and result.error_code == "NOT_FOUND"
            assert list(rows.order_by("pk").values()) == before
            assert not TraceErrorDetail.objects.filter(
                analysis_id__in=[attempt.pk, later.pk]
            ).exists()
        else:
            assert not result.is_error, result.content
            assert result.data["analysis_id"] == str(attempt.pk)

    for call in scoring_followups:
        call.assert_not_called()
    if target != "deleted_last":
        assert rows.values().get(pk=later.pk) == later_before
    if target in ("implicit", "explicit"):
        assert set(rows.values_list("pk", flat=True)) == {later.pk, attempt.pk}
        attempt.refresh_from_db()
        assert attempt.total_errors == 1 and attempt.overall_score == 2.0
        assert TraceErrorDetail.objects.get(analysis=attempt).description == "Own finding"


@pytest.mark.django_db
def test_interactive_tools_keep_creation_and_latest_selection(assignment, scoring_followups):
    from ai_tools.tools.tracing.submit_trace_finding import SubmitTraceFindingTool
    from ai_tools.tools.tracing.submit_trace_scores import SubmitTraceScoresTool

    ctx, attempt, _, _ = assignment
    trace_id = str(attempt.trace_id)
    finding = SubmitTraceFindingTool().run(finding_params(trace_id), ctx)
    assert not finding.is_error, finding.content
    assert finding.data["analysis_id"] != str(attempt.pk)
    scores = SubmitTraceScoresTool().run(score_params(trace_id), ctx)
    assert not scores.is_error, scores.content
    assert scores.data["analysis_id"] == finding.data["analysis_id"]
    assert scores.data["clustering_triggered"] is True
    for call in scoring_followups:
        call.assert_called_once()
    attempt.refresh_from_db()
    assert attempt.total_errors == 0 and attempt.overall_score is None


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_revoked_org_falcon_entitlement_denies_live_initiator(assignment, monkeypatch):
    from ee.usage.models.usage import PlanEntitlement
    from ee.usage.services import entitlements, metering

    ctx, attempt, reader, _ = assignment
    monkeypatch.setattr(service, "_deployment_location", DeploymentLocation.CLOUD)
    monkeypatch.setattr(service, "_deployment_flavor", DeploymentFlavor.CLOUD)
    monkeypatch.setattr(
        service, "_cloud_plan_resolver", entitlements.get_cloud_plan_resolver()
    )
    # Exercise normal cached plan reads and explicit override invalidation
    # without an external Redis or a deployment-specific upsell catalog.
    cached = {}
    cache = Mock(spec=["get", "setex", "delete"])
    cache.get.side_effect = cached.get
    cache.setex.side_effect = lambda key, ttl, value: cached.update({key: value})
    cache.delete.side_effect = lambda key: cached.pop(key, None)
    monkeypatch.setattr(
        entitlements.BillingConfig, "_instance", entitlements.BillingConfig._empty()
    )
    monkeypatch.setattr(entitlements, "get_redis", lambda: cache)
    monkeypatch.setattr(metering, "get_redis", lambda: cache)
    grant = PlanEntitlement.objects.create(
        feature="has_falcon_ai", organization=ctx.organization, plan="free", value_bool=True,
    )
    scope = capture_background_execution_scope(ctx, attempt=attempt)
    assert revalidate(scope, attempt).auth_scope is ctx.auth_scope
    grant.value_bool = False
    grant.save(update_fields=["value_bool"])
    entitlements.Entitlements.invalidate_cache(str(ctx.organization_id), grant.feature)
    reader.root_ids_by_trace_ids.reset_mock()
    with pytest.raises(CapabilityDenied) as denied:
        revalidate(scope, attempt)
    assert denied.value.feature == EEFeature.FALCON_AI.value
    assert denied.value.reason_code == DenialReason.PLAN_FEATURE_MISSING.value
    reader.root_ids_by_trace_ids.assert_not_called()


@pytest.mark.requires_ee
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("saml", [True, False], ids=["saml", "ordinary-bearer"])
def test_socket_passes_only_middleware_authority_to_agent(
    assignment, monkeypatch, settings, saml
):
    from types import SimpleNamespace
    from urllib.parse import urlencode

    from asgiref.sync import async_to_sync

    from accounts.authentication import generate_encrypted_message
    from accounts.saml_credential import SamlCredential
    from ee.falcon_ai.consumers import FalconAIConsumer
    from ee.falcon_ai.models import Conversation
    from saml2_auth.tests.ws_helpers import WebsocketCommunicator
    from tfc.middleware.jwt_auth import JWTAuthMiddleware

    ctx, attempt, _, _ = assignment
    token_id = ctx.auth_scope.token_id
    if not saml:
        token_id = AuthToken.objects.create(
            user=ctx.user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
        ).pk
    token = generate_encrypted_message({"user_id": str(ctx.user_id), "id": str(token_id)})
    query = urlencode({"token": token, "workspace_id": str(ctx.workspace_id)})
    conversation = Conversation.objects.create(
        organization=ctx.organization, workspace=ctx.workspace, user=ctx.user
    )
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    # Exercise connect, SAML liveness, chat and _run_agent without dispatching a model.
    agent = Mock()
    monkeypatch.setattr("ee.falcon_ai.consumers.AgentLoop", agent)
    monkeypatch.setattr("ee.falcon_ai.consumers.StreamBuffer", Mock())
    monkeypatch.setattr(
        "ee.falcon_ai.consumers.check_usage",
        lambda *_: SimpleNamespace(allowed=False, reason="test stop"),
    )
    socket_scopes = []

    async def observe_authenticated_socket(scope, receive, send):
        socket_scopes.append(scope["auth_scope"])
        await FalconAIConsumer.as_asgi()(scope, receive, send)

    async def chat():
        socket = WebsocketCommunicator(
            JWTAuthMiddleware(observe_authenticated_socket), f"/ws/falcon-ai/?{query}"
        )
        try:
            connected, _ = await socket.connect(timeout=5)
            assert connected
            await socket.send_json_to({
                "type": "chat", "message": "Check this trace", "conversation_id": str(conversation.pk),
                # Client context is not transport authority, even for the same user.
                "context": {"auth_scope": {"token_id": str(ctx.auth_scope.token_id)}},
            })
            frame = await socket.receive_json_from(timeout=5)
            assert frame["type"] == "error" and frame["data"]["error"] == "test stop"
        finally:
            await socket.disconnect()

    async_to_sync(chat)()
    agent.assert_called_once()
    agent.return_value.run.assert_not_called()
    delivered = agent.call_args.kwargs["tool_context"]
    assert len(socket_scopes) == 1 and delivered.auth_scope is socket_scopes[0]
    assert delivered.background_execution_scope is None
    if saml:
        assert isinstance(delivered.auth_scope, SamlCredential)
        assert delivered.auth_scope.token_id == token_id
        scope = capture_background_execution_scope(delivered, attempt=attempt)
        assert scope.auth_scope is delivered.auth_scope
    else:
        assert delivered.auth_scope is None
        with pytest.raises(BackgroundScopeDenied, match="credential"):
            capture_background_execution_scope(delivered, attempt=attempt)
