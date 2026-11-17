"""Settlement drain: the gateway's per-org outbox into the managed credit ledger.

Every 30 seconds (``tfc/temporal/schedules/agentcc_managed.py``) the drain
reads each org's ``mc:{<org>}:outbox`` stream through the consumer group
``django-settlement`` and handles each ``managed-settlement-v1`` record:

- ``spend``: one ledger entry through BL-LEDGER-2A's
  ``settle_spend(record, workspace_id=...)``, keyed ``spend:<request_id>``, so
  a redelivered record writes nothing. The workspace is the request-log row's
  (managed-settlement-v1 has none), or None. A managed capability call
  settles one record per attempt (``<request_id>.<n>``), passed on as sent. A
  spend whose ``markup_bps`` is not the catalog markup
  (``expected_markup_bps``, invariant I10) is still settled, because the
  gateway already charged it, and is logged as an error and counted.
- ``void``: no entry, only a count.
- anything else, or a spend the ledger refuses for any reason: moved to
  ``mc:{<org>}:dead`` with the error, so it never holds back the org's later
  spends. A database that is unreachable is not the record's fault: the run
  stops and the entry stays pending.

The spend's usage event is the ledger's, not the drain's: ``settle_spend``
writes the ``gateway_managed_spend`` ``UsageEventLog`` row in the same
transaction as the entry (event id ``uuid5(NAMESPACE_URL, "agentcc:spend:" +
request_id)``, already rated to the entry, I1), so the drain never emits one.

An entry is acknowledged only after its ledger write committed. One left
pending by a crash is reclaimed (``XAUTOCLAIM``) after 300 seconds idle. After
each org's pass its stream is trimmed to about 7 days, never past an entry that
is pending or not yet delivered. Last, every org the gateway found without an
account hash (``mc:needs_push``) is re-pushed, after this run's spend is in the
ledger so the pushed floor includes it. Money never depends on the request log.
"""

import random
import re
import time
from dataclasses import asdict, dataclass

import redis
import structlog
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import InterfaceError, OperationalError, transaction

from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services.managed_credits import ensure_account_pushed, settle_spend

logger = structlog.get_logger(__name__)

GROUP = "django-settlement"
CONSUMER = "django-drain"
OUTBOX_ORGS = "mc:outbox_orgs"
NEEDS_PUSH = "mc:needs_push"
BATCH = 500
CLAIM_IDLE_MS = 300_000
RETAIN_MS = 7 * 24 * 3600 * 1000
PUSH_BATCH = 100
# The schedule fires every 30 s and skips a run while one is still going.
RUN_DEADLINE_S = 25.0
# Fields a dead-lettered record gains; managed-settlement-v1 has neither.
DEAD_ERROR = "drain_error"
DEAD_SOURCE = "outbox_id"
_ERROR_CHARS = 500
# The catalog markup in basis points: managed.markup_percent 5 is 500
# (internal/config/config.go). AGENTCC_MANAGED_MARKUP_BPS overrides it once
# the price catalog names another markup.
DEFAULT_MARKUP_BPS = 500
# A managed capability attempt's request id is <request_id>.<n>.
_ATTEMPT_SUFFIX = re.compile(r"^(?P<base>.+)\.\d+$")
# The database is down or the connection was lost: retry the record later.
_DATABASE_UNAVAILABLE = (OperationalError, InterfaceError)


def outbox_key(org_id: str) -> str:
    return f"mc:{{{org_id}}}:outbox"


def dead_key(org_id: str) -> str:
    return f"mc:{{{org_id}}}:dead"


def expected_markup_bps() -> int:
    """The markup every gateway SPEND entry must carry (invariant I10)."""
    return int(getattr(settings, "AGENTCC_MANAGED_MARKUP_BPS", DEFAULT_MARKUP_BPS))


def _workspace_id(org_id: str, request_id) -> str | None:
    """The workspace of the request-log row for the request, if any.

    managed-settlement-v1 has no workspace field (usage-event-v1 section 8). A
    capability attempt's ``<request_id>.<n>`` is looked up by its base id. A
    record without a request id has none (``settle_spend`` refuses it).
    """
    from agentcc.models.request_log import AgentccRequestLog

    if not isinstance(request_id, str) or not request_id:
        return None
    request_ids = [request_id]
    attempt = _ATTEMPT_SUFFIX.match(request_id)
    if attempt:
        request_ids.append(attempt.group("base"))
    workspace_ids = (
        AgentccRequestLog.no_workspace_objects.filter(
            organization_id=org_id,
            request_id__in=request_ids,
            workspace__isnull=False,
        )
        .order_by("created_at")
        .values_list("workspace_id", flat=True)
    )
    found = workspace_ids.first()
    return str(found) if found else None


@dataclass
class DrainCounts:
    orgs: int = 0
    org_errors: int = 0
    settled: int = 0
    redelivered: int = 0
    voids: int = 0
    dead: int = 0
    markup_drift: int = 0
    trimmed: int = 0
    pushed: int = 0
    push_failed: int = 0
    push_unknown_org: int = 0


def drain_managed_settlements(
    client: redis.Redis | None = None, *, deadline_s: float = RUN_DEADLINE_S
) -> dict[str, int]:
    """Run the drain once; return what it did.

    ``client`` defaults to Django's Redis (``REDIS_URL``), which must be the
    gateway's billing Redis. It needs ``decode_responses=True``. An org's pass
    stops reading at ``deadline_s`` after its first batch; orgs not reached are
    drained by the next run.
    """
    # Each entry commits in a durable block before it is acknowledged; inside a
    # caller's transaction that block raises, so check before reading anything.
    with transaction.atomic(durable=True):
        pass
    own_client = client is None
    if own_client:
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    run = _Run(client, deadline=time.monotonic() + deadline_s)
    try:
        run.drain_outboxes()
        run.service_needs_push()
    finally:
        if own_client:
            client.close()
    counts = asdict(run.counts)
    logger.info("managed_settlement_drain_completed", **counts)
    return counts


class _Run:
    """One drain run over one Redis connection."""

    def __init__(self, client: redis.Redis, *, deadline: float):
        self.client = client
        self.deadline = deadline
        self.counts = DrainCounts()
        self.markup_bps = expected_markup_bps()

    def past_deadline(self) -> bool:
        return time.monotonic() >= self.deadline

    # --- outboxes ---------------------------------------------------------

    def drain_outboxes(self) -> None:
        org_ids = self._org_ids()
        # A random order, so a backlog that outlasts a run never starves the same orgs.
        random.shuffle(org_ids)
        for org_id in org_ids:
            self.counts.orgs += 1
            try:
                self._drain_org(org_id)
            except redis.ResponseError:
                # One org's stream refusing a command (deleted mid-run, or not a
                # stream) must not hold back every other org's spend; its
                # entries stay pending for the next run.
                logger.exception("managed_settlement_org_failed", org_id=org_id)
                self.counts.org_errors += 1
            if self.past_deadline():
                break

    def _org_ids(self) -> list[str]:
        hinted = self.client.smembers(OUTBOX_ORGS)
        accounts = ManagedCreditAccount.objects.values_list(
            "organization_id", flat=True
        )
        return sorted(set(hinted) | {str(org_id) for org_id in accounts})

    def _drain_org(self, org_id: str) -> None:
        stream = outbox_key(org_id)
        try:
            self.client.xgroup_create(stream, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        while True:
            reply = self.client.xreadgroup(GROUP, CONSUMER, {stream: ">"}, count=BATCH)
            entries = reply[0][1] if reply else []
            self._handle_all(org_id, entries)
            if len(entries) < BATCH or self.past_deadline():
                break
        start = "0-0"
        while True:
            start, claimed, _deleted = self.client.xautoclaim(
                stream, GROUP, CONSUMER, CLAIM_IDLE_MS, start_id=start, count=BATCH
            )
            self._handle_all(org_id, claimed)
            if start == "0-0" or self.past_deadline():
                break
        self._trim(stream)

    def _handle_all(self, org_id: str, entries: list) -> None:
        for entry_id, fields in entries:
            if fields is not None:  # an entry deleted while it was pending
                self._handle(org_id, entry_id, fields)

    def _handle(self, org_id: str, entry_id: str, fields: dict[str, str]) -> None:
        version, kind = fields.get("v"), fields.get("kind")
        if version != "1" or kind not in ("spend", "void"):
            self._dead_letter(
                org_id,
                entry_id,
                fields,
                f"not a managed-settlement-v1 record: v={version!r} kind={kind!r}",
            )
            return
        if kind == "void":
            self.counts.voids += 1
            self._ack(org_id, entry_id)
            return
        if fields.get("org_id") != org_id:
            self._dead_letter(
                org_id,
                entry_id,
                fields,
                f"org_id {fields.get('org_id')!r} is not the outbox's org",
            )
            return
        try:
            # Committed when the block exits, so the XACK below follows the
            # commit. settle_spend writes the spend's usage event in the same
            # transaction; the drain emits none (BL-LEDGER-2A interface).
            with transaction.atomic(durable=True):
                workspace_id = _workspace_id(org_id, fields.get("request_id"))
                entry, created = settle_spend(fields, workspace_id=workspace_id)
        except _DATABASE_UNAVAILABLE:
            logger.exception(
                "managed_settlement_database_unavailable",
                org_id=org_id,
                outbox_id=entry_id,
            )
            raise
        except Exception as exc:  # any refusal: one bad record never blocks the org
            self._dead_letter(
                org_id, entry_id, fields, f"{type(exc).__name__}: {exc}", exc=exc
            )
            return
        if created:
            self.counts.settled += 1
            self._check_markup(org_id, entry)
        else:
            self.counts.redelivered += 1
            billed = int(fields["billed_micros"])  # settle_spend checked it
            if billed != -entry.amount_micros:
                logger.error(
                    "managed_settlement_redelivery_differs",
                    org_id=org_id,
                    request_id=entry.request_id,
                    billed_micros=billed,
                    ledger_micros=-entry.amount_micros,
                )
        self._ack(org_id, entry_id)

    def _check_markup(self, org_id: str, entry: ManagedCreditEntry) -> None:
        """I10: flag a newly settled spend whose markup is not the catalog's."""
        if entry.markup_bps == self.markup_bps:
            return
        self.counts.markup_drift += 1
        logger.error(
            "managed_settlement_markup_drift",
            org_id=org_id,
            request_id=entry.request_id,
            markup_bps=entry.markup_bps,
            expected_bps=self.markup_bps,
        )

    def _ack(self, org_id: str, entry_id: str) -> None:
        self.client.xack(outbox_key(org_id), GROUP, entry_id)

    def _dead_letter(
        self,
        org_id: str,
        entry_id: str,
        fields: dict[str, str],
        error: str,
        exc: Exception | None = None,
    ) -> None:
        error = error[:_ERROR_CHARS]
        logger.error(
            "managed_settlement_dead_lettered",
            org_id=org_id,
            outbox_id=entry_id,
            error=error,
            exc_info=exc,
        )
        pipe = self.client.pipeline(transaction=True)
        pipe.xadd(
            dead_key(org_id), {**fields, DEAD_ERROR: error, DEAD_SOURCE: entry_id}
        )
        pipe.xack(outbox_key(org_id), GROUP, entry_id)
        pipe.execute()
        self.counts.dead += 1

    def _trim(self, stream: str) -> None:
        """Trim acknowledged entries older than 7 days, so the outbox stays bounded.

        The bound is the lowest of now minus 7 days, the oldest pending entry
        and the entry after the group's last delivered one: an entry the group
        has not read yet is neither pending nor settled.
        """
        bounds = [(int(time.time() * 1000) - RETAIN_MS, 0)]
        pending = self.client.xpending(stream, GROUP)
        if pending["pending"]:
            bounds.append(_stream_id(pending["min"]))
        group = next(g for g in self.client.xinfo_groups(stream) if g["name"] == GROUP)
        ms, seq = _stream_id(group["last-delivered-id"])
        bounds.append((ms, seq + 1))
        ms, seq = min(bounds)
        self.counts.trimmed += self.client.xtrim(
            stream, minid=f"{ms}-{seq}", approximate=False
        )

    # --- needs push -------------------------------------------------------

    def service_needs_push(self) -> None:
        """Re-push every org whose account hash the gateway found missing."""
        failed: list[str] = []
        while popped := self.client.spop(NEEDS_PUSH, PUSH_BATCH):
            for org_id in popped:
                if not self._push(org_id):
                    failed.append(org_id)
        # Re-added only now: inside the loop SPOP would hand them straight back.
        if failed:
            self.client.sadd(NEEDS_PUSH, *failed)
        self.counts.push_failed = len(failed)

    def _push(self, org_id: str) -> bool:
        """Push one org; False when it must stay in the set."""
        try:
            org = Organization.objects.get(pk=org_id)
        except (Organization.DoesNotExist, ValidationError):
            # Nothing to push; the gateway adds the org again on its next 503.
            logger.warning("managed_settlement_push_unknown_org", org_id=org_id)
            self.counts.push_unknown_org += 1
            return True
        try:
            state = ensure_account_pushed(org)
        except Exception:
            logger.exception("managed_settlement_push_failed", org_id=org_id)
            return False
        if state is None:  # ensure_account_pushed logged why
            return False
        self.counts.pushed += 1
        return True


def _stream_id(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")
    return int(ms), int(seq or 0)
