"""Shared wire contract for deployment telemetry.

SINGLE SOURCE OF TRUTH for the registration/heartbeat payload shape, the
size/volume caps, and the email-domain derivation. This module's body is
**vendored verbatim** into the receiver repo at
``ee/usage/deployment_telemetry_schema.py`` (only the module docstring,
which names each copy's role, differs). A contract test
(``test_schema_contract``) asserts every public constant in the two copies
agrees and that a payload built here validates on the receiver, so a cap or
field bumped on one side can't silently diverge from the other.

Dependency-free on purpose: no Django, no DRF. Both repos import the same
constants and the same helpers; the builders/serializers wrap them.

Wire-compatibility rule: bump ``SCHEMA_VERSION`` on any change to field
names, caps, or semantics. The receiver rejects payloads whose major
version it does not understand.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# ── Caps (part of the wire contract — both sides must agree) ──
MAX_PAYLOAD_BYTES = 512 * 1024
MAX_REGISTRATION_USERS = 500
MAX_WINDOW_HOURS = 24
MAX_VERSION_LEN = 100
MAX_DEPLOYMENT_TYPE_LEN = 50
MAX_EMAIL_LEN = 254
MAX_DOMAIN_LEN = 253

# Heartbeat counts may exceed 2**31; the receiver column is a 63-bit
# unsigned big integer, so this is the hard ceiling for any count value.
MAX_COUNT_VALUE = 2**63 - 1

# ── Heartbeat count fields ──
# total_evaluations_count is the sum of the three EVAL_COMPONENT_FIELDS.
EVAL_COMPONENT_FIELDS = (
    "eval_logger_count",
    "model_hub_evaluations_count",
    "dataset_eval_runs_count",
)

COUNT_FIELDS = (
    "active_users_count",
    "traces_count",
    "spans_count",
    "projects_count",
    "eval_logger_count",
    "model_hub_evaluations_count",
    "dataset_eval_runs_count",
    "total_evaluations_count",
    "simulation_runs_count",
    "simulation_calls_count",
    "experiments_count",
    "gateway_requests_count",
    "datasets_count",
)

# ── Allowed top-level keys ──
REGISTRATION_FIELDS = frozenset(
    {
        "schema_version",
        "instance_id",
        "version",
        "deployment_type",
        "timestamp",
        "telemetry_disabled",
        "users",
    }
)

HEARTBEAT_FIELDS = frozenset(
    {
        "schema_version",
        "instance_id",
        "version",
        "window_start",
        "window_end",
        *COUNT_FIELDS,
    }
)

USER_FIELDS = frozenset({"email", "domain"})


def derive_domain(email: str) -> str:
    """Return the lowercased domain part of an email address.

    Single definition used by both the sender (when building the payload)
    and the receiver (when validating that ``domain`` matches ``email``),
    so the two can't disagree on how a domain is parsed.
    """
    return email.strip().lower().rsplit("@", 1)[-1]


def expected_total_evaluations(counts: dict) -> int | None:
    """Sum of the three eval component counts, or None if any is null.

    A null component means that collector failed; the total is then
    unknown rather than an understatement.
    """
    components = [counts.get(field) for field in EVAL_COMPONENT_FIELDS]
    if any(component is None for component in components):
        return None
    return sum(components)
