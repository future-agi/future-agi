"""Phase 3B — authz-posture audit of every registered AI tool.

For each bridge tool (a registry tool carrying a ``ViewSetBinding``) this
resolves the REAL view class, sets ``view.action`` exactly the way the
bridge does, and records:

- the resolved per-action permission classes (``view.get_permissions()``,
  honoring ``get_permissions`` overrides) — i.e. what
  ``ai_tools.authz.enforce_view_permissions`` now evaluates on every call;
- where queryset scoping comes from (BaseModelViewSetMixin lineage, custom
  ``get_queryset`` source heuristics, or the ContextVar-scoped
  ``BaseModelManager`` default manager that BaseTool.run's
  ``workspace_context`` activates);
- whether the handler reaches object-level permissions via ``get_object()``
  and whether it carries in-method guards;
- a risk bucket per the binding design §2.2:

    A  IsAuthenticated + ws/org-scoped queryset            -> OK
    B  org/workspace role permission class                  -> enforced, OK
    C  object-level permission class                        -> OK if handler
                                                               uses get_object()
    D  permission-dependent with UNSCOPED queryset          -> EXPOSED (fix)
    E  in-method guard (no scoped queryset)                 -> OK, documented
    F  AllowAny / APIKey / admin-token / empty / unresolved -> FLAG
    H  hand-written tool (no DRF view; scoped by
       workspace_context managers + tool code)              -> documented

Outputs:
- ``ai_tools/tests/manifests/authz_audit.json``  (machine-readable)
- ``ai_tools/tests/authz_audit_report.md``       (checked-in report, incl.
  the cluster-7 sign-off table and the destructive-execution audit)

Run in-container::

    docker exec ws1-backend bash -lc \
      "cd /app/backend && python -m ai_tools.tests.audit_authz"
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _setup_django():
    import os

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    django.setup()


# ---------------------------------------------------------------------------
# Per-view analysis helpers
# ---------------------------------------------------------------------------

# Permission classes that mean "anyone" or "non-user credentials" — should be
# ZERO on the bridge (the bridge auth floor still denies anonymous, but these
# views get flagged for explicit review).
FLAG_PERM_TOKENS = ("AllowAny", "APIKey", "AdminToken", "IsAdminToken")

# Heuristic tokens proving a custom get_queryset / handler scopes by tenant.
SCOPE_TOKEN_RE = re.compile(
    r"workspace|organization|request\.user|user_id|get_user_organization"
)
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_SELF_CALL_RE = re.compile(r"\bself\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_HELPER_SKIP_MODULE_PREFIXES = ("rest_framework", "django", "builtins", "drf_yasg")


def _scope_tokens_of(func, owner_cls=None) -> tuple[list[str], list[str]]:
    """Tenant-scope tokens in a function's source; when none are found
    directly, follow (a) called module-level helper functions and (b)
    ``self._method(...)`` delegations on the owner class, one level deep —
    the dominant codebase patterns are
    ``def get(self, request): qs = scoped_helper(request)`` and
    ``self._get_x_from_clickhouse(request, ...)`` where the scoping lives in
    the callee, not the handler.

    Returns (tokens, via_helpers).
    """
    func = inspect.unwrap(getattr(func, "__func__", func))
    try:
        src = inspect.getsource(func)
    except Exception:
        return [], []
    tokens = set(SCOPE_TOKEN_RE.findall(src))
    if tokens:
        return sorted(tokens), []
    via = []
    g = getattr(func, "__globals__", {}) or {}
    for name in sorted(set(_CALL_RE.findall(src))):
        helper = g.get(name)
        if not callable(helper) or isinstance(helper, type):
            continue
        mod = getattr(helper, "__module__", "") or ""
        if mod.startswith(_HELPER_SKIP_MODULE_PREFIXES):
            continue
        try:
            helper_src = inspect.getsource(inspect.unwrap(helper))
        except Exception:
            continue
        found = set(SCOPE_TOKEN_RE.findall(helper_src))
        if found:
            tokens |= found
            via.append(name)
    if owner_cls is not None:
        for name in sorted(set(_SELF_CALL_RE.findall(src))):
            method = getattr(owner_cls, name, None)
            if method is None:
                continue
            mod = getattr(method, "__module__", "") or ""
            if mod.startswith(_HELPER_SKIP_MODULE_PREFIXES):
                continue
            try:
                method_src = inspect.getsource(
                    inspect.unwrap(getattr(method, "__func__", method))
                )
            except Exception:
                continue
            found = set(SCOPE_TOKEN_RE.findall(method_src))
            if found:
                tokens |= found
                via.append(f"self.{name}")
    return sorted(tokens), via
GUARD_TOKEN_RE = re.compile(
    r"PermissionDenied|_has_queue_role|has_permission|organization_role"
    r"|workspace_role|is_admin|IsOrganizationAdmin|require_role|forbidden"
)


def _resolved_permission_classes(viewset_cls, action: str, method: str):
    """Resolve per-action permission instances the way the bridge does:
    instantiated view + ``view.action`` set, then ``get_permissions()``."""
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    django_request = APIRequestFactory().generic(
        (method or "GET").upper(), "/authz-audit/"
    )
    request = Request(django_request)
    request.auth = None
    request._authenticator = None

    view = viewset_cls()
    view.request = request
    view.action = action
    view.kwargs = {}
    view.format_kwarg = None
    view.args = ()
    perms = view.get_permissions()
    return [type(p).__name__ for p in perms], perms


def _object_level_perms(perm_instances) -> list[str]:
    out = []
    for p in perm_instances:
        for klass in type(p).__mro__:
            if "has_object_permission" in klass.__dict__ and not (
                klass.__module__ or ""
            ).startswith("rest_framework"):
                out.append(type(p).__name__)
                break
    return out


def _model_for_view(viewset_cls):
    qs = getattr(viewset_cls, "queryset", None)
    if qs is not None:
        return qs.model
    ser = getattr(viewset_cls, "serializer_class", None)
    meta = getattr(ser, "Meta", None)
    return getattr(meta, "model", None)


def _tenancy_fields(model) -> list[str]:
    if model is None:
        return []
    names = []
    try:
        for f in model._meta.get_fields():
            if f.name in ("workspace", "organization", "project", "user"):
                names.append(f.name)
    except Exception:
        pass
    return names


def _queryset_scoping(viewset_cls):
    """Classify where tenant scoping of the queryset comes from."""
    try:
        from tfc.utils.base_viewset import (
            BaseModelViewSetMixin,
            BaseModelViewSetMixinWithUserOrg,
        )

        if issubclass(viewset_cls, BaseModelViewSetMixinWithUserOrg):
            return "mixin:BaseModelViewSetMixinWithUserOrg (ws+org+soft-delete)", True
        if issubclass(viewset_cls, BaseModelViewSetMixin):
            return "mixin:BaseModelViewSetMixin (ws+org+soft-delete)", True
    except Exception:
        pass

    # Custom get_queryset override (defined outside rest_framework)?
    owner = None
    for klass in viewset_cls.__mro__:
        if "get_queryset" in klass.__dict__:
            owner = klass
            break
    if owner is not None and not (owner.__module__ or "").startswith(
        "rest_framework"
    ):
        tokens, via = _scope_tokens_of(owner.__dict__["get_queryset"])
        if tokens:
            via_note = f" via {', '.join(via)}" if via else ""
            return (
                f"custom get_queryset @ {owner.__module__}.{owner.__name__}"
                f" (tokens: {', '.join(tokens)}{via_note})"
            ), True
        return (
            f"custom get_queryset @ {owner.__module__}.{owner.__name__}"
            " (NO tenant tokens)"
        ), False

    # ContextVar-scoped default manager? BaseTool.run always enters
    # workspace_context, so BaseModelManager.objects is tenant-filtered on
    # the bridge path — but ONLY for models with a real `workspace` DB field
    # (BaseModelManager.get_queryset filters nothing on org-only models; the
    # organization_id clause merely widens the workspace Q).
    model = _model_for_view(viewset_cls)
    if model is not None:
        try:
            from tfc.utils.base_model import BaseModelManager, _has_db_field

            mgr = getattr(model, "objects", None) or model._default_manager
            if isinstance(mgr, BaseModelManager) and _has_db_field(
                model, "workspace"
            ):
                return (
                    "ContextVar manager (BaseModelManager workspace filter "
                    "under workspace_context; fields: "
                    f"{', '.join(_tenancy_fields(model))})"
                ), True
        except Exception:
            pass

    return "NONE DETECTED", False


def _handler_facts(viewset_cls, action: str, method: str):
    handler = getattr(viewset_cls, action, None)
    if handler is None and method:
        handler = getattr(viewset_cls, method.lower(), None)
    if handler is None:
        return {"found": False, "uses_get_object": False, "guard_tokens": [],
                "scope_tokens": [], "scope_via": []}
    try:
        src = inspect.getsource(inspect.unwrap(handler))
    except Exception:
        src = ""
    scope_tokens, scope_via = _scope_tokens_of(handler, owner_cls=viewset_cls)
    return {
        "found": True,
        "uses_get_object": "get_object(" in src,
        "guard_tokens": sorted(set(GUARD_TOKEN_RE.findall(src))),
        "scope_tokens": scope_tokens,
        "scope_via": scope_via,
    }


# Hand-reviewed verdicts for rows the static heuristics cannot classify.
# Every entry REQUIRES a justification naming the verified mechanism.
REVIEWED_OVERRIDES = {
    # Create-only APIView: handler is `request.validated_serializer.save()`.
    # SimulatorAgent has a workspace FK and BaseModel.save() auto-stamps the
    # caller's workspace from workspace_context (WorkspaceModelMixin), so the
    # row can only be created in the caller's own tenant; there is no
    # tenant-keyed read in the handler. Verified 2026-06-10 (3B audit).
    "create_simulator_agent": (
        "E",
        False,
        "reviewed: create-only; workspace auto-stamped by workspace_context "
        "on save; no cross-tenant read surface",
    ),
}


def _bucket(perm_names, perm_error, object_perms, scoped, handler):
    if perm_error:
        return "F", True, "permission resolution failed (runtime fails closed)"
    flagged = [p for p in perm_names if any(t in p for t in FLAG_PERM_TOKENS)]
    if not perm_names or flagged:
        reason = (
            f"flag perms: {', '.join(flagged)}" if flagged
            else "EMPTY permission_classes (project default AllowAny)"
        )
        # The bridge auth floor + tenant scoping mitigate; exposed only when
        # the queryset is also unscoped.
        return "F", not scoped, reason
    if object_perms:
        ok = handler["uses_get_object"]
        return "C", not ok, (
            "object perms via get_object()" if ok
            else "object-level permission NOT reached (no get_object())"
        )
    non_auth = [p for p in perm_names if p != "IsAuthenticated"]
    if non_auth:
        return "B", False, f"role perms enforced: {', '.join(non_auth)}"
    if scoped:
        return "A", False, "IsAuthenticated + scoped queryset"
    if handler["guard_tokens"] or handler["scope_tokens"]:
        return "E", False, (
            "in-method scoping/guards: "
            + ", ".join(handler["guard_tokens"] + handler["scope_tokens"])
        )
    return "D", True, "permission-dependent with UNSCOPED queryset"


def audit_tool(tool) -> dict:
    from ai_tools.drf_bridge import _resolve_class

    binding = getattr(tool, "binding", None)
    row = {
        "tool": tool.name,
        "category": tool.category,
        "execution_policy": getattr(tool, "execution_policy", "") or "read",
    }
    if binding is None:
        row.update(
            kind="hand-written",
            view=None,
            action=None,
            method=None,
            permission_classes=[],
            object_permissions=[],
            queryset_scoping=(
                "workspace_context ContextVar managers + tool-internal "
                "queries (no DRF view)"
            ),
            uses_get_object=False,
            handler_guards=[],
            bucket="H",
            exposed=False,
            note="hand-written tool; covered by BaseTool.run workspace_context",
        )
        return row

    row["kind"] = "bridge"
    row["view"] = binding.viewset_class
    row["action"] = binding.action
    row["method"] = binding.method
    try:
        viewset_cls = _resolve_class(binding.viewset_class)
    except Exception as e:
        row.update(
            permission_classes=[],
            object_permissions=[],
            queryset_scoping="VIEW IMPORT FAILED",
            uses_get_object=False,
            handler_guards=[],
            bucket="F",
            exposed=True,
            note=f"view import failed: {e}",
        )
        return row

    perm_error = None
    try:
        perm_names, perm_instances = _resolved_permission_classes(
            viewset_cls, binding.action, binding.method
        )
    except Exception as e:
        perm_error = f"{type(e).__name__}: {e}"
        perm_names, perm_instances = [], []

    scoping, scoped = _queryset_scoping(viewset_cls)
    handler = _handler_facts(viewset_cls, binding.action, binding.method)
    object_perms = _object_level_perms(perm_instances)
    bucket, exposed, note = _bucket(
        perm_names, perm_error, object_perms, scoped, handler
    )
    if perm_error:
        note = f"{note}; {perm_error}"
    if tool.name in REVIEWED_OVERRIDES and bucket == "D":
        bucket, exposed, note = REVIEWED_OVERRIDES[tool.name]
    row.update(
        permission_classes=perm_names,
        object_permissions=object_perms,
        queryset_scoping=scoping,
        uses_get_object=handler["uses_get_object"],
        handler_guards=handler["guard_tokens"],
        bucket=bucket,
        exposed=exposed,
        note=note,
    )
    return row


# ---------------------------------------------------------------------------
# Cluster 7 — static sign-off (stays UNBRIDGED in 3B per design §2.4)
# ---------------------------------------------------------------------------

CLUSTER7 = [
    # (dotted view class, capability, verdict)
    ("accounts.views.keys.SecretKeyAPIViewSet",
     "get_secret_keys / generate_secret_key / enable_key / disable_key / "
     "delete_secret_key",
     "DEFER — returns/creates live API-key material; frontend secret-masking "
     "(UX_UI 7.6) is a hard prerequisite before any bridging."),
    ("agentcc.views.api_key.AgentccAPIKeyViewSet",
     "revoke (credential lifecycle)",
     "DEFER — credential lifecycle; bridge later WITH destructive gate + "
     "secret masking. check_permissions prerequisite now satisfied by 3B."),
    ("accounts.views.workspace.WorkspaceManagementView",
     "workspace create/update/delete",
     "DEFER — tenancy-shaping writes; needs role-perm hardening review "
     "before LLM exposure (IsAuthenticated-only today)."),
    ("accounts.views.workspace.WorkspaceMembershipView",
     "workspace membership add/remove",
     "DEFER — membership writes change who can see workspace data; bridge "
     "only with org/workspace-admin permission classes asserted."),
    ("accounts.views.rbac_views.MemberListAPIView",
     "list org members",
     "BRIDGEABLE LATER (read) — IsOrganizationAdmin enforced by 3B "
     "check_permissions; deferred with the rest of the cluster."),
    ("accounts.views.rbac_views.MemberRoleUpdateAPIView",
     "change member role",
     "DEFER — privilege escalation surface; requires explicit product "
     "sign-off + destructive-style confirmation."),
    ("accounts.views.rbac_views.MemberRemoveAPIView",
     "remove member",
     "DEFER — destructive on people-access; same bar as role updates."),
    ("accounts.views.rbac_views.WorkspaceMemberRoleUpdateAPIView",
     "change workspace member role",
     "DEFER — same as MemberRoleUpdateAPIView at workspace scope."),
    ("accounts.views.rbac_views.WorkspaceMemberRemoveAPIView",
     "remove workspace member",
     "DEFER — same as MemberRemoveAPIView at workspace scope."),
    ("accounts.views.organization_views.OrganizationCreateAPIView",
     "create organization",
     "DO NOT BRIDGE — tenant creation is onboarding-flow only; no LLM "
     "use-case."),
    ("accounts.views.organization_views.OrganizationUpdateAPIView",
     "update organization",
     "DEFER — org-level writes; needs org-admin perm assertion + product "
     "sign-off."),
]


def audit_cluster7() -> list[dict]:
    from ai_tools.authz import describe_view_authz
    from ai_tools.drf_bridge import _resolve_class

    rows = []
    for dotted, capability, verdict in CLUSTER7:
        try:
            cls = _resolve_class(dotted)
            info = describe_view_authz(cls)
            perms = info["permission_classes"]
        except Exception as e:
            perms = [f"IMPORT FAILED: {e}"]
        rows.append(
            {
                "view": dotted,
                "capability": capability,
                "permission_classes": perms,
                "verdict": verdict,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Destructive-execution audit (3A acceptance hook, owned by 3B per §2.2):
# every completed destructive entry in Message.tool_calls carries
# confirmed:true.
# ---------------------------------------------------------------------------


def audit_destructive_executions() -> dict:
    try:
        from ee.falcon_ai.models import Message
    except Exception as e:
        return {"status": "skipped", "reason": f"ee Message unavailable: {e}"}
    try:
        completed = 0
        violations = []
        qs = Message.no_workspace_objects.exclude(tool_calls=[]) if hasattr(
            Message, "no_workspace_objects"
        ) else Message.objects.exclude(tool_calls=[])
        for msg in qs.iterator():
            for entry in msg.tool_calls or []:
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("execution_policy") == "destructive"
                    and entry.get("status") == "completed"
                ):
                    completed += 1
                    if entry.get("confirmed") is not True:
                        violations.append(
                            {
                                "message_id": str(msg.id),
                                "tool": entry.get("tool_name")
                                or entry.get("name"),
                            }
                        )
        return {
            "status": "ok",
            "completed_destructive_entries": completed,
            "violations": violations,
        }
    except Exception as e:
        return {"status": "skipped", "reason": f"query failed: {e}"}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

BUCKET_LEGEND = {
    "A": "IsAuthenticated + scoped queryset — OK",
    "B": "org/workspace role permission class — enforced by 3B, OK",
    "C": "object-level permission class — OK when reached via get_object()",
    "D": "permission-dependent with UNSCOPED queryset — EXPOSED, must fix",
    "E": "in-method guard / in-handler scoping — OK, documented",
    "F": "AllowAny / APIKey / admin-token / empty / unresolved — FLAG",
    "H": "hand-written tool (no DRF view) — workspace_context-scoped",
}


def render_report(rows, cluster7, destructive_audit, exempt) -> str:
    by_bucket: dict[str, list[dict]] = {}
    for r in rows:
        by_bucket.setdefault(r["bucket"], []).append(r)
    exposed = [r for r in rows if r["exposed"]]
    bridge_rows = [r for r in rows if r["kind"] == "bridge"]
    hand_rows = [r for r in rows if r["kind"] == "hand-written"]

    lines = []
    a = lines.append
    a("# Falcon AI-tools authorization audit (Phase 3B)")
    a("")
    a(f"Generated by `ai_tools/tests/audit_authz.py` on "
      f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}.")
    a("")
    a("Per-call enforcement: `DRFBridgeTool.execute` now evaluates the "
      "view's REAL per-action permission classes against the bridge request "
      "(`ai_tools/authz.py:enforce_view_permissions`) before any handler "
      "runs — plus an authenticated-user floor (the project sets no "
      "`DEFAULT_PERMISSION_CLASSES`, so DRF's default is AllowAny). "
      "Denials return a clean `PERMISSION_DENIED` ToolResult; a permission "
      "class that crashes on the synthetic request fails CLOSED.")
    a("")
    a(f"- Tools audited: **{len(rows)}** "
      f"({len(bridge_rows)} bridged, {len(hand_rows)} hand-written)")
    a(f"- AUTHZ_EXEMPT entries: **{len(exempt)}** "
      f"({sorted(exempt) if exempt else 'empty — as designed'})")
    a(f"- Exposed (must-fix) rows: **{len(exposed)}**")
    a("")
    a("## Bucket summary")
    a("")
    a("| Bucket | Meaning | Count |")
    a("|---|---|---|")
    for b in "ABCDEFH":
        a(f"| {b} | {BUCKET_LEGEND[b]} | {len(by_bucket.get(b, []))} |")
    a("")
    if exposed:
        a("## EXPOSED rows (mandatory fixes)")
        a("")
        a("| Tool | View.action | Bucket | Why |")
        a("|---|---|---|---|")
        for r in exposed:
            a(f"| `{r['tool']}` | `{r['view']}.{r['action']}` "
              f"| {r['bucket']} | {r['note']} |")
        a("")
    a("## Bridged tools — full table")
    a("")
    a("| Tool | Policy | View.action (method) | Permission classes | "
      "Queryset scoping | get_object() | Bucket | Exposed |")
    a("|---|---|---|---|---|---|---|---|")
    for r in sorted(bridge_rows, key=lambda x: (x["bucket"], x["tool"])):
        view_short = (r["view"] or "").rsplit(".", 1)[-1]
        a(
            f"| `{r['tool']}` | {r['execution_policy']} "
            f"| `{view_short}.{r['action']}` ({r['method']}) "
            f"| {', '.join(r['permission_classes']) or '(empty)'} "
            f"| {r['queryset_scoping']} "
            f"| {'yes' if r['uses_get_object'] else 'no'} "
            f"| {r['bucket']} | {'YES' if r['exposed'] else 'no'} |"
        )
    a("")
    a("## Hand-written tools")
    a("")
    a("No DRF view: these run entirely inside `BaseTool.run`'s "
      "`workspace_context`, so `BaseModelManager` querysets are "
      "workspace/org-filtered; risk surface is the tool code itself "
      "(reviewed per packet, not via permission classes).")
    a("")
    a("| Tool | Category | Policy |")
    a("|---|---|---|")
    for r in sorted(hand_rows, key=lambda x: x["tool"]):
        a(f"| `{r['tool']}` | {r['category']} | {r['execution_policy']} |")
    a("")
    a("## Cluster 7 sign-off (accounts / members / orgs / secret keys)")
    a("")
    a("Decision (design §2.4): cluster 7 stays UNBRIDGED in 3B. "
      "Prerequisite (a) — per-call `check_permissions` enforcement — is "
      "now shipped; prerequisite (b) — frontend secret-masking of tool "
      "params/results (UX_UI 7.6) — is not, and bridging key-bearing "
      "views without it would render secrets verbatim into chat and "
      "persist them in `Message.tool_calls`. Per-tool verdicts:")
    a("")
    a("| View | Capability | Permission classes | Verdict |")
    a("|---|---|---|---|")
    for r in cluster7:
        a(f"| `{r['view']}` | {r['capability']} "
          f"| {', '.join(r['permission_classes'])} | {r['verdict']} |")
    a("")
    a("## Destructive-execution audit (Message.tool_calls)")
    a("")
    if destructive_audit.get("status") == "ok":
        n = destructive_audit["completed_destructive_entries"]
        v = destructive_audit["violations"]
        a(f"Completed destructive entries scanned: **{n}**; entries missing "
          f"`confirmed:true`: **{len(v)}**.")
        if v:
            a("")
            a("Violations (3A acceptance breach — every completed "
              "destructive entry must carry `confirmed:true`):")
            for item in v[:50]:
                a(f"- message `{item['message_id']}` tool `{item['tool']}`")
    else:
        a(f"Skipped: {destructive_audit.get('reason')}")
    a("")
    return "\n".join(lines)


def main() -> int:
    _setup_django()

    from ai_tools.authz import AUTHZ_EXEMPT
    from ai_tools.registry import registry

    tools = registry.list_all()
    rows = [audit_tool(t) for t in tools]
    cluster7 = audit_cluster7()
    destructive_audit = audit_destructive_executions()

    here = Path(__file__).resolve().parent
    manifest_path = here / "manifests" / "authz_audit.json"
    report_path = here / "authz_audit_report.md"

    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "tool_count": len(rows),
                "authz_exempt": sorted(AUTHZ_EXEMPT),
                "tools": rows,
                "cluster7": cluster7,
                "destructive_execution_audit": destructive_audit,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    report_path.write_text(
        render_report(rows, cluster7, destructive_audit, AUTHZ_EXEMPT)
    )

    exposed = [r for r in rows if r["exposed"]]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    print(f"audited {len(rows)} tools; buckets: "
          + ", ".join(f"{b}={counts.get(b, 0)}" for b in "ABCDEFH"))
    print(f"exposed rows: {len(exposed)}")
    for r in exposed:
        print(f"  EXPOSED {r['bucket']} {r['tool']} ({r['view']}.{r['action']}) "
              f"- {r['note']}")
    print(f"wrote {manifest_path}")
    print(f"wrote {report_path}")
    return 1 if exposed and any(r["bucket"] == "D" for r in exposed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
