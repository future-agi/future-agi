"""Phase 3B negative-test matrix: authorization on the live bridge path.

Two layers:

1. Unit tests for ``ai_tools.authz.enforce_view_permissions`` — the
   mechanism (authenticated-user floor, permission evaluation, clean
   PERMISSION_DENIED shape, fail-closed on crashing permission classes).
2. DB-backed cross-tenant matrix — representative read / write / destructive
   bridge tools called with cross-workspace, cross-org, and anonymous
   contexts. The acceptance bar is the DB side effect: a denied call must
   leave the target row untouched.

Destructive assertions accept ``CONFIRMATION_REQUIRED`` alongside
``NOT_FOUND``/``PERMISSION_DENIED``: Phase 3A's confirmation gate (in
``BaseTool.run``) may intercept destructive tools BEFORE the bridge authz
check. Either way the invariant under test holds — no side effect and an
error/blocked result for a cross-tenant caller.

Run in-container (DB-backed)::

    docker exec ws1-backend bash -lc "cd /app/backend && \
      PG_HOST=host.docker.internal PG_PORT=15432 PG_USER=test_user \
      PG_PASSWORD=test_password PG_DB=test_tfc \
      REDIS_URL=redis://host.docker.internal:16379/0 \
      pytest ai_tools/tests/test_authz_negative.py -x -q"
"""

import uuid

import pytest
from django.contrib.auth.models import AnonymousUser

import ai_tools.authz as authz_module
from ai_tools.authz import (
    AUTHZ_EXEMPT,
    enforce_view_permissions,
    permission_denied_result,
)
from ai_tools.base import ToolContext
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_dataset, make_prompt_template

# Error codes acceptable for a cross-tenant call against a tool whose
# execution must not proceed. NOT_FOUND is the expected outcome for
# queryset-scoped views (the row is invisible); PERMISSION_DENIED for
# permission-class denials (including the bridge authentication floor).
DENIED_CODES = {"NOT_FOUND", "PERMISSION_DENIED"}
# Destructive tools may additionally be intercepted by the Phase 3A
# confirmation gate before authz runs (preview, zero side effects).
DESTRUCTIVE_BLOCKED_CODES = DENIED_CODES | {"CONFIRMATION_REQUIRED"}


# ---------------------------------------------------------------------------
# Layer 1 — unit tests for the enforcement mechanism (no DB)
# ---------------------------------------------------------------------------


class _AllowPerm:
    def has_permission(self, request, view):
        return True


class _DenyPerm:
    message = "Organization admin access required."

    def has_permission(self, request, view):
        return False


class _CrashingPerm:
    def has_permission(self, request, view):
        raise AttributeError("synthetic request lacks attribute X")


class _FakeView:
    def __init__(self, permissions):
        self._permissions = permissions

    def get_permissions(self):
        return self._permissions


class _FakeUser:
    is_authenticated = True
    id = 42


class _FakeRequest:
    def __init__(self, user):
        self.user = user


class TestEnforceViewPermissions:
    def test_authorized_returns_none(self):
        view = _FakeView([_AllowPerm()])
        assert enforce_view_permissions(view, _FakeRequest(_FakeUser()), "t") is None

    def test_deny_returns_clean_permission_denied(self):
        view = _FakeView([_AllowPerm(), _DenyPerm()])
        result = enforce_view_permissions(view, _FakeRequest(_FakeUser()), "list_members")
        assert result is not None
        assert result.is_error is True
        assert result.error_code == "PERMISSION_DENIED"
        assert result.data["denied_by"] == "_DenyPerm"
        assert result.data["tool"] == "list_members"
        # Clean LLM-facing shape: names the tool, carries the permission's
        # message, states that nothing happened — no tracebacks.
        assert "list_members" in result.content
        assert "Organization admin access required." in result.content
        assert "No action was taken." in result.content
        assert "Traceback" not in result.content

    def test_crashing_permission_fails_closed(self):
        view = _FakeView([_CrashingPerm()])
        result = enforce_view_permissions(view, _FakeRequest(_FakeUser()), "t")
        assert result is not None
        assert result.error_code == "PERMISSION_DENIED"
        assert result.data["denied_by"] == "_CrashingPerm"

    def test_get_permissions_crash_fails_closed(self):
        class _BrokenView:
            def get_permissions(self):
                raise RuntimeError("boom")

        result = enforce_view_permissions(_BrokenView(), _FakeRequest(_FakeUser()), "t")
        assert result is not None
        assert result.error_code == "PERMISSION_DENIED"
        assert result.data["denied_by"] == "get_permissions_error"

    def test_anonymous_user_denied_before_permissions(self):
        # Even with an allow-all view (project default is AllowAny — no
        # DEFAULT_PERMISSION_CLASSES), the bridge floor requires an
        # authenticated user.
        view = _FakeView([_AllowPerm()])
        result = enforce_view_permissions(view, _FakeRequest(AnonymousUser()), "t")
        assert result is not None
        assert result.error_code == "PERMISSION_DENIED"
        assert result.data["denied_by"] == "bridge_authentication_floor"

    def test_missing_user_denied(self):
        view = _FakeView([_AllowPerm()])
        result = enforce_view_permissions(view, _FakeRequest(None), "t")
        assert result is not None
        assert result.error_code == "PERMISSION_DENIED"

    def test_permission_denied_result_shape(self):
        result = permission_denied_result("delete_dataset", "Nope.", denied_by="X")
        assert result.is_error is True
        assert result.error_code == "PERMISSION_DENIED"
        assert result.data == {
            "tool": "delete_dataset",
            "denied_by": "X",
            "reason": "Nope.",
        }

    def test_authz_exempt_ships_empty(self):
        # Design §2.1: the escape hatch ships EMPTY; any future entry is a
        # deliberate reviewed act with a justification comment. This test is
        # the tripwire.
        assert AUTHZ_EXEMPT == set()

    def test_exempt_tool_skips_enforcement(self, monkeypatch):
        monkeypatch.setattr(authz_module, "AUTHZ_EXEMPT", {"justified_tool"})
        view = _FakeView([_DenyPerm()])
        # Exempt name bypasses evaluation entirely (even the deny perm)…
        assert (
            enforce_view_permissions(view, _FakeRequest(_FakeUser()), "justified_tool")
            is None
        )
        # …while every other tool still goes through it.
        denied = enforce_view_permissions(view, _FakeRequest(_FakeUser()), "other_tool")
        assert denied is not None and denied.error_code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Layer 2 — DB-backed cross-tenant matrix on live bridge tools
# ---------------------------------------------------------------------------


@pytest.fixture
def other_workspace(db, user):
    """A second, NON-default workspace in the caller's own org.

    Non-default matters: default-workspace scoping deliberately includes
    sibling default workspaces and workspace-null rows, so only a
    non-default workspace isolates cross-workspace access.
    """
    from accounts.models.workspace import Workspace

    return Workspace.objects.create(
        name="Other Workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def other_workspace_context(user, other_workspace):
    """Caller context bound to the same user/org but the OTHER workspace —
    objects created with this context land in ``other_workspace``."""
    return ToolContext(
        user=user, organization=user.organization, workspace=other_workspace
    )


@pytest.fixture
def other_org_context(db):
    """A fully separate tenant: org2 / user2 / default workspace2."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    org2 = Organization.objects.create(name="Other Organization")
    user2 = User.objects.create_user(
        email=f"intruder-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other Org User",
        organization=org2,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user2,
        organization=org2,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    ws2 = Workspace.objects.create(
        name="Other Org Workspace",
        organization=org2,
        is_default=True,
        is_active=True,
        created_by=user2,
    )
    return ToolContext(user=user2, organization=org2, workspace=ws2)


@pytest.fixture
def anonymous_context(tool_context):
    """Anonymous caller against a real workspace — must hit the bridge
    authentication floor regardless of view permission classes."""
    return ToolContext(
        user=AnonymousUser(),
        organization=tool_context.organization,
        workspace=tool_context.workspace,
    )


def _assert_denied(result, allowed_codes=DENIED_CODES):
    assert result.error_code in allowed_codes, (
        f"expected one of {sorted(allowed_codes)}, got "
        f"error_code={result.error_code!r} is_error={result.is_error} "
        f"content={result.content[:300]!r}"
    )
    if result.error_code != "CONFIRMATION_REQUIRED":
        assert result.is_error is True


@pytest.mark.django_db
class TestReadToolMatrix:
    """get_prompt_template / get_dataset — execution_policy: read."""

    def test_same_workspace_read_succeeds(self, tool_context):
        tpl = make_prompt_template(tool_context, name="own-template")
        result = run_tool("get_prompt_template", {"id": str(tpl.id)}, tool_context)
        assert not result.is_error, result.content[:300]

    def test_cross_workspace_read_denied(self, tool_context, other_workspace_context):
        tpl = make_prompt_template(other_workspace_context, name="ws-b-template")
        result = run_tool("get_prompt_template", {"id": str(tpl.id)}, tool_context)
        _assert_denied(result)

    def test_cross_org_read_denied(self, tool_context, other_org_context):
        tpl = make_prompt_template(tool_context, name="org-1-template")
        result = run_tool("get_prompt_template", {"id": str(tpl.id)}, other_org_context)
        _assert_denied(result)

    def test_anonymous_read_denied(self, tool_context, anonymous_context):
        tpl = make_prompt_template(tool_context, name="anon-target")
        result = run_tool("get_prompt_template", {"id": str(tpl.id)}, anonymous_context)
        assert result.error_code == "PERMISSION_DENIED"
        assert result.is_error is True

    def test_cross_workspace_dataset_read_denied(
        self, tool_context, other_workspace_context
    ):
        ds = make_dataset(other_workspace_context, name="ws-b-dataset")
        result = run_tool("get_dataset", {"id": str(ds.id)}, tool_context)
        _assert_denied(result)


@pytest.mark.django_db
class TestWriteToolMatrix:
    """update_prompt_template — execution_policy: mutate."""

    def test_cross_workspace_write_denied_and_unchanged(
        self, tool_context, other_workspace_context
    ):
        from model_hub.models.run_prompt import PromptTemplate

        tpl = make_prompt_template(other_workspace_context, name="keep-name")
        result = run_tool(
            "update_prompt_template",
            {"id": str(tpl.id), "name": "hijacked"},
            tool_context,
        )
        _assert_denied(result)
        assert PromptTemplate.all_objects.get(id=tpl.id).name == "keep-name"

    def test_cross_org_write_denied_and_unchanged(self, tool_context, other_org_context):
        from model_hub.models.run_prompt import PromptTemplate

        tpl = make_prompt_template(tool_context, name="keep-name")
        result = run_tool(
            "update_prompt_template",
            {"id": str(tpl.id), "name": "hijacked"},
            other_org_context,
        )
        _assert_denied(result)
        assert PromptTemplate.all_objects.get(id=tpl.id).name == "keep-name"

    def test_anonymous_write_denied_and_unchanged(self, tool_context, anonymous_context):
        from model_hub.models.run_prompt import PromptTemplate

        tpl = make_prompt_template(tool_context, name="keep-name")
        result = run_tool(
            "update_prompt_template",
            {"id": str(tpl.id), "name": "hijacked"},
            anonymous_context,
        )
        assert result.error_code == "PERMISSION_DENIED"
        assert PromptTemplate.all_objects.get(id=tpl.id).name == "keep-name"


@pytest.mark.django_db
class TestDestructiveToolMatrix:
    """delete_prompt_template / delete_dataset — execution_policy: destructive.

    The DB side effect is the acceptance bar: a cross-tenant or anonymous
    caller must never delete (or soft-delete) the row.
    """

    @staticmethod
    def _template_intact(tpl_id):
        from model_hub.models.run_prompt import PromptTemplate

        tpl = PromptTemplate.all_objects.filter(id=tpl_id).first()
        return tpl is not None and not tpl.deleted

    @staticmethod
    def _dataset_intact(ds_id):
        from model_hub.models.develop_dataset import Dataset

        ds = Dataset.all_objects.filter(id=ds_id).first()
        return ds is not None and not ds.deleted

    def test_cross_workspace_destroy_not_executed(
        self, tool_context, other_workspace_context
    ):
        tpl = make_prompt_template(other_workspace_context, name="ws-b-victim")
        result = run_tool("delete_prompt_template", {"id": str(tpl.id)}, tool_context)
        _assert_denied(result, DESTRUCTIVE_BLOCKED_CODES)
        assert self._template_intact(tpl.id)

    def test_cross_org_destroy_not_executed(self, tool_context, other_org_context):
        tpl = make_prompt_template(tool_context, name="org-1-victim")
        result = run_tool(
            "delete_prompt_template", {"id": str(tpl.id)}, other_org_context
        )
        _assert_denied(result, DESTRUCTIVE_BLOCKED_CODES)
        assert self._template_intact(tpl.id)

    def test_anonymous_destroy_not_executed(self, tool_context, anonymous_context):
        tpl = make_prompt_template(tool_context, name="anon-victim")
        result = run_tool(
            "delete_prompt_template", {"id": str(tpl.id)}, anonymous_context
        )
        _assert_denied(result, {"PERMISSION_DENIED", "CONFIRMATION_REQUIRED"})
        assert self._template_intact(tpl.id)

    def test_cross_workspace_dataset_destroy_not_executed(
        self, tool_context, other_workspace_context
    ):
        ds = make_dataset(other_workspace_context, name="ws-b-ds-victim")
        result = run_tool("delete_dataset", {"id": str(ds.id)}, tool_context)
        _assert_denied(result, DESTRUCTIVE_BLOCKED_CODES)
        assert self._dataset_intact(ds.id)

    def test_same_workspace_destroy_positive_control(self, tool_context):
        """Authorized destroy must NOT be blocked by 3B authz: it either
        executes (pre-3A) or returns the 3A confirmation preview. Proves the
        denials above are isolation, not a broken tool."""
        tpl = make_prompt_template(tool_context, name="own-victim")
        result = run_tool("delete_prompt_template", {"id": str(tpl.id)}, tool_context)
        if result.error_code == "CONFIRMATION_REQUIRED":
            assert self._template_intact(tpl.id)  # preview = zero side effects
        else:
            assert not result.is_error, result.content[:300]
            assert not self._template_intact(tpl.id)


@pytest.mark.django_db
class TestBucketDFixMatrix:
    """Regression matrix for the 3B audit's bucket-D fixes, on the bridge
    path (VersionCompareView is bridge-only — it has no URL route). The
    guard must 404 cross-tenant callers and stay invisible to the owner."""

    @staticmethod
    def _make_template_with_versions(ctx, name):
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        tpl = EvalTemplate.no_workspace_objects.create(
            name=name,
            organization=ctx.organization,
            workspace=ctx.workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail"},
            visible_ui=True,
        )
        for criteria in ("v1 instructions", "v2 instructions"):
            EvalTemplateVersion.objects.create_version(
                eval_template=tpl,
                criteria=criteria,
                model="turing_large",
                config_snapshot={"output": "Pass/Fail"},
                user=ctx.user,
                organization=ctx.organization,
            )
        return tpl

    def test_same_org_version_compare_succeeds(self, tool_context):
        tpl = self._make_template_with_versions(tool_context, "own-compare")
        result = run_tool(
            "compare_eval_template_versions",
            {"eval_template_id": str(tpl.id), "a": "1", "b": "2"},
            tool_context,
        )
        assert not result.is_error, result.content[:300]

    def test_cross_org_version_compare_denied(self, tool_context, other_org_context):
        tpl = self._make_template_with_versions(tool_context, "org1-compare")
        result = run_tool(
            "compare_eval_template_versions",
            {"eval_template_id": str(tpl.id), "a": "1", "b": "2"},
            other_org_context,
        )
        _assert_denied(result)
        # No data leak: the version contents must not appear in the result.
        assert "v1 instructions" not in (result.content or "")

    def test_cross_workspace_version_compare_denied(
        self, tool_context, other_workspace_context
    ):
        tpl = self._make_template_with_versions(
            other_workspace_context, "ws-b-compare"
        )
        result = run_tool(
            "compare_eval_template_versions",
            {"eval_template_id": str(tpl.id), "a": "1", "b": "2"},
            tool_context,
        )
        _assert_denied(result)


# ---------------------------------------------------------------------------
# Layer 3 — approval isolation (design §2.3 row 5; no DB, fake Redis)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal stand-in implementing the subset confirmations.py uses
    (setex/get/ttl) so the isolation property under test is the KEYING,
    not Redis availability."""

    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = (value, ttl)

    def get(self, key):
        hit = self.store.get(key)
        return hit[0] if hit else None

    def ttl(self, key):
        hit = self.store.get(key)
        return hit[1] if hit else -2


class _Obj:
    def __init__(self, id):
        self.id = id


def _ctx(user_id, conversation_id=None, transport="falcon"):
    return ToolContext(
        user=_Obj(user_id),
        organization=_Obj("org-1"),
        workspace=_Obj("ws-1"),
        transport=transport,
        conversation_id=conversation_id,
    )


@pytest.fixture
def fake_confirm_redis(monkeypatch):
    from ai_tools import confirmations

    fake = _FakeRedis()
    monkeypatch.setattr(confirmations, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def nuke_tool():
    from pydantic import BaseModel as PydanticBaseModel

    from ai_tools.base import BaseTool, ToolResult

    class _NukeInput(PydanticBaseModel):
        id: str

    executed = []

    class _NukeTool(BaseTool):
        name = "_test_nuke"
        description = "Test destructive tool (approval isolation)."
        category = "test"
        input_model = _NukeInput
        execution_policy = "destructive"

        def execute(self, params, context):
            executed.append((params.id, context.user_id))
            return ToolResult(content="nuked", data={})

    return _NukeTool(), executed


class TestApprovalIsolation:
    """One user's server-held approval can never authorize another user's
    call (lookup key embeds user_id), and approvals don't bleed across
    conversations (key embeds conversation_id)."""

    def test_store_lookup_is_user_scoped(self, fake_confirm_redis):
        from ai_tools import confirmations

        ctx_a = _ctx("user-a", conversation_id="conv-1")
        ctx_b = _ctx("user-b", conversation_id="conv-1")
        args = {"id": "victim-1"}
        args_hash = confirmations.compute_args_hash(args)
        token, _ = confirmations.create_pending(
            ctx_a, "delete_dataset", args_hash, args, "preview"
        )
        confirmations.set_status(token, "approved")
        # Same tool, same exact args, same conversation — different user.
        assert confirmations.lookup(ctx_b, "delete_dataset", args_hash) is None
        rec_a = confirmations.lookup(ctx_a, "delete_dataset", args_hash)
        assert rec_a is not None and rec_a["status"] == "approved"

    def test_user_b_cannot_consume_user_a_approval(
        self, fake_confirm_redis, nuke_tool
    ):
        from ai_tools import confirmations

        tool, executed = nuke_tool
        ctx_a = _ctx("user-a", conversation_id="conv-1")
        ctx_b = _ctx("user-b", conversation_id="conv-1")

        # Phase 1: A previews; consumer-equivalent approves A's record.
        res = tool.run({"id": "victim-1"}, ctx_a)
        assert res.error_code == "CONFIRMATION_REQUIRED"
        assert executed == []
        confirmations.set_status(res.data["confirmation"]["token"], "approved")

        # B replays the exact args + confirm=true on the falcon transport:
        # must NOT execute — B just gets a fresh preview of their own.
        res_b = tool.run({"id": "victim-1", "confirm": True}, ctx_b)
        assert res_b.error_code == "CONFIRMATION_REQUIRED"
        assert executed == []
        assert res_b.data["confirmation"]["token"] != res.data["confirmation"]["token"]

        # Positive control — A's own phase-2 call consumes and executes,
        # proving B's denial above was isolation, not a broken gate.
        res_a = tool.run({"id": "victim-1", "confirm": True}, ctx_a)
        assert not res_a.is_error and res_a.error_code is None
        assert executed == [("victim-1", "user-a")]
        assert res_a.data.get("confirmed") is True

    def test_approval_does_not_bleed_across_conversations(
        self, fake_confirm_redis, nuke_tool
    ):
        from ai_tools import confirmations

        tool, executed = nuke_tool
        ctx_c1 = _ctx("user-a", conversation_id="conv-1")
        ctx_c2 = _ctx("user-a", conversation_id="conv-2")

        res = tool.run({"id": "victim-2"}, ctx_c1)
        assert res.error_code == "CONFIRMATION_REQUIRED"
        confirmations.set_status(res.data["confirmation"]["token"], "approved")

        # Same user, same args — different conversation: fresh preview.
        res_c2 = tool.run({"id": "victim-2", "confirm": True}, ctx_c2)
        assert res_c2.error_code == "CONFIRMATION_REQUIRED"
        assert executed == []
