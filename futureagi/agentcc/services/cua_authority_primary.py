"""Fresh primary-only authority reads. No writes, positive cache or model fallbacks.

This is an internal service, not an authenticated HTTP endpoint. Its future
transport must use non_atomic_requests: an inherited transaction can contain a
snapshot older than the gateway's challenge and is deliberately refused here.
"""

from agentcc.services.cua_authority_policy import AuthorityDenied, AuthorityUnavailable


def _one(query, *, denied=False):
    rows = list(query[:2])
    if len(rows) != 1:
        raise AuthorityDenied() if denied and not rows else AuthorityUnavailable()
    return rows[0]


def _normal_policy(config):
    # Consume the existing owner's alias tables, never invent a second policy
    # parser. Do not use model_validate here: numeric coercions are not evidence.
    from agentcc.contracts.gateway_admin import AdmissionCapConfig, AgentsConfig, BudgetsConfig, RoutingConfig

    budgets = BudgetsConfig._normalize_input_aliases(config["budgets"])
    routing = RoutingConfig._normalize_input_aliases(config["routing"])
    if not isinstance(budgets, dict) or not isinstance(routing, dict):
        raise AuthorityUnavailable()
    for name in ("agents", "key_ids"):
        levels = budgets.get(name)
        if levels is not None:
            if not isinstance(levels, dict):
                raise AuthorityUnavailable()
            budgets[name] = {key: AdmissionCapConfig._normalize_input_aliases(cap)
                             for key, cap in levels.items()}
    if "agents" in routing:
        routing["agents"] = AgentsConfig._normalize_input_aliases(routing["agents"])
    rollout = routing.get("rollout")
    if rollout:
        if not isinstance(rollout, dict):
            raise AuthorityUnavailable()
        candidate = RoutingConfig._normalize_input_aliases(rollout.get("routing"))
        if not isinstance(candidate, dict):
            raise AuthorityUnavailable()
        if "agents" in candidate:
            candidate["agents"] = AgentsConfig._normalize_input_aliases(candidate["agents"])
        routing["rollout"] = {**rollout, "routing": candidate}
    return budgets, routing


def read_primary(subject):
    """Read key, memberships and active policy in one fresh Postgres snapshot.

    No secret columns (hash/prefix/metadata/owner/provider configuration) are
    selected from key rows. default is explicit on every query; replica routing
    and a default connection accidentally pointing at a standby both refuse.
    """
    from django.db import connections, transaction
    from django.utils import timezone

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from agentcc.models.api_key import AgentccAPIKey
    from agentcc.models.cap_activation import AgentccCapActivation, AgentccCapPolicyRevision
    from agentcc.models.org_config import AgentccOrgConfig

    connection = connections["default"]
    if (connection.vendor != "postgresql" or connection.in_atomic_block
            or not connection.get_autocommit()):
        raise AuthorityUnavailable()
    with transaction.atomic(using="default"):
        with connection.cursor() as cursor:
            # First statement after BEGIN; no read before isolation is pinned.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '750ms'")
            cursor.execute("SET LOCAL lock_timeout = '500ms'")
            cursor.execute("SELECT pg_is_in_recovery()")
            if cursor.fetchone() != (False,):
                raise AuthorityUnavailable()
        key = _one(AgentccAPIKey._base_manager.using("default").filter(
            gateway_key_id=subject["key_id"], deleted=False, status=AgentccAPIKey.ACTIVE,
            organization_id=subject["org_id"], user_id=subject["user_id"],
            user__is_active=True, ee_agent_key__isnull=True,
        ).values("organization_id", "user_id", "workspace_id", "gateway_key_id", "expires_at"), denied=True)
        expires = key["expires_at"]
        if expires is not None and expires <= timezone.now():
            raise AuthorityDenied()
        if key["workspace_id"] is not None and str(key["workspace_id"]) != subject["workspace_id"]:
            raise AuthorityDenied()
        _one(OrganizationMembership._base_manager.using("default").filter(
            organization_id=key["organization_id"], user_id=key["user_id"],
            is_active=True, deleted=False, user__is_active=True,
        ).values_list("pk", flat=True), denied=True)
        _one(Workspace._base_manager.using("default").filter(
            pk=subject["workspace_id"], organization_id=key["organization_id"],
            is_active=True, deleted=False,
        ).values_list("pk", flat=True), denied=True)
        _one(WorkspaceMembership._base_manager.using("default").filter(
            workspace_id=subject["workspace_id"], user_id=key["user_id"],
            is_active=True, deleted=False,
        ).values_list("pk", flat=True), denied=True)
        config = _one(AgentccOrgConfig._base_manager.using("default").filter(
            organization_id=key["organization_id"], is_active=True, deleted=False,
        ).values("budgets", "routing"))
        revision = _one(AgentccCapPolicyRevision._base_manager.using("default").filter(
            organization_id=key["organization_id"],
        ).values_list("revision", flat=True))
        budgets, routing = _normal_policy(config)
        agents = routing.get("agents") or {}
        bindings = agents.get("key_bindings") or {}
        agent = bindings.get(key["gateway_key_id"])
        levels = ["key_id:" + key["gateway_key_id"]]
        if isinstance(agent, str) and agent:
            levels.append("agent:" + agent)
        activation_rows = list(AgentccCapActivation._base_manager.using("default").filter(
            organization_id=key["organization_id"], level__in=levels,
        ).values_list("level", "activated_at"))
        activations = dict(activation_rows)
        if len(activations) != len(activation_rows):
            raise AuthorityUnavailable()
        return {
            "subject": {"kind": "personal_key", "key_id": key["gateway_key_id"],
                        "org_id": str(key["organization_id"]), "user_id": str(key["user_id"]),
                        "workspace_id": subject["workspace_id"]},
            "expires_at": expires, "revision": revision, "budgets": budgets,
            "routing": routing, "activations": activations,
        }
