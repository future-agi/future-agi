"""The EE agent key's scope, budget, cap, lifetime and audit (CN22c).

AM-C25's 22:50 addendum (binding) makes the gateway key CN22 mints for Omega
EE a scoped key, not an org-wide one:

1. Scopes ``mcp`` and ``models`` only. They ride on reserved key metadata
   (``scopes``; RESERVED_KEY_METADATA) that the gateway's key gate enforces
   (``agentcc-gateway/internal/keyscope``), and Django's routes answer the
   key 403 ``key_scope_denied`` (``middleware_ee_agent_key.py``).
2. Models: the org's own, resolved per request. The key carries no model or
   provider list, so the gateway confines it to the org's providers and its
   managed_models lists on every call (G11).
3. A spend budget per key: the workspace's (an Owner or Admin sets it) or
   the platform default, sent as ``budget_limit_usd`` and ``budget_period``
   and enforced by the gateway (402 ``agent_key_budget_exhausted``).
4. A per-org cap on live agent keys (409 ``agent_key_cap_reached``) and a
   90-day lifetime; EE rotates with a new key and ensure.
5. Every mint, rotation, scope refusal, budget refusal and cap refusal is
   audited with the workspace and EE as the actor.

The scope and budget are never stored on the key's row: they are derived
from the binding and the workspace policy each time Django sends the key to
the gateway, so no tenant write to the key can change them, and a policy
change reaches every live key at the gateway's next key sync.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models.audit_log import AuditScope
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey
from tfc.utils.audit import log_audit

EE_ACTOR = "omega-ee"
SCOPES = "mcp,models"

# Reserved key metadata the gateway reads (keyscope.Meta*).
META_SCOPES = "scopes"
META_BUDGET_LIMIT_USD = "budget_limit_usd"
META_BUDGET_PERIOD = "budget_period"

# The cap on live agent keys per org: one key per workspace plus a rotation's
# overlap fits well under it. An operator may move it.
CAP_ENV = "AGENTCC_EE_AGENT_KEY_CAP"
DEFAULT_CAP = 20

# Every agent key expires this long after it is minted (AM-C25, item 4).
LIFETIME = timedelta(days=90)

# A workspace with no policy row: each agent key may spend this much a month.
DEFAULT_BUDGET_ENV = "AGENTCC_EE_AGENT_KEY_DEFAULT_BUDGET_USD"
DEFAULT_BUDGET_USD = Decimal("50.00")
DEFAULT_PERIOD = AgentccEEWorkspacePolicy.Period.MONTHLY.value
MAX_BUDGET_USD = Decimal("1000000.00")
CENT = Decimal("0.01")

AUDIT_AGENT_KEY_CAP_REFUSED = "agentcc.ee_agent_key.cap_refused"
AUDIT_AGENT_KEY_ROTATED = "agentcc.ee_agent_key.rotated"
AUDIT_AGENT_KEY_SCOPE_REFUSED = "agentcc.ee_agent_key.scope_refused"
AUDIT_AGENT_KEY_BUDGET_REFUSED = "agentcc.ee_agent_key.budget_refused"
AUDIT_WORKSPACE_POLICY_UPDATED = "agentcc.ee_workspace_policy.updated"

# The gateway's refusal codes, and the audit action each one is written as.
CODE_SCOPE_DENIED = "key_scope_denied"
CODE_BUDGET_EXHAUSTED = "agent_key_budget_exhausted"
CODE_CAP_REACHED = "agent_key_cap_reached"
REFUSAL_ACTIONS = {
    CODE_SCOPE_DENIED: AUDIT_AGENT_KEY_SCOPE_REFUSED,
    CODE_BUDGET_EXHAUSTED: AUDIT_AGENT_KEY_BUDGET_REFUSED,
}


# --- Cap and lifetime --------------------------------------------------------


def cap() -> int:
    """The per-org cap on live agent keys: AGENTCC_EE_AGENT_KEY_CAP when it
    is a whole number of at least 1, else the default."""
    try:
        value = int(os.environ.get(CAP_ENV, "").strip())
    except ValueError:
        return DEFAULT_CAP
    return value if value >= 1 else DEFAULT_CAP


def live_agent_keys(org):
    """The org's agent keys the gateway still honours: active, not deleted,
    not expired. Revoked and expired keys leave room under the cap."""
    now = timezone.now()
    return AgentccAPIKey.no_workspace_objects.filter(
        ee_agent_key__organization=org,
        status=AgentccAPIKey.ACTIVE,
        deleted=False,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def expiry_from(now: datetime) -> datetime:
    return now + LIFETIME


EXPIRY_FIXED = (
    "This key belongs to Omega EE's provisioning API: it expires 90 days after "
    "it was minted and EE rotates it, so its expiry cannot be removed or moved "
    "later. Revoke it, or set an earlier expiry."
)


def extends_agent_key(key: AgentccAPIKey, expires_at) -> bool:
    """Whether setting ``expires_at`` on ``key`` would let an EE agent key
    live longer: no expiry, or a later one. An earlier one is allowed."""
    if binding_of(key) is None:
        return False
    return expires_at is None or key.expires_at is None or expires_at > key.expires_at


# --- The workspace budget ----------------------------------------------------


@dataclass(frozen=True)
class Budget:
    usd: Decimal
    period: str
    default: bool


def default_budget() -> Budget:
    raw = os.environ.get(DEFAULT_BUDGET_ENV, "").strip()
    usd = DEFAULT_BUDGET_USD
    if raw:
        try:
            parsed = Decimal(raw).quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            parsed = None
        if parsed is not None and parsed.is_finite() and 0 <= parsed <= MAX_BUDGET_USD:
            usd = parsed
    return Budget(usd, DEFAULT_PERIOD, True)


def budget_for(workspace_id, policies=None) -> Budget:
    """The budget each agent key of the workspace gets. ``policies`` is a
    preloaded {workspace id: policy row}, or None to read the one row."""
    if policies is None:
        row = AgentccEEWorkspacePolicy.objects.filter(workspace_id=workspace_id).first()
    else:
        row = policies.get(workspace_id)
    if row is None:
        return default_budget()
    return Budget(row.agent_key_budget_usd.quantize(CENT), row.budget_period, False)


def set_budget(org, workspace, usd: Decimal, period: str, user) -> Budget:
    """An Owner or Admin's budget for the workspace's agent keys, audited
    with the person as the actor."""
    before = budget_for(workspace.id)
    usd = usd.quantize(CENT)
    AgentccEEWorkspacePolicy.objects.update_or_create(
        workspace=workspace,
        defaults={
            "organization": org,
            "agent_key_budget_usd": usd,
            "budget_period": period,
            "updated_by": user,
        },
    )
    after = budget_for(workspace.id)
    log_audit(
        organization=org,
        action=AUDIT_WORKSPACE_POLICY_UPDATED,
        scope=AuditScope.ORGANIZATION,
        target_id=workspace.id,
        actor=user,
        changes={
            "budget_usd": {"old": str(before.usd), "new": str(after.usd)},
            "budget_period": {"old": before.period, "new": after.period},
        },
        metadata={"workspace": str(workspace.id)},
    )
    return after


def server_owned_metadata(budget: Budget) -> dict:
    """The reserved entries an agent key carries at the gateway."""
    return {
        META_SCOPES: SCOPES,
        META_BUDGET_LIMIT_USD: str(budget.usd.quantize(CENT)),
        META_BUDGET_PERIOD: budget.period,
    }


def binding_of(key: AgentccAPIKey) -> AgentccEEAgentKey | None:
    try:
        return key.ee_agent_key
    except ObjectDoesNotExist:
        return None


def payload_metadata(keys) -> dict:
    """{key pk: reserved entries} for the agent keys among ``keys`` (each
    loaded with ``ee_agent_key``), with one query for their workspaces'
    policies. A key that is not an agent key has no entry."""
    bindings = {key.pk: binding_of(key) for key in keys}
    workspaces = {b.workspace_id for b in bindings.values() if b is not None}
    policies = {
        row.workspace_id: row
        for row in AgentccEEWorkspacePolicy.objects.filter(workspace_id__in=workspaces)
    }
    return {
        pk: server_owned_metadata(budget_for(binding.workspace_id, policies))
        for pk, binding in bindings.items()
        if binding is not None
    }


# --- Usage per key -----------------------------------------------------------


def period_start(period: str, now: datetime) -> datetime:
    """The start of the budget period ``now`` falls in, as the gateway counts
    it (keyscope.Budget.PeriodStart): 00:00 UTC of the day, of the Monday, of
    the first of the month."""
    now = now.astimezone(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == AgentccEEWorkspacePolicy.Period.DAILY:
        return day
    if period == AgentccEEWorkspacePolicy.Period.WEEKLY:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def workspace_agent_keys(org, workspace, mint_idempotency_key=None) -> list[dict]:
    """Every agent key minted for the workspace, newest first, with its
    budget, what it spent this period (the request log's cost, as the
    gateway's budget counts it) and the Idempotency-Key it was minted with
    (CN22d). ``mint_idempotency_key`` keeps only the keys minted with it."""
    budget = budget_for(workspace.id)
    now = timezone.now()
    start = period_start(budget.period, now)
    keys = AgentccAPIKey.no_workspace_objects.select_related("ee_agent_key").filter(
        ee_agent_key__organization=org,
        ee_agent_key__workspace=workspace,
        deleted=False,
    )
    if mint_idempotency_key is not None:
        keys = keys.filter(ee_agent_key__mint_idempotency_key=mint_idempotency_key)
    keys = list(keys.order_by("-created_at"))
    spend = {
        row["api_key_id"]: row
        for row in AgentccRequestLog.no_workspace_objects.filter(
            organization=org,
            api_key_id__in=[k.gateway_key_id for k in keys],
            created_at__gte=start,
        )
        .values("api_key_id")
        .annotate(total=Sum("cost"), requests=Count("id"))
    }
    out = []
    for key in keys:
        used = spend.get(key.gateway_key_id, {})
        status = key.status
        if status == AgentccAPIKey.ACTIVE and key.expires_at and key.expires_at <= now:
            status = AgentccAPIKey.EXPIRED
        out.append(
            {
                "agent_key_id": key.gateway_key_id,
                "name": key.name,
                "status": status,
                "created_at": key.created_at,
                "expires_at": key.expires_at,
                "last_used_at": key.last_used_at,
                "budget_usd": str(budget.usd),
                "budget_period": budget.period,
                "period_start": start,
                "period_spend_usd": str(
                    (used.get("total") or Decimal(0)).quantize(Decimal("0.000001"))
                ),
                "requests": used.get("requests", 0),
                "mint_idempotency_key": _mint_key_of(key),
            }
        )
    return out


def _mint_key_of(key: AgentccAPIKey) -> str | None:
    binding = binding_of(key)
    if binding is None or binding.mint_idempotency_key is None:
        return None
    return str(binding.mint_idempotency_key)


# --- Audit -------------------------------------------------------------------


def audit_ee(org, action: str, target_id, **metadata) -> None:
    """Audited as EE, never as a person (AM-C18 Q1)."""
    log_audit(
        organization=org,
        action=action,
        scope=AuditScope.ORGANIZATION,
        target_id=target_id,
        actor=None,
        metadata={"actor": EE_ACTOR, **metadata},
    )


def audit_refusal(binding: AgentccEEAgentKey, code: str, *, surface: str, **detail):
    """A scope or budget refusal of an agent key, with its workspace."""
    audit_ee(
        binding.organization,
        REFUSAL_ACTIONS[code],
        binding.id,
        workspace=str(binding.workspace_id),
        agent_key_id=binding.api_key.gateway_key_id,
        code=code,
        surface=surface,
        **detail,
    )


def bindings_by_gateway_id(gateway_key_ids) -> dict:
    """{gateway key id: binding} for the agent keys among the ids."""
    return {
        b.api_key.gateway_key_id: b
        for b in AgentccEEAgentKey.objects.select_related(
            "api_key", "organization"
        ).filter(api_key__gateway_key_id__in=list(gateway_key_ids))
    }
