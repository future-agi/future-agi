"""Runtime ledger status, never config: cached for reads, fresh on activation."""

from hashlib import sha256

from django.core.cache import cache
from django.db import transaction
from rest_framework.response import Response

from agentcc.contracts.gateway_admin import BudgetsConfig
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.gateway_client import get_gateway_client

LEDGER_READ_TTL = 60
LEDGER_STATUSES = ("ready", "absent", "unavailable", "unknown")
# No gateway reports the spend ledger yet, so a save that switches daily caps
# on is refused with this. A restore never is (org_config_writer).
LEDGER_REQUIRED_MESSAGE = (
    "Daily caps can't be turned on in this release. "
    "Budgets in Gateway > Budgets still limit spend."
)


class AdmissionCapLedgerUnavailable(OrgConfigRejected):
    code = "admission_cap_ledger_unavailable"

    def __init__(self, ledger_status):
        self.ledger_status = (
            ledger_status if ledger_status in ("absent", "unavailable") else "unknown"
        )
        super().__init__(LEDGER_REQUIRED_MESSAGE)

    def response(self):
        return Response(
            {
                "code": self.code,
                "message": LEDGER_REQUIRED_MESSAGE,
                "ledger_status": self.ledger_status,
            },
            status=409,
        )


def _cache_key(org_id, gateway_url):
    # Fixed length; no gateway URL, credentials or org identifier in cache keys.
    scope = f"{org_id}\n{gateway_url.rstrip('/')}".encode()
    return f"agentcc:cap-ledger:v1:{sha256(scope).hexdigest()}"


def remember_ledger_status(org_id, gateway_url, status):
    """Read-side optimization only; cache failure never changes a write's outcome."""
    if status not in LEDGER_STATUSES:
        return
    try:
        cache.set(_cache_key(org_id, gateway_url), status, timeout=LEDGER_READ_TTL)
    except Exception:
        pass


def get_ledger_status(org_id, *, fresh=False, require_autocommit=False):
    # Generic updater callbacks cannot be previewed without executing their
    # side effects. Already-transactional callers may hold an org lock: never
    # perform speculative I/O for those callers, even if a cached ready exists.
    if require_autocommit and not transaction.get_autocommit():
        return "unknown"
    try:
        client = get_gateway_client()
    except Exception:
        return "unknown"
    if not fresh:
        try:
            cached = cache.get(_cache_key(org_id, client.base_url))
            if cached in LEDGER_STATUSES:
                return cached
        except Exception:
            pass
    try:
        status = client.admission_cap_ledger_status()
    except Exception:
        status = "unknown"
    remember_ledger_status(org_id, client.base_url, status)
    return status


def caps_enabled(config):
    budgets = BudgetsConfig._normalize_input_aliases(
        config.budgets or {} if config is not None else {}
    )
    return isinstance(budgets, dict) and budgets.get("admission_caps_enabled") is True


def preflight_cap_ledger_for_activation(active, candidate):
    """An unlocked preview; the writer must recheck the transition under lock."""
    if not caps_enabled(candidate) or caps_enabled(active):
        return "unknown"
    return get_ledger_status(candidate.organization_id, fresh=True)


def require_cap_ledger_for_activation(active, candidate, *, ledger_status="unknown"):
    """Under the org lock, use only the preflight; never start network I/O here."""
    if not caps_enabled(candidate) or caps_enabled(active):
        return
    # A transition discovered only after locking has no fresh observation and
    # must refuse unknown rather than probing while it holds the org lock.
    if ledger_status != "ready":
        raise AdmissionCapLedgerUnavailable(ledger_status)
