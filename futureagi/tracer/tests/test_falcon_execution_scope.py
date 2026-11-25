"""A legacy context can still be constructed; it cannot authorize background execution."""

from dataclasses import fields, replace
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
from ai_tools.base import BackgroundExecutionScope, ToolContext
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.tests._falcon_analysis import finding_params, install_reader, score_params
from tracer.tests._falcon_hold import assert_hold, assert_worker_refused


def make_credentialed_attempt(project, user):
    idp = SAMLMetadataModel.objects.create(
        id=str(uuid4()),
        organization=project.organization,
        is_enabled=True,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=str(uuid4()),
    )
    token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS,
        auth_origin=AuthTokenOrigin.SAML,
        scoped_organization=project.organization,
        origin_idp=idp,
        origin_idp_generation=idp.security_generation,
        last_used_at=timezone.now(),
    )
    credential = validate_saml_credential(
        AuthToken.objects.values().get(pk=token.pk),
        str(user.pk),
        now=timezone.now(),
        transport="http",
    )
    foreground = ToolContext(
        user, project.organization, project.workspace, auth_scope=credential
    )
    attempt = TraceErrorAnalysis.objects.create(trace_id=uuid4(), project=project)
    # Construct the old shape as input, not as proof of current authorization.
    scope = BackgroundExecutionScope(
        org_id=project.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.pk,
        assigned_trace_id=str(attempt.trace_id),
        initiating_user_id=user.pk,
        analysis_attempt_id=attempt.pk,
        auth_scope=credential,
    )
    return foreground, attempt, scope


@pytest.fixture
def assignment(observe_project, user):
    return make_credentialed_attempt(observe_project, user)


def bound_context(ctx, attempt, scope):
    return replace(
        ctx,
        project=attempt.project,
        assigned_trace_id=str(attempt.trace_id),
        background_execution_scope=scope,
    )


@pytest.mark.django_db
def test_legacy_context_construction_does_not_grant_execution(assignment):
    ctx, attempt, scope = assignment
    bound = bound_context(ctx, attempt, scope)
    copied = ToolContext(**{f.name: getattr(bound, f.name) for f in fields(bound)})
    assert (
        copied.auth_scope is scope.auth_scope
        and copied.background_execution_scope is scope
    )
    with pytest.raises(TypeError):
        ToolContext(
            ctx.user,
            ctx.organization,
            ctx.workspace,
            attempt.project,
            str(attempt.trace_id),
            scope,
        )
    from tracer.services.falcon_background_analysis import BackgroundAnalysisUnavailable

    with pytest.raises(BackgroundAnalysisUnavailable) as denied:
        copied.require_foreground_execution()
    assert_hold(denied.value.code, str(denied.value))


@pytest.mark.django_db
def test_accounting_hold_denies_legacy_scope(assignment):
    from tracer.tasks.error_analysis import _falcon_analyze_single_trace

    _, attempt, scope = assignment
    result = _falcon_analyze_single_trace(
        str(attempt.trace_id),
        str(attempt.project_id),
        background_execution_scope=scope,
        analysis_attempt_id=attempt.pk,
    )
    assert_worker_refused(result, str(attempt.trace_id))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        "no_user",
        "ordinary_bearer",
        "unknown_scope",
        "user_binding",
        "org_binding",
        "project",
        "trace",
        "assigned_without_project",
        "other_workspace",
        "nested",
        "api_key",
        "mcp",
    ],
)
def test_producer_refuses_regardless_of_legacy_initiating_context(assignment, change):
    ctx, attempt, scope = assignment
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
            token="test",
            client_id="test",
            scopes=["read"],
            user_id=str(ctx.user_id),
            organization_id=str(ctx.organization_id),
            workspace_id=str(ctx.workspace_id),
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
    elif change == "other_workspace":
        other = Workspace.objects.create(
            organization=ctx.organization, name="Other workspace", created_by=ctx.user
        )
        # The org Owner could switch to it, but did not initiate this job there.
        Project.objects.filter(pk=attempt.project_id).update(workspace=other)
    else:
        ctx = replace(ctx, background_execution_scope=scope)
    from ai_tools.registry import registry

    result = registry.get("analyze_project_traces").run(
        {"project_id": str(attempt.project_id)}, ctx
    )
    assert result.is_error and result.data is None
    assert_hold(result.error_code, result.content)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        "token",
        "non_saml_origin",
        "expired",
        "generation",
        "rebound_token",
        "user",
        "membership",
        "workspace",
        "viewer",
        "entitlement",
        "project",
        "trace",
        "attempt",
    ],
)
def test_legacy_context_refuses_after_live_state_changes(assignment, change):
    ctx, attempt, scope = assignment
    bound = bound_context(ctx, attempt, scope)
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
    elif change == "entitlement":
        from ee.usage.models.usage import PlanEntitlement

        PlanEntitlement.objects.update_or_create(
            organization=ctx.organization,
            feature="has_falcon_ai",
            plan="free",
            defaults={"value_bool": False},
        )
    elif change == "project":
        Project.objects.filter(pk=attempt.project_id).update(deleted=True)
    elif change == "trace":
        TraceErrorAnalysis.objects.filter(pk=attempt.pk).update(trace_id=uuid4())
    else:
        attempt.delete()
    from ai_tools.tools.tracing.submit_trace_scores import SubmitTraceScoresTool

    result = SubmitTraceScoresTool().run(score_params(scope.assigned_trace_id), bound)
    assert result.is_error and result.data is None
    assert_hold(result.error_code, result.content)


@pytest.mark.django_db
def test_legacy_mismatches_do_not_enable_execution(assignment):
    from ai_tools.registry import registry
    from tracer.tasks.error_analysis import _falcon_analyze_single_trace

    ctx, attempt, scope = assignment
    tid = str(attempt.trace_id)
    bound = bound_context(ctx, attempt, scope)
    for updates in (
        {"project": None},
        {"assigned_trace_id": None},
        {"auth_scope": None},
        {"user": None},
        {"workspace": None},
    ):
        denied = registry.get("submit_trace_scores").run(
            score_params(tid), replace(bound, **updates)
        )
        assert denied.is_error
        assert_hold(denied.error_code, denied.content)
    for pid, trace_id, aid in (
        (uuid4(), tid, attempt.pk),
        (attempt.project_id, str(uuid4()), attempt.pk),
        (attempt.project_id, tid, uuid4()),
        (attempt.project_id, tid, None),
    ):
        assert_worker_refused(
            _falcon_analyze_single_trace(
                trace_id,
                str(pid),
                background_execution_scope=scope,
                analysis_attempt_id=aid,
            ),
            trace_id,
        )


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
@pytest.mark.parametrize(
    "target", ["implicit", "explicit", "different", "deleted", "deleted_last"]
)
def test_background_tools_refuse_without_writing_even_to_captured_attempt(
    assignment, scoring_followups, target
):
    from ai_tools.tools.tracing.submit_trace_finding import SubmitTraceFindingTool
    from ai_tools.tools.tracing.submit_trace_scores import SubmitTraceScoresTool

    foreground, attempt, scope = assignment
    trace_id = str(attempt.trace_id)
    bound = bound_context(foreground, attempt, scope)
    rows = TraceErrorAnalysis.objects.filter(project=attempt.project, trace_id=trace_id)
    later = rows.create(
        project=attempt.project, trace_id=trace_id, insights="Leave alone"
    )
    later_before = rows.values().get(pk=later.pk)
    assert rows.latest("analysis_date").pk == later.pk

    args = {}
    if target == "explicit":
        args = {"analysis_id": str(attempt.pk)}
    elif target == "different":
        args = {"analysis_id": str(later.pk)}
    elif target in ("deleted", "deleted_last"):
        attempt.delete()  # Disappeared after capture: never use later or create.
        if target == "deleted_last":
            later.delete()
    before = list(rows.order_by("pk").values())
    for tool, params in (
        (SubmitTraceFindingTool(), finding_params(trace_id)),
        (SubmitTraceScoresTool(), score_params(trace_id)),
    ):
        raw = params | args
        for result in (
            tool.run(raw, bound),
            tool.execute(tool.input_model(**raw), bound),
        ):
            assert result.is_error
            assert_hold(result.error_code, result.content)
            assert result.data is None
            assert list(rows.order_by("pk").values()) == before
            assert not TraceErrorDetail.objects.filter(analysis__in=rows).exists()

    for call in scoring_followups:
        call.assert_not_called()
    if target != "deleted_last":
        assert rows.values().get(pk=later.pk) == later_before
    if target in ("implicit", "explicit"):
        assert set(rows.values_list("pk", flat=True)) == {later.pk, attempt.pk}
        attempt.refresh_from_db()
        assert attempt.total_errors == 0 and attempt.overall_score is None


@pytest.mark.django_db
def test_interactive_tools_keep_creation_and_latest_selection(
    trace, user, monkeypatch, scoring_followups
):
    from ai_tools.registry import registry

    project, tid = trace.project, str(trace.pk)
    ctx = ToolContext(user, project.organization, project.workspace)
    first = TraceErrorAnalysis.objects.create(project=project, trace=trace)
    reader = install_reader(monkeypatch, project, tid)
    read = registry.get("read_trace_span").run(
        {"trace_id": tid, "span_id": "shared-span"}, ctx
    )
    assert not read.is_error and "Own evidence" in read.content
    reader.list_by_trace.assert_called_once_with(tid)
    finding = registry.get("submit_trace_finding").run(finding_params(tid), ctx)
    assert not finding.is_error and finding.data["analysis_id"] != str(first.pk)
    scores = registry.get("submit_trace_scores").run(score_params(tid), ctx)
    assert (
        not scores.is_error
        and scores.data["analysis_id"] == finding.data["analysis_id"]
    )
    assert scores.data["clustering_triggered"] is True
    reader.root_ids_by_trace_ids.assert_called_once_with([tid])
    for call in scoring_followups[:2]:
        call.assert_called_once_with(tid)
    scoring_followups[2].assert_called_once_with(str(project.pk))
    first.refresh_from_db()
    assert first.total_errors == 0 and first.overall_score is None


@pytest.fixture
def falcon_plan_grant(assignment, monkeypatch):
    """Grant Falcon only to this test organization through the real plan resolver."""
    from ee.usage.models.usage import PlanEntitlement
    from ee.usage.services import entitlements, metering
    from tfc.capabilities import service
    from tfc.licensing.types import DeploymentFlavor, DeploymentLocation

    ctx, _, _ = assignment
    monkeypatch.setattr(service, "_configured", True)
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
    return PlanEntitlement.objects.create(
        feature="has_falcon_ai",
        organization=ctx.organization,
        plan="free",
        value_bool=True,
    )


@pytest.mark.requires_ee
@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("falcon_plan_grant")
@pytest.mark.parametrize("saml", [True, False], ids=["saml", "ordinary-bearer"])
def test_socket_constructs_unassigned_foreground_context(
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

    ctx, _, _ = assignment
    token_id = ctx.auth_scope.token_id
    if not saml:
        token_id = AuthToken.objects.create(
            user=ctx.user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
        ).pk
    token = generate_encrypted_message(
        {"user_id": str(ctx.user_id), "id": str(token_id)}
    )
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
            await socket.send_json_to(
                {
                    "type": "chat",
                    "message": "Check this trace",
                    "conversation_id": str(conversation.pk),
                    # Client context is not transport authority, even for the same user.
                    "context": {
                        "auth_scope": {"token_id": str(ctx.auth_scope.token_id)}
                    },
                }
            )
            frame = await socket.receive_json_from(timeout=5)
            assert frame["type"] == "error" and frame["data"]["error"] == "test stop"
        finally:
            await socket.disconnect()

    async_to_sync(chat)()
    agent.assert_called_once()
    agent.return_value.run.assert_not_called()
    delivered = agent.call_args.kwargs["tool_context"]
    assert len(socket_scopes) == 1
    if saml:
        assert isinstance(socket_scopes[0], SamlCredential)
        assert socket_scopes[0].token_id == token_id
    else:
        assert socket_scopes[0] is None
    assert (
        delivered.user_id == ctx.user_id and delivered.workspace_id == ctx.workspace_id
    )
    assert delivered.auth_scope is None and delivered.background_execution_scope is None
    assert delivered.assigned_trace_id is None


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_paid_override_revocation_does_not_admit_background_context(
    assignment, falcon_plan_grant
):
    from ai_tools.registry import registry
    from ee.usage.services import entitlements
    from tfc.capabilities import service

    ctx, attempt, scope = assignment
    grant = falcon_plan_grant
    assert service.check("falcon_ai", org_id=str(ctx.organization_id)).allowed
    grant.value_bool = False
    grant.save(update_fields=["value_bool"])
    entitlements.Entitlements.invalidate_cache(str(ctx.organization_id), grant.feature)
    assert not service.check("falcon_ai", org_id=str(ctx.organization_id)).allowed
    denied = registry.get("submit_trace_scores").run(
        score_params(str(attempt.trace_id)), bound_context(ctx, attempt, scope)
    )
    assert denied.is_error
    assert_hold(denied.error_code, denied.content)


@pytest.mark.django_db
def test_child_only_trace_does_not_enable_foreground_score(
    observe_project, user, scoring_followups
):
    from ai_tools.tools.tracing.submit_trace_scores import SubmitTraceScoresTool
    from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans

    tid, sid = str(uuid4()), str(uuid4())
    project = observe_project
    ctx = ToolContext(user, project.organization, project.workspace)
    row = TraceErrorAnalysis.objects.create(project=project, trace_id=tid)
    try:
        assert (
            seed_ch_spans(
                [
                    {
                        "id": sid,
                        "trace_id": tid,
                        "parent_span_id": str(uuid4()),
                        "project_id": str(project.pk),
                        "name": "child",
                        "observation_type": "llm",
                        "start_time": timezone.now(),
                    }
                ]
            )
            == 1
        )
        result = SubmitTraceScoresTool().run(
            score_params(tid) | {"analysis_id": str(row.pk)}, ctx
        )
        assert result.is_error and result.error_code == "NOT_FOUND"
        row.refresh_from_db()
        assert row.overall_score is None
        for call in scoring_followups:
            call.assert_not_called()
    finally:
        delete_ch_spans([sid])


@pytest.mark.django_db
def test_viewer_missing_pg_trace_returns_dev_not_found(assignment, scoring_followups):
    from ai_tools.registry import registry

    ctx, attempt, _ = assignment
    OrganizationMembership.no_workspace_objects.filter(
        user=ctx.user, organization=ctx.organization
    ).update(role="Viewer", level=Level.VIEWER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=ctx.user, workspace=ctx.workspace
    ).update(role="workspace_viewer", level=Level.WORKSPACE_VIEWER)
    assert ctx.user.can_access_workspace(
        ctx.workspace
    ) and not ctx.user.can_write_to_workspace(ctx.workspace)
    tid = str(attempt.trace_id)
    for name, params in (
        ("submit_trace_finding", finding_params(tid)),
        ("read_trace_span", {"trace_id": tid, "span_id": "absent"}),
    ):
        result = registry.get(name).run(params, ctx)
        assert result.is_error and result.error_code == "NOT_FOUND"
    assert not TraceErrorDetail.objects.filter(analysis=attempt).exists()
    for call in scoring_followups:
        call.assert_not_called()
