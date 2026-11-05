"""Durable metric dispatch on existing workers; no fleet qualification implied.

METRICS_MAX_CONCURRENT_PROJECTS (positive integer, default 1) is a supported
admission setting, NOT an M2/M3/M11 certified fleet size. The rollout stays off
until those gates pass. A separate, transaction-held PG advisory semaphore
bounds *executing* writers even after a lease expires. Project fencing still
controls publication. Neither a Temporal timeout nor lease expiry kills a
Python thread, so counting unexpired leases alone is not sufficient.

METRICS_RETENTION_DAYS_PROVIDER is the dotted path of the deployment's current
organization-policy callable, taking an organization UUID. There is deliberately
no guessed retention fallback. The native writer requires a single local CH
shard (its existing publication receipts enforce that contract).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from time import monotonic
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from django.db.models import (
    BooleanField,
    DateTimeField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Least, Power
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.models.derived_metric import (
    DerivedMetricVersion,
    LeaseGrant,
    MetricFencingError,
    MetricProjectRefresh,
    MetricRefreshAttempt,
)
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.services.derived_metrics.lease import DEFAULT_LEASE_TTL, ProjectLease
from tracer.services.derived_metrics.refresh import RefreshWriter

DISPATCH_SECONDS = 60
RUN_NOW_THROTTLE = timedelta(seconds=60)
# Retry policy, not throughput/cadence qualification. Cursors never move here.
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 900
_LOCK_NAMESPACE = 0x4D455452
_DISPATCH_LOCK = -1


@dataclass(frozen=True)
class Reservation:
    grant: LeaseGrant
    purpose: str
    requested_at: datetime | None

    def payload(self) -> dict:
        return {
            "project_id": str(self.grant.project_id),
            "epoch": self.grant.fencing_epoch,
            "token": str(self.grant.lease_token),
            "expires_at": self.grant.expires_at.isoformat(),
            "purpose": self.purpose,
            "requested_at": self.requested_at.isoformat()
            if self.requested_at
            else None,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "Reservation":
        purpose = payload["purpose"]
        if purpose not in {"incremental", "backfill", "repair"}:
            raise ValueError("unknown metric dispatch purpose")
        return cls(
            LeaseGrant(
                UUID(payload["project_id"]),
                int(payload["epoch"]),
                UUID(payload["token"]),
                datetime.fromisoformat(payload["expires_at"]),
            ),
            purpose,
            datetime.fromisoformat(payload["requested_at"])
            if payload.get("requested_at")
            else None,
        )


def concurrent_projects() -> int:
    value = getattr(settings, "METRICS_MAX_CONCURRENT_PROJECTS", 1)
    if type(value) is not int or value < 1:
        raise ImproperlyConfigured("METRICS_MAX_CONCURRENT_PROJECTS must be positive")
    return value


def reconciliation_interval() -> timedelta:
    """Configurable coverage-age trigger, NOT a measured completion guarantee.

    Reuse the design's proposed bound; admission/coverage throughput still needs
    M2/M3/M11 qualification. A pending sweep never waits for this interval.
    """
    from tracer.services.derived_metrics.changes import PROPOSED_REPAIR_BOUND

    value = getattr(
        settings,
        "METRICS_RECONCILIATION_INTERVAL_SECONDS",
        int(PROPOSED_REPAIR_BOUND.total_seconds()),
    )
    if type(value) is not int or value < 1:
        raise ImproperlyConfigured(
            "METRICS_RECONCILIATION_INTERVAL_SECONDS must be positive"
        )
    return timedelta(seconds=value)


def eligible_projects(now: datetime):
    """Only small PG control rows; unfinished work bypasses last_success_at."""
    repair_cutoff = now - reconciliation_interval()
    active = DerivedMetricVersion.objects.filter(
        metric__project_id=OuterRef("project_id"),
        metric__deleted=False,
        id=F("metric__active_version_id"),
        pins_revoked_at__isnull=True,
    )
    recovery = MetricRefreshAttempt.objects.filter(
        project_id=OuterRef("project_id"), state__in=MetricRefreshAttempt.OPEN_STATES
    )
    retry_delay = Least(
        BACKOFF_BASE_SECONDS * Power(2, Least(F("consecutive_failures"), 5) - 1),
        BACKOFF_MAX_SECONDS,
        output_field=FloatField(),
    )
    return (
        MetricProjectRefresh.objects.annotate(
            incremental_due=ExpressionWrapper(
                F("incremental_last_success_at")
                + ExpressionWrapper(
                    F("cadence_seconds") * timedelta(seconds=1),
                    output_field=DurationField(),
                ),
                output_field=DateTimeField(),
            ),
            retry_due=ExpressionWrapper(
                F("last_error_at")
                + ExpressionWrapper(
                    retry_delay * timedelta(seconds=1), output_field=DurationField()
                ),
                output_field=DateTimeField(),
            ),
            recovery_pending=Exists(recovery),
            has_active_definition=Exists(active),
            # Coverage is through the sweep's START, not its completion time
            # (nor the row's invalidation token). Legacy complete cursors may
            # lack covered_through but still record that actual source ceiling.
            repair_covered_through=Cast(
                Coalesce(
                    KeyTextTransform("covered_through", "reconciliation_cursor"),
                    KeyTextTransform("started_at", "reconciliation_cursor"),
                ),
                output_field=DateTimeField(),
            ),
        )
        .annotate(
            routine_repair_due=ExpressionWrapper(
                Q(has_active_definition=True)
                & (
                    Q(reconciliation_completed_at__isnull=True)
                    | Q(repair_covered_through__isnull=True)
                    | Q(repair_covered_through__lte=repair_cutoff)
                ),
                output_field=BooleanField(),
            )
        )
        .filter(project__deleted=False)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
        .filter(
            Q(consecutive_failures=0)
            | Q(last_error_at__isnull=True)
            | Q(retry_due__lte=now)
        )
        .filter(
            Q(incremental_last_success_at__isnull=True)
            | Q(incremental_due__lte=now)
            | Q(refresh_requested_at__isnull=False)
            # A publication may succeed before its cleanup fails. The durable
            # failure itself must keep that remainder eligible after backoff.
            | Q(consecutive_failures__gt=0)
            | Q(pending_backfill=True)
            | Q(pending_repair=True)
            | Q(routine_repair_due=True)
            | Q(pending_expiry=True)
            | Q(pending_erasure=True)
            | Q(recovery_pending=True)
            | Q(head_manifest__published_by__source_ceilings__recovery_pending=True)
            | Q(next_due_at__lte=now)
        )
        # Least recently admitted first, not oldest success: endless chunks and
        # failing projects must move to the back of the same fair queue.
        .order_by(Coalesce("last_started_at", "created_at"), "project_id")
    )


def reserve_projects(*, clock: Callable = timezone.now) -> list[Reservation]:
    if not metrics_enabled():
        return []
    now, limit = clock(), concurrent_projects()
    with transaction.atomic():
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                [_LOCK_NAMESPACE, _DISPATCH_LOCK],
            )
            if not cursor.fetchone()[0]:
                return []
        free = max(
            0,
            limit
            - MetricProjectRefresh.objects.filter(lease_expires_at__gt=now).count(),
        )
        reservations = []
        # Never hold a refresh row then acquire its project serialization lock:
        # the publication protocol acquires these locks in the opposite order.
        for state in eligible_projects(now)[:free]:
            lanes = ("incremental", "backfill", "repair")
            start = state.fencing_epoch % len(lanes)
            purpose = next(
                lane
                for lane in lanes[start:] + lanes[:start]
                if lane == "incremental"
                or (lane == "backfill" and state.pending_backfill)
                or (
                    lane == "repair"
                    and (state.pending_repair or state.routine_repair_due)
                )
            )
            grant = MetricProjectRefresh.take_lease(
                state.project_id,
                holder=f"metrics:queued:{purpose}",
                ttl=DEFAULT_LEASE_TTL,
                now=now,
            )
            if grant is None:
                continue
            updates = {"last_started_at": now}
            if purpose == "repair":
                # take_lease holds the project serialization lock until this
                # transaction commits. Persist eligibility before enqueue, but
                # leave coverage and its CAS token to the existing writer.
                updates["pending_repair"] = True
            MetricProjectRefresh.objects.filter(project_id=state.project_id).update(
                **updates
            )
            reservations.append(Reservation(grant, purpose, state.refresh_requested_at))
        return reservations


def request_refresh(project_id: UUID, *, clock: Callable = timezone.now) -> bool:
    """Persist Run now, coalescing and throttling concurrent requests in PG.

    The request remains until a successful pass acknowledges *that timestamp*.
    last_started_at continues the throttle after acknowledgment. This is a
    service seam for the API's existing definition/permission admission.
    """
    if not metrics_enabled(project=project_id):
        return False
    from tracer.services.derived_metrics.deletion_protocol import serialized_project

    now = clock()
    with serialized_project(project_id):
        state = MetricProjectRefresh.for_project(project_id)
        if state.refresh_requested_at is not None or (
            state.last_started_at and now < state.last_started_at + RUN_NOW_THROTTLE
        ):
            return False
        MetricProjectRefresh.objects.filter(pk=state.pk).update(
            refresh_requested_at=now
        )
    return True


def record_failure(reservation: Reservation, *, now: datetime, code: str) -> None:
    # Only fixed internal codes, never exception messages or private payloads.
    if code not in {"metric_dispatch_failed", "metric_refresh_failed"}:
        raise ValueError("unsupported metric failure code")
    grant = reservation.grant
    MetricProjectRefresh.objects.filter(
        project_id=grant.project_id,
        fencing_epoch=grant.fencing_epoch,
        lease_token=grant.lease_token,
    ).update(
        last_error_at=now,
        last_error_code=code,
        consecutive_failures=F("consecutive_failures") + 1,
    )


def release(reservation: Reservation, *, now: datetime) -> None:
    grant = reservation.grant
    MetricProjectRefresh.release_lease(
        grant.project_id, epoch=grant.fencing_epoch, token=grant.lease_token, now=now
    )


def dispatch(enqueue: Callable[[dict], None], *, clock: Callable = timezone.now) -> int:
    count = 0
    for reservation in reserve_projects(clock=clock):
        try:
            if not metrics_enabled():
                release(reservation, now=clock())
                continue
            enqueue(reservation.payload())
            count += 1
        except Exception:
            # Ambiguous enqueue is safe: the old token cannot start after its
            # release. If the worker already claimed it, leave its lease alone.
            with transaction.atomic():
                state = MetricProjectRefresh.objects.select_for_update().get(
                    project_id=reservation.grant.project_id
                )
                if state.lease_holder == f"metrics:queued:{reservation.purpose}":
                    record_failure(
                        reservation, now=clock(), code="metric_dispatch_failed"
                    )
                    release(reservation, now=clock())
    return count


@contextmanager
def execution_slot(project_id: UUID) -> Iterator[bool]:
    """Independent transaction locks survive lease expiry and work with PgBouncer.

    Never use the ORM transaction: publications must commit while these locks
    remain held. Connection loss releases the locks; publication still needs a
    current fenced lease. No slot table or production schema change is needed.
    """
    database = connections["default"].copy(alias="metric_execution_lock")
    try:
        database.set_autocommit(False)
        with database.cursor() as cursor:
            # Separate namespace from lifecycle/deletion serialization locks.
            key = int.from_bytes(
                sha256(str(project_id).encode()).digest()[:4], "big", signed=True
            )
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)", [_LOCK_NAMESPACE + 1, key]
            )
            if not cursor.fetchone()[0]:
                yield False
                return
            for slot in range(concurrent_projects()):
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(%s, %s)", [_LOCK_NAMESPACE, slot]
                )
                if cursor.fetchone()[0]:
                    yield True
                    return
        yield False
    finally:
        database.rollback()
        database.close()


def execute_reservation(
    payload: dict, *, clock: Callable = timezone.now, runner: Callable | None = None
) -> bool:
    reservation = Reservation.from_payload(payload)
    lease = ProjectLease(reservation.grant, clock=clock)
    if not metrics_enabled(project=lease.project_id):
        release(reservation, now=clock())
        return False
    with execution_slot(lease.project_id) as admitted:
        if not admitted:
            # No work ran. Return only a still-queued reservation to the next
            # tick; a duplicate delivery must never release a running writer.
            MetricProjectRefresh.objects.filter(
                project_id=lease.project_id,
                fencing_epoch=reservation.grant.fencing_epoch,
                lease_token=reservation.grant.lease_token,
                lease_holder=f"metrics:queued:{reservation.purpose}",
            ).update(lease_token=None, lease_expires_at=None, lease_holder="")
            return False
        claimed = MetricProjectRefresh.objects.filter(
            MetricProjectRefresh.lease_q(**lease.fence()),
            project_id=lease.project_id,
            lease_holder=f"metrics:queued:{reservation.purpose}",
        ).update(lease_holder=f"metrics:running:{reservation.purpose}")
        if not claimed:
            return False
        try:
            lease.renew()
            if not metrics_enabled(project=lease.project_id):
                return False
            (runner or run_project_pass)(lease, reservation.purpose)
            # The writer alone advances work flags, cursors and manifest. A
            # successful backfill/repair is not proof of incremental catch-up.
            updates = {"consecutive_failures": 0, "last_error_code": ""}
            with transaction.atomic():
                state = MetricProjectRefresh._locked(lease.project_id, **lease.check())
                if state.refresh_requested_at == reservation.requested_at:
                    updates["refresh_requested_at"] = None
                MetricProjectRefresh.objects.filter(pk=state.pk).update(**updates)
            return True
        except MetricFencingError:
            return False
        except Exception:
            record_failure(reservation, now=clock(), code="metric_refresh_failed")
            raise
        finally:
            lease.release()


class NativeMetricAnalytics:
    """Keep B04's per-statement caps; interactive analytics strips these caps."""

    supports_per_query_read_settings = True

    def __init__(self, client):
        self.client = client

    def execute_ch_query(
        self, query, params, *, timeout_ms, settings, server_execution_cap_ms=None
    ):
        from tracer.services.clickhouse.query_service import QueryResult

        started = monotonic()
        rows, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings={
                **settings,
                "max_execution_time": max(
                    0.001, min(timeout_ms, server_execution_cap_ms or timeout_ms) / 1000
                ),
            },
        )
        names = [name for name, _ in columns]
        progress = self.client.last_query.progress
        return QueryResult(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=(monotonic() - started) * 1000,
            columns=names,
            read_rows=progress.rows,
            read_bytes=progress.bytes,
        )


class _ScheduledRefreshWriter(RefreshWriter):
    """Bind the project policy even when B09 calls refresh with default kwargs."""

    def __init__(self, *args, settle_minutes: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.settle_minutes = settle_minutes

    def refresh(self, *args, **kwargs):
        kwargs.setdefault("settle_minutes", self.settle_minutes)
        return super().refresh(*args, **kwargs)


def run_project_pass(lease: ProjectLease, purpose: str) -> None:
    """One bounded history page or one incremental/due-settle publication."""
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config
    from tracer.services.derived_metrics import backfill
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore
    from tracer.services.derived_metrics.queries import Scope

    provider = getattr(settings, "METRICS_RETENTION_DAYS_PROVIDER", "")
    if not provider:
        raise ImproperlyConfigured(
            "metrics requires a current organization retention provider"
        )
    policy = import_string(provider)
    state = MetricProjectRefresh.objects.select_related("project").get(
        project_id=lease.project_id
    )
    scope = Scope(
        state.project.organization_id, state.project.workspace_id, lease.project_id
    )
    config = get_v2_config()
    client = Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=10,
        send_receive_timeout=60,
    )
    try:
        store = ClickHousePublicationStore(client)
        writer = _ScheduledRefreshWriter(
            lease,
            store,
            settle_minutes=state.settle_minutes,
            org_id=scope.organization,
            workspace_id=scope.workspace,
            retention_days=lambda: policy(scope.organization),
        )
        # Mark a conservative due hint BEFORE writing. A crash after publication
        # but before the next-due read must not hide newly opened queue rows.
        with transaction.atomic():
            locked = MetricProjectRefresh._locked(lease.project_id, **lease.check())
            MetricProjectRefresh.objects.filter(pk=locked.pk).update(
                next_due_at=lease.clock()
            )
        analytics = NativeMetricAnalytics(client)
        runner = backfill.BackfillRunner(writer, analytics, scope)
        if (
            purpose == "backfill"
            and backfill.pending_versions(lease.project_id).exists()
        ):
            runner.run_pass(max_chunks=1)
        elif purpose == "repair" and state.pending_repair:
            runner.run_repair_page()
        else:
            run_incremental(writer, analytics, scope, state)
        writer.collect_garbage()
    finally:
        client.disconnect()


def run_incremental(writer, analytics, scope, state) -> None:
    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.publication import committed_rows_select
    from tracer.services.derived_metrics.storage import UNIT_STATE

    now = writer.lease.clock()
    definitions = backfill.working_definitions(scope.project)
    try:
        batch = backfill.incremental_changes(
            analytics, writer, state.head_manifest, definitions, now=now
        )
    except changes.arrivals.ContinuousCandidateOverflow:
        # The same unconsumed interval overflows again on incremental turns.
        # Do not invalidate a sweep already covering it: only a complete repair
        # may consume that sweep's start ceiling. Read under the current fence,
        # not from the potentially stale state captured before discovery.
        with transaction.atomic():
            current = MetricProjectRefresh._locked(
                scope.project, **writer.lease.check()
            )
            if not current.pending_repair:
                backfill.queue_bulk_change(scope.project, now=now)
        backfill.BackfillRunner(writer, analytics, scope).run_repair_page(
            skip_incremental=True
        )
        return
    # Read the committed queue, not physical stale nominations. The existing
    # writer recomputes source arrival and uses the committed arrival as a floor.
    # No lower time bound: a long scheduler outage never drops overdue units.
    sql, params = committed_rows_select(
        UNIT_STATE,
        state.head_manifest,
        org_id=scope.organization,
        project_id=scope.project,
    )
    due = writer.store.execute(
        f"SELECT level, unit_id FROM ({sql}) WHERE is_deleted = 0 AND due_at <= %(due_now)s ORDER BY due_at, level, unit_id LIMIT 1000",
        {**params, "due_now": now},
    )
    batch = replace(
        batch,
        units=tuple(sorted(set(batch.units) | {changes.UnitKey(*row) for row in due})),
        cursor_updates={**batch.cursor_updates, "incremental_last_success_at": now},
    )
    head = writer.refresh(
        batch,
        definitions,
        scope,
        start=now - timedelta(days=backfill.MAX_HISTORY_DAYS),
        end=now,
        as_of=now,
        settle_minutes=state.settle_minutes,
    )
    sql, params = committed_rows_select(
        UNIT_STATE, head, org_id=scope.organization, project_id=scope.project
    )
    remaining = writer.store.execute(
        f"SELECT minOrNull(due_at) FROM ({sql}) WHERE is_deleted = 0", params
    )[0][0]
    # If this query fails, the old due hint stays and the retry is durable.
    with transaction.atomic():
        locked = MetricProjectRefresh._locked(scope.project, **writer.lease.check())
        MetricProjectRefresh.objects.filter(pk=locked.pk).update(next_due_at=remaining)
