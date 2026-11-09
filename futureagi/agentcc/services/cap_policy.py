"""Cross-section safety envelope for the opt-in synchronous model cap slice.

The central config writer calls this on the complete, contract-normalized
payload, not on a patch. No validation here activates a policy or reconciles
historical spend. Limits are model-spend ceilings, not a complete agent ledger.
"""

import math
import re
from collections.abc import Mapping

MAX_CAP_USD = 1_000_000_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z", re.ASCII)


class CapPolicyRejected(ValueError):
    """An enabled cap envelope cannot be enforced by this gateway slice."""


def _reject(path, message):
    raise CapPolicyRejected(f"{path}: {message}")


def _identity(value, path):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _reject(path, "must be a bounded ASCII identity (1-128 characters)")


def _limit(value, path):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= MAX_CAP_USD
    ):
        _reject(path, "must be finite USD between 0 and 1000000000")


def _agents(routing):
    return (routing or {}).get("agents") or {}


def _validate_agents(agents, path):
    if agents.get("unpriced_under_cap", "reject") != "reject":
        _reject(f"{path}.unpriced_under_cap", "enabled admission caps require reject")
    if agents.get("max_run_usd") is not None:
        _limit(agents["max_run_usd"], f"{path}.max_run_usd")
    for key, agent in (agents.get("key_bindings") or {}).items():
        _identity(key, f"{path}.key_bindings key")
        _identity(agent, f"{path}.key_bindings.{key}")


def validate_cap_input_numbers(budgets, routing):
    """Reject numeric coercions before the generated model converts bool/string.

    Reuse its alias normalizers rather than maintaining a second alias table.
    The complete normalized payload below remains the policy authority.
    """
    from agentcc.contracts.gateway_admin import (
        AgentsConfig,
        BudgetsConfig,
        RoutingConfig,
    )

    if not isinstance(budgets, dict):
        return  # The generated object validator supplies the structural error.
    budgets = BudgetsConfig._normalize_input_aliases(budgets)
    enabled = budgets.get("admission_caps_enabled", False)
    if enabled is False or enabled is None:
        return
    if enabled is not True:
        _reject("budgets.admission_caps_enabled", "must be a boolean")
    for name in ("agents", "key_ids"):
        levels = budgets.get(name) or {}
        if not isinstance(levels, dict):
            continue
        for identity, value in levels.items():
            if isinstance(value, dict):
                _limit(value.get("limit"), f"budgets.{name}.{identity}.limit")

    def check_route(value, allow_rollout=True):
        if not isinstance(value, dict):
            return
        value = RoutingConfig._normalize_input_aliases(value)
        agents = value.get("agents")
        if isinstance(agents, dict):
            agents = AgentsConfig._normalize_input_aliases(agents)
            if agents.get("max_run_usd") is not None:
                _limit(agents["max_run_usd"], "routing.agents.max_run_usd")
        rollout = value.get("rollout")
        if allow_rollout and isinstance(rollout, dict):
            check_route(rollout.get("routing"), allow_rollout=False)

    check_route(routing)


def validate_cap_policy(payload):
    """Validate the normalized whole config, leaving legacy disabled policy alone.

    A rollout replaces its routing section, rather than merging it. A candidate
    must repeat all authoritative bindings and retain or lower the run ceiling;
    it cannot gain a fail-open or unpriced escape hatch. Presets have no cap
    fields in the contract, so cannot replace this envelope.
    """
    budgets = payload.get("budgets") or {}
    if not budgets.get("admission_caps_enabled", False):
        return
    if budgets.get("enabled") is not True:
        _reject("budgets.enabled", "must be true with admission_caps_enabled")
    if budgets.get("fail_mode", "closed") != "closed":
        _reject("budgets.fail_mode", "enabled admission caps require closed")
    if budgets.get("no_max_tokens") != "clamp":
        _reject("budgets.no_max_tokens", "enabled admission caps require clamp")
    for name in ("agents", "key_ids"):
        for identity, cap in (budgets.get(name) or {}).items():
            path = f"budgets.{name}.{identity}"
            _identity(identity, path)
            if not isinstance(cap, Mapping):
                _reject(path, "must be a daily hard-cap object")
            unknown = set(cap) - {"limit", "period", "hard", "action", "activated_at"}
            if unknown:
                _reject(path, f"unsupported cap options: {', '.join(sorted(unknown))}")
            _limit(cap.get("limit"), f"{path}.limit")
            if cap.get("period", "daily") != "daily":
                _reject(f"{path}.period", "only daily caps are supported")
            if (
                cap.get("hard", True) is not True
                or cap.get("action", "block") != "block"
            ):
                _reject(path, "only hard/block caps are supported")
    routing = payload.get("routing") or {}
    agents = _agents(routing)
    _validate_agents(agents, "routing.agents")
    rollout = routing.get("rollout")
    if not rollout:
        return
    candidate = _agents(rollout.get("routing"))
    _validate_agents(candidate, "routing.rollout.routing.agents")
    # No binding may be dropped or changed, including a currently uncapped one:
    # effective routing is not an administrative rebinding operation.
    bindings = agents.get("key_bindings") or {}
    other = candidate.get("key_bindings") or {}
    if any(other.get(key) != agent for key, agent in bindings.items()):
        _reject(
            "routing.rollout.routing.agents.key_bindings",
            "cannot drop or change stored bindings",
        )
    ceiling = agents.get("max_run_usd")
    lower = candidate.get("max_run_usd")
    if ceiling is not None and (lower is None or lower > ceiling):
        _reject(
            "routing.rollout.routing.agents.max_run_usd",
            "cannot drop or raise the stored run ceiling",
        )
