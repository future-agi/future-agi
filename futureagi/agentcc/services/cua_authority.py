"""Internal H03 person/key authority check, not a public authorization API.

Only the future authenticated gateway transport may call this service. Selectors
are comparison inputs, not credentials. This result alone cannot authorize a
native operation, mint an admission or qualify any stream: those checks remain
with the gateway's sealed ExpectedAdmission and monotonic lease owner.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import UUID

from agentcc.services.cua_authority_policy import (
    AuthorityDenied, AuthorityUnavailable, compare_policy, observe_policy,
    utc_stamp, validate_expected,
)
from agentcc.services.cua_authority_primary import read_primary

_SUBJECT = {"kind", "key_id", "org_id", "user_id", "workspace_id"}
_REQUIREMENTS = {"root_run", "budget_scope", "caller_run_scope"}


def _now():
    return datetime.now(timezone.utc)


def _validate_subject(subject, requirements):
    if type(subject) is not dict or set(subject) != _SUBJECT:
        raise AuthorityUnavailable()
    if subject["kind"] != "personal_key":
        raise AuthorityUnavailable()
    key = subject["key_id"]
    if type(key) is not str or not 1 <= len(key) <= 128:
        raise AuthorityUnavailable()
    for field in ("org_id", "user_id", "workspace_id"):
        value = subject[field]
        if type(value) is not str or str(UUID(value)) != value:
            raise AuthorityUnavailable()
    if type(requirements) is not dict or set(requirements) != _REQUIREMENTS:
        raise AuthorityUnavailable()
    # Unknown, absent and unsupported are NOT equivalent to not-applicable.
    if any(value is not False for value in requirements.values()):
        raise AuthorityUnavailable()


def check_personal_authority(subject, expected_admission, requirements):
    """Fresh primary comparison with closed value-free negative outcomes.

    The eventual RPC layer must bind schema/challenge/phase/operation and accept
    input only over approved service authentication/TLS. It must not mount this
    function as an end-user endpoint. No URL is registered by this slice.
    Gateway freshness still starts BEFORE its RPC; these FA bounds never reset
    a gateway lease on response arrival and do not prove zero revocation race.
    """
    try:
        subject, expected, requirements = deepcopy((subject, expected_admission, requirements))
        _validate_subject(subject, requirements)
        validate_expected(expected)
        start = monotonic()
        checked_at = _now()
        utc_stamp(checked_at)
        snapshot = read_primary(subject)
        if snapshot["subject"] != subject:
            raise AuthorityDenied()
        observed = observe_policy(snapshot)
        if compare_policy(expected, observed) != "match":
            raise AuthorityDenied()
        expires = snapshot["expires_at"]
        valid_until = checked_at + timedelta(seconds=1)
        if expires is not None:
            utc_stamp(expires)
            valid_until = min(valid_until, expires)
        completed = _now()
        utc_stamp(completed)
        if expires is not None and expires <= completed:
            raise AuthorityDenied()
        elapsed = monotonic() - start
        if not 0 <= elapsed < 1 or completed < checked_at or completed >= valid_until:
            raise AuthorityUnavailable()
        comparison = {"central_admission_id": expected["central_admission_id"], "result": "match"}
        for field in ("policy_revision", "applicability", "activations", "policy_relation"):
            comparison["expected_" + field] = deepcopy(expected[field])
        return {
            "result": "allow", "subject": deepcopy(snapshot["subject"]),
            "checked_at": utc_stamp(checked_at),
            "credential_expires_at": utc_stamp(expires) if expires is not None else None,
            "valid_until": utc_stamp(valid_until),
            "observed_policy": observed, "admission_comparison": comparison,
        }
    except AuthorityDenied:
        return {"result": "deny"}
    except Exception:
        # DB/parse/import/unavailable-model failures must never echo identities,
        # policy contents, credentials or exception details into errors/logs.
        return {"result": "unavailable"}
