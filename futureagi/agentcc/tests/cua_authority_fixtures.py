"""Synthetic independent H03 expectations; no production credentials or providers."""

from datetime import datetime, timezone

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
ACTIVATION = datetime(2026, 11, 1, tzinfo=timezone.utc)
STAMP = "2026-11-01T00:00:00Z"
ORG = "00000000-0000-4000-8000-000000000001"
USER = "00000000-0000-4000-8000-000000000002"
WORKSPACE = "00000000-0000-4000-8000-000000000003"


def subject():
    return {"kind": "personal_key", "key_id": "h03-key", "org_id": ORG,
            "user_id": USER, "workspace_id": WORKSPACE}


def requirements():
    return {"root_run": False, "budget_scope": False, "caller_run_scope": False}


def snapshot():
    return {
        "subject": subject(), "expires_at": None, "revision": 12,
        "budgets": {"admission_caps_enabled": True, "enabled": True,
                    "fail_mode": "closed", "no_max_tokens": "clamp",
                    "policy_revision": 12,
                    "key_ids": {"h03-key": {"limit": 2.5, "activated_at": STAMP}}},
        "routing": {}, "activations": {"key_id:h03-key": ACTIVATION},
    }


def expected():
    # Deliberately not built with the implementation's projection function.
    return {
        "central_admission_id": "central-h03-request-1", "policy_revision": 12,
        "applicability": {"policy_state": "enabled", "key_daily": "required",
                          "agent_daily": "not_applicable",
                          "administrative_binding": "not_applicable",
                          "administrative_run": "not_applicable"},
        "activations": {"key_daily": {"state": "required", "level": "key_id:h03-key",
                                      "activation_id": STAMP},
                        "agent_daily": {"state": "not_applicable"}},
        "policy_relation": {"org_id": ORG, "key_id": "h03-key",
                            "administrative_agent": None,
                            "key_daily": {"limit_micros": 2500000, "period": "daily",
                                          "hard": True, "action": "block"},
                            "agent_daily": None, "run_limit_micros": None},
    }


def disabled():
    snap, exp = snapshot(), expected()
    snap["budgets"] = {"admission_caps_enabled": False, "policy_revision": 12}
    exp["applicability"]["policy_state"] = "disabled"
    exp["applicability"]["key_daily"] = "not_applicable"
    exp["activations"]["key_daily"] = {"state": "not_applicable"}
    exp["policy_relation"]["key_daily"] = None
    return snap, exp
