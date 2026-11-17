"""Read-only H03 expected-policy comparison, not a gateway admission issuer.

Inputs are the independently read primary policy and the gateway's expectation.
An equal correlation ID never proves the gateway's private admission provenance.
No setters, quota changes, caches or caller-supplied authority fallbacks live here.
"""

import math
import re
from copy import deepcopy
from datetime import datetime, timezone

from agentcc.services.cap_policy import validate_cap_policy

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z", re.ASCII)
_MAX_REVISION = 2**63 - 1
_STATES = {"required", "not_applicable"}
_POLICY_FIELDS = {"policy_revision", "applicability", "activations", "policy_relation"}
_APPLICABILITY = {"policy_state", "key_daily", "agent_daily", "administrative_binding", "administrative_run"}
_RELATION = {"org_id", "key_id", "administrative_agent", "key_daily", "agent_daily", "run_limit_micros"}


class AuthorityDenied(Exception):
    """Known identity, membership, revocation or policy mismatch; value-free."""


class AuthorityUnavailable(Exception):
    """Missing, ambiguous or unsupported proof; value-free."""


def _require(condition):
    if not condition:
        raise AuthorityUnavailable()


def _record(value, fields):
    _require(type(value) is dict and set(value) == fields)


def _identity(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)


def _revision(value):
    _require(type(value) is int and 0 < value <= _MAX_REVISION)


def utc_stamp(value):
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activation_stamp(value):
    _require(type(value) is str and len(value) <= 40 and value.endswith("Z"))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(utc_stamp(parsed) == value)
    except (ValueError, OverflowError):
        raise AuthorityUnavailable() from None


def _cap_evidence(value):
    if value is None:
        return
    _record(value, {"limit_micros", "period", "hard", "action"})
    _require(type(value["limit_micros"]) is int and 0 <= value["limit_micros"] <= 10**15)
    _require(value["period"] == "daily" and value["hard"] is True and value["action"] == "block")


def _validate_policy(value):
    _record(value, _POLICY_FIELDS)
    _revision(value["policy_revision"])
    app = value["applicability"]
    _record(app, _APPLICABILITY)
    _require(app["policy_state"] in ("enabled", "disabled"))
    for field in _APPLICABILITY - {"policy_state"}:
        _require(type(app[field]) is str and app[field] in _STATES)
    slots = value["activations"]
    _record(slots, {"key_daily", "agent_daily"})
    for slot, prefix in (("key_daily", "key_id:"), ("agent_daily", "agent:")):
        activation = slots[slot]
        _require(type(activation) is dict)
        if activation.get("state") == "not_applicable":
            _record(activation, {"state"})
        else:
            _record(activation, {"state", "level", "activation_id"})
            _require(activation["state"] == "required")
            level = activation["level"]
            _require(type(level) is str and level.startswith(prefix))
            _identity(level[len(prefix):])
            _activation_stamp(activation["activation_id"])
    relation = value["policy_relation"]
    _record(relation, _RELATION)
    _identity(relation["org_id"])
    _identity(relation["key_id"])
    if relation["administrative_agent"] is not None:
        _identity(relation["administrative_agent"])
    _cap_evidence(relation["key_daily"])
    _cap_evidence(relation["agent_daily"])
    run = relation["run_limit_micros"]
    _require(run is None or (type(run) is int and 0 <= run <= 10**15))


def validate_expected(expected):
    """Closed evidence shape; no coercion, zero-revision or absent-slot equality."""
    try:
        _record(expected, _POLICY_FIELDS | {"central_admission_id"})
        _identity(expected["central_admission_id"])
        _validate_policy({key: expected[key] for key in _POLICY_FIELDS})
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AuthorityUnavailable() from None


def compare_policy(expected, observed):
    validate_expected(expected)
    _validate_policy(observed)
    return "match" if all(expected[key] == observed[key] for key in _POLICY_FIELDS) else "mismatch"


def _bucket(cap, level, activations):
    if cap is None:
        return {"state": "not_applicable"}, None
    _require(type(cap) is dict)
    stored = activations.get(level)
    _require(stored is not None)
    stamp = utc_stamp(stored)
    _require(type(cap.get("activated_at")) is str and cap["activated_at"] == stamp)
    # Same binary-float floor-to-micros as the gateway, never Decimal rounding up.
    micros = math.floor(float(cap["limit"]) * 1_000_000)
    return ({"state": "required", "level": level, "activation_id": stamp},
            {"limit_micros": micros, "period": "daily", "hard": True, "action": "block"})


def observe_policy(snapshot):
    """Project canonical primary policy; never read the gateway expectation."""
    try:
        budgets, routing = snapshot["budgets"], snapshot["routing"]
        _require(type(budgets) is dict and type(routing) is dict)
        revision = snapshot["revision"]
        _revision(revision)
        _revision(budgets.get("policy_revision"))
        _require(revision == budgets["policy_revision"])
        enabled = budgets.get("admission_caps_enabled")
        _require(type(enabled) is bool)
        validate_cap_policy({"budgets": budgets, "routing": routing})
        who = snapshot["subject"]
        _identity(who["org_id"])
        _identity(who["key_id"])
        agents = routing.get("agents") or {}
        _require(type(agents) is dict)
        # This slice cannot certify T7, even if a daily cap also exists.
        if enabled and agents.get("max_run_usd") is not None:
            raise AuthorityUnavailable()
        bindings = agents.get("key_bindings") or {}
        _require(type(bindings) is dict)
        agent = bindings.get(who["key_id"]) if enabled else None
        if agent is not None:
            _identity(agent)
        agent_caps, key_caps = budgets.get("agents") or {}, budgets.get("key_ids") or {}
        _require(type(agent_caps) is dict and type(key_caps) is dict)
        if enabled and agent_caps and agent is None:
            raise AuthorityDenied()
        ac = agent_caps.get(agent) if enabled else None
        kc = key_caps.get(who["key_id"]) if enabled else None
        _require(type(snapshot["activations"]) is dict)
        key_activation, key_policy = _bucket(kc, "key_id:" + who["key_id"], snapshot["activations"])
        agent_activation, agent_policy = _bucket(ac, "agent:" + (agent or ""), snapshot["activations"])
        result = {
            "policy_revision": revision,
            "applicability": {"policy_state": "enabled" if enabled else "disabled",
                              "key_daily": key_activation["state"],
                              "agent_daily": agent_activation["state"],
                              "administrative_binding": "required" if agent else "not_applicable",
                              "administrative_run": "not_applicable"},
            "activations": {"key_daily": key_activation, "agent_daily": agent_activation},
            "policy_relation": {"org_id": who["org_id"], "key_id": who["key_id"],
                                "administrative_agent": agent, "key_daily": key_policy,
                                "agent_daily": agent_policy, "run_limit_micros": None},
        }
        _validate_policy(result)
        return deepcopy(result)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise AuthorityUnavailable() from None
