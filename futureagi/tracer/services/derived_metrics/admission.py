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
    CharField,
    DateTimeField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Concat, Least, Power
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.models.derived_metric import (
    DerivedMetricVersion,
    LeaseGrant,
    MetricErasureRequest,
    MetricFencingError,
    MetricProjectRefresh,
    MetricRefreshAttempt,
)
from tracer.models.project import Project
from tracer.services.derived_metrics.deletion_protocol import (
    lifecycle_lock_budget,
    project_lock,
    try_control_update,
)
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.services.derived_metrics.lease import (
    DEFAULT_LEASE_TTL,
    MetricPassDeadline,
    ProjectLease,
)
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.services.derived_metrics.retention import expiry_interval
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)

DISPATCH_SECONDS = 60
RUN_NOW_THROTTLE = timedelta(seconds=60)
# Retry policy, not throughput/cadence qualification. Cursors never move here.
BACKOFF_BASE_SECONDS = 60
# One pass's budget: the activity's 600 s timeout less a minute to unwind.
PASS_SECONDS = 540
# Longer than a pass, so a paused project's expiry pass never finds the last
# expiry's floor still within its interval (physical_cutoff).
PAUSED_EXPIRY_SLACK = timedelta(minutes=15)
BACKOFF_MAX_SECONDS = 900
_LOCK_NAMESPACE = 0x4D455452
_DISPATCH_LOCK = -1


class RefreshAdmissionUnavailable(Exception):
    """No queue row could be locked; the request was not acknowledged."""


@dataclass(frozen=True)
class Reservation:
    grant: LeaseGrant
    purpose: str
    requested_at: datetime | None
    expiry_id: UUID | None = None

    @property
    def job(self) -> str:
        # Bind the queued/running holder to the durable operation, not just its lane.
        return f"cleanup:{self.expiry_id}" if self.expiry_id else self.purpose

    def payload(self) -> dict:
        return {
            "project_id": str(self.grant.project_id),
            "epoch": self.grant.fencing_epoch,
            "token": str(self.grant.lease_token),
            "expires_at": self.grant.expires_at.isoformat(),
            "purpose": self.purpose,
            **({"expiry_id": str(self.expiry_id)} if self.expiry_id else {}),
            "requested_at": self.requested_at.isoformat()
            if self.requested_at
            else None,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "Reservation":
        purpose = payload["purpose"]
        if purpose not in {"incremental", "backfill", "repair", "cleanup", "expiry"}:
            raise ValueError("unknown metric dispatch purpose")
        if (purpose == "cleanup") != bool(payload.get("expiry_id")):
            raise ValueError("cleanup requires a durable expiry identity")
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
            UUID(payload["expiry_id"]) if purpose == "cleanup" else None,
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


def _expiry_requests(project_id, organization_id):
    # Only the publication outbox can authorize this lane; pending flags cannot.
    return MetricErasureRequest.objects.filter(
        project_key=project_id,
        organization_key=organization_id,
        scope_kind="history",
        store_payload__protocol="metric_expiry",
        deletion_key=Concat(
            Value("expiry:"),
            KeyTextTransform("checkpoint_attempt", "store_payload"),
            output_field=CharField(),
        ),
        status__in=("queued", "failed"),
        completed_at__isnull=True,
    ).order_by("requested_at", "id")


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
            cleanup_id=Subquery(
                _expiry_requests(
                    OuterRef("project_id"), OuterRef("project__organization_id")
                ).values("id")[:1]
            ),
            simulation_agent_id=F("project__simulation_agent_id"),
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
        # Paused and hidden projects run only privacy work: an expiry pass
        # once per expiry interval (and any cleanup debt). The floor's
        # own stamp is written a little after a pass starts, so wait a further
        # PAUSED_EXPIRY_SLACK: the next pass then finds the interval elapsed.
        .filter(
            Q(paused_reason="", simulation_agent_id__isnull=True)
            | Q(pending_expiry=True)
            | Q(cleanup_id__isnull=False)
            | Q(last_started_at__isnull=True)
            | Q(last_started_at__lte=now - expiry_interval() - PAUSED_EXPIRY_SLACK)
        )
        .filter(
            Q(simulation_agent_id__isnull=True)
            | Q(cleanup_id__isnull=False)
            | Q(head_manifest_id__isnull=False)
        )
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
        .filter(
            Q(consecutive_failures=0)
            | Q(last_error_at__isnull=True)
            | Q(retry_due__lte=now)
        )
        .filter(
            Q(incremental_last_success_at__isnull=True)
            | Q(incremental_due__lte=now)
            | Q(simulation_agent_id__isnull=False, head_manifest_id__isnull=False)
            | Q(refresh_requested_at__isnull=False)
            # A publication may succeed before its cleanup fails. The durable
            # failure itself must keep that remainder eligible after backoff.
            | Q(consecutive_failures__gt=0)
            | Q(pending_backfill=True)
            | Q(pending_repair=True)
            | Q(routine_repair_due=True)
            | Q(pending_expiry=True)
            | Q(cleanup_id__isnull=False)
            | Q(pending_erasure=True)
            | Q(recovery_pending=True)
            | Q(head_manifest__published_by__source_ceilings__recovery_pending=True)
            | Q(next_due_at__lte=now)
        )
        # Least recently admitted first among ELIGIBLE projects, failing or
        # not: backoff alone deprioritises a failing project (it is not
        # eligible until retry_due), and endless chunks move to the back of the
        # same queue. Every eligible project gets a turn within as many
        # dispatches as there are eligible projects. (10M: ordering healthy
        # projects first starved once-failed ones behind always-busy ones.)
        .order_by(Coalesce("last_started_at", "created_at"), "project_id")
    )


@contextmanager
def _project_admission(
    project_id: UUID, expiry_id: UUID | None = None, *, privacy_only=False
) -> Iterator[bool]:
    # Lifecycle -> parent -> refresh. Visibility linearizes at the locked claim;
    # cleanup authorization instead names an existing immutable publication debt.
    # Keep the existing global OFF and deleted-parent policies for both lanes.
    with transaction.atomic():
        # A privacy purge may legitimately hold lifecycle serialization while
        # draining a mutation. Skip that project, never stall fleet dispatch.
        if not project_lock(project_id, blocking=False):
            yield False
            return
        if expiry_id is None and not privacy_only:
            yield metrics_enabled(project=project_id) and (
                Project.no_workspace_objects.select_for_update(skip_locked=True)
                .filter(pk=project_id)
                .exists()
            )
            return
        # Resolve only an already-enrolled refresh row; this path cannot create
        # hidden state. Lock its parent alone, retaining the normal lock order.
        state = (
            MetricProjectRefresh.objects.select_related("project")
            .select_for_update(of=("project",), skip_locked=True)
            .filter(project_id=project_id, project__deleted=False)
            .first()
        )
        if expiry_id is None:
            # Flags alone cannot authorize expiry on a headless hidden project.
            yield bool(
                state
                and (state.head_manifest_id or not state.project.simulation_agent_id)
            )
            return
        yield bool(
            state
            and _expiry_requests(project_id, state.project.organization_id)
            .filter(pk=expiry_id)
            .exists()
        )


def reserve_projects(*, clock: Callable = timezone.now) -> list[Reservation]:
    enabled = metrics_enabled()
    now, limit = clock(), concurrent_projects()
    with transaction.atomic():
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                [_LOCK_NAMESPACE, _DISPATCH_LOCK],
            )
            if not cursor.fetchone()[0]:
                return []
        from tracer.services.derived_metrics import busy_guard, size_cap

        if enabled:
            size_cap.resume_within_limit(now=now)
            busy_guard.resume_within_budget(now=now)
        free = max(
            0,
            limit
            - MetricProjectRefresh.objects.filter(lease_expires_at__gt=now).count(),
        )
        reservations: list[Reservation] = []
        # Never hold a refresh row then acquire its project serialization lock:
        # the publication protocol acquires these locks in the opposite order.
        for state in eligible_projects(now).iterator(chunk_size=100):
            if len(reservations) >= free:
                break
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
            privacy_only = state.simulation_agent_id is not None or not metrics_enabled(
                project=state.project_id
            )
            if state.paused_reason or privacy_only or state.pending_expiry:
                purpose = "expiry"
            expiry_id = state.cleanup_id
            if expiry_id:
                purpose = "cleanup"
            job = f"cleanup:{expiry_id}" if expiry_id else purpose
            with _project_admission(
                state.project_id, expiry_id, privacy_only=purpose == "expiry"
            ) as admitted:
                if not admitted:
                    continue
                if (
                    not MetricProjectRefresh.objects.select_for_update(skip_locked=True)
                    .filter(pk=state.pk)
                    .exists()
                ):
                    continue
                grant = MetricProjectRefresh.take_lease(
                    state.project_id,
                    holder=f"metrics:queued:{job}",
                    ttl=DEFAULT_LEASE_TTL,
                    now=now,
                )
                if grant is None:
                    continue
                updates = {"last_started_at": now}
                if purpose == "repair":
                    # Persist eligibility before enqueue, leaving coverage and
                    # its CAS token to the existing fenced writer.
                    updates["pending_repair"] = True
                MetricProjectRefresh.objects.filter(project_id=state.project_id).update(
                    **updates
                )
                reservations.append(
                    Reservation(grant, purpose, state.refresh_requested_at, expiry_id)
                )
        return reservations


def request_refresh(project_id: UUID, *, clock: Callable = timezone.now) -> bool:
    """Persist Run now, coalescing and throttling concurrent requests in PG.

    The request remains until a successful pass acknowledges *that timestamp*.
    last_started_at continues the throttle after acknowledgment. This is a
    service seam for the API's existing definition/permission admission.
    """
    if not metrics_enabled(project=project_id):
        return False
    now = clock()
    # Run now only records a coalesced request. It must not acquire the writer's
    # lifecycle lock (including while a previous deployment has a stuck writer).
    # Admission/visibility is rechecked by the dispatcher before taking a lease.
    if not Project.no_workspace_objects.filter(pk=project_id).exists():
        return False
    # Saving a definition already enrolls its project. Never lazily recreate
    # state here: a parent cascade may have committed after the visibility read.
    with transaction.atomic():
        state = (
            MetricProjectRefresh.objects.select_for_update(
                of=("self",), skip_locked=True
            )
            .filter(project_id=project_id, project__deleted=False)
            .first()
        )
        if state is None:
            # A skipped lock is NOT proof of a pending/throttled request. Missing
            # state likewise cannot acknowledge delivery; the caller must retry.
            raise RefreshAdmissionUnavailable
        if (
            state.refresh_requested_at is not None and state.paused_reason != "too_busy"
        ) or (state.last_started_at and now < state.last_started_at + RUN_NOW_THROTTLE):
            return False
        updates = {"refresh_requested_at": now}
        if state.paused_reason == "too_busy":
            from tracer.services.derived_metrics.busy_guard import resume_updates

            if state.lease_expires_at and state.lease_expires_at > now:
                return False
            updates.update(resume_updates(now))
        MetricProjectRefresh.objects.filter(pk=state.pk).update(**updates)
    return True


def record_failure(reservation: Reservation, *, now: datetime, code: str) -> None:
    # Only fixed internal codes, never exception messages or private payloads.
    if code not in {"metric_dispatch_failed", "metric_refresh_failed"}:
        raise ValueError("unsupported metric failure code")
    grant = reservation.grant
    fenced = MetricProjectRefresh.objects.filter(
        project_id=grant.project_id,
        fencing_epoch=grant.fencing_epoch,
        lease_token=grant.lease_token,
    )
    try_control_update(
        fenced,
        last_error_at=now,
        last_error_code=code,
        consecutive_failures=F("consecutive_failures") + 1,
        **({"busy_yields": 0} if code == "metric_refresh_failed" else {}),
    )
    if reservation.purpose == "expiry":
        # Retry a failed expiry after backoff, not a whole interval later. Only a
        # published head can clear this flag, so never set it on a headless project.
        try_control_update(
            fenced.filter(head_manifest__isnull=False), pending_expiry=True
        )


def release(reservation: Reservation, *, now: datetime) -> None:
    grant = reservation.grant
    MetricProjectRefresh.release_lease(
        grant.project_id, epoch=grant.fencing_epoch, token=grant.lease_token, now=now
    )


def _queued_reservation(reservation: Reservation):
    grant = reservation.grant
    return MetricProjectRefresh.objects.filter(
        project_id=grant.project_id,
        fencing_epoch=grant.fencing_epoch,
        lease_token=grant.lease_token,
        lease_holder=f"metrics:queued:{reservation.job}",
    )


def _release_queued(reservation: Reservation) -> None:
    # Rejection and duplicate delivery must not release a running/replacement job.
    try_control_update(
        _queued_reservation(reservation),
        lease_token=None,
        lease_expires_at=None,
        lease_holder="",
    )


def dispatch(enqueue: Callable[[dict], None], *, clock: Callable = timezone.now) -> int:
    count = 0
    for reservation in reserve_projects(clock=clock):
        with _project_admission(
            reservation.grant.project_id,
            reservation.expiry_id,
            privacy_only=reservation.purpose == "expiry",
        ) as admitted:
            if not admitted:
                _release_queued(reservation)
                continue
            if (
                not _queued_reservation(reservation)
                .filter(lease_expires_at__gt=clock())
                .exists()
            ):
                continue
            try:
                enqueue(reservation.payload())
                count += 1
            except Exception as exc:
                # A lost enqueue response may already have a running consumer.
                # Catch inside the transaction so its claim is not rolled back.
                if (
                    _queued_reservation(reservation)
                    .select_for_update(skip_locked=True)
                    .exists()
                ):
                    record_failure(
                        reservation, now=clock(), code="metric_dispatch_failed"
                    )
                    _release_queued(reservation)
                private_logger.exception("metric_dispatch_failed", exc)
    return count


# The dispatch activity runs once a minute with a 50 s limit. Reserving only
# at that tick left the single slot idle 76% of the time on the 10M run (a
# 12 s pass, then a wait for the next tick), so one tick keeps filling freed
# slots, polling this often, until it has run this long.
DISPATCH_TICK_SECONDS = 40
DISPATCH_POLL_SECONDS = 2


def dispatch_until(
    enqueue: Callable[[dict], None],
    *,
    clock: Callable = timezone.now,
    seconds: float = DISPATCH_TICK_SECONDS,
    poll: float = DISPATCH_POLL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """dispatch(), then again whenever a slot frees, until ``seconds`` pass.

    Every round is an ordinary dispatch: the same K slots, eligibility,
    least-recently-admitted order and dispatch lock, so a freed slot is refilled
    within ``poll`` seconds instead of at the next tick.
    """
    from time import sleep as default_sleep

    sleep = sleep or default_sleep
    deadline = monotonic() + seconds
    count = dispatch(enqueue, clock=clock)
    while monotonic() + poll < deadline:
        sleep(poll)
        count += dispatch(enqueue, clock=clock)
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
    # The activity timeout cannot kill a synchronous Python thread. Leave a
    # full minute for unwinding transports/locks before its 600-second timeout.
    lease = ProjectLease(
        reservation.grant, clock=clock, deadline=monotonic() + PASS_SECONDS
    )
    privacy_only = reservation.purpose in {"expiry", "cleanup"}
    if not privacy_only and not metrics_enabled(project=lease.project_id):
        _release_queued(reservation)
        return False
    with execution_slot(lease.project_id) as admitted, lifecycle_lock_budget(lease):
        if not admitted:
            _release_queued(reservation)
            return False
        from tracer.services.derived_metrics import busy_guard

        claimed = False
        previous_head = None
        previous_frontier = None
        try:
            with _project_admission(
                lease.project_id, reservation.expiry_id, privacy_only=privacy_only
            ) as visible:
                if not visible:
                    _release_queued(reservation)
                    return False
                claimed = (
                    _queued_reservation(reservation)
                    .filter(MetricProjectRefresh.lease_q(**lease.fence()))
                    .update(lease_holder=f"metrics:running:{reservation.job}")
                )
                if not claimed:
                    return False
                if reservation.purpose == "expiry":
                    # A queued purpose (or a cleanup debt) isn't permission to
                    # read telemetry. Bind this decision to the locked claim,
                    # never to a caller-supplied reservation payload.
                    lease.expiry_source_allowed = (
                        metrics_enabled(project=lease.project_id)
                        and Project.no_workspace_objects.filter(
                            pk=lease.project_id
                        ).exists()
                    )
                lease.renew()
            # Admission commits before the runner: publication/physical cleanup
            # require their existing independent commit boundaries.
            if not privacy_only and not metrics_enabled(project=lease.project_id):
                return False
            state_before = MetricProjectRefresh.objects.get(project_id=lease.project_id)
            previous_head = state_before.head_manifest_id
            previous_frontier = busy_guard.frontier(state_before)
            started = lease.ticker()
            if reservation.expiry_id:
                run_project_cleanup(lease, reservation.expiry_id)
            else:
                (runner or run_project_pass)(lease, reservation.purpose)
            # The writer alone advances work flags, cursors and manifest. A
            # successful backfill/repair is not proof of incremental catch-up.
            updates = {"consecutive_failures": 0, "last_error_code": ""}
            if not privacy_only:
                updates["busy_yields"] = 0
            with transaction.atomic():
                state = MetricProjectRefresh._locked(lease.project_id, **lease.check())
                if (
                    not reservation.expiry_id
                    and state.refresh_requested_at == reservation.requested_at
                ):
                    updates["refresh_requested_at"] = None
                # A pass that finished in under a quarter of its budget earns
                # back capture budget lost to an earlier yield.
                learned = state.pass_capture_budget
                if learned is not None and lease.ticker() - started < PASS_SECONDS / 4:
                    from tracer.services.derived_metrics.refresh import (
                        pass_max_captures,
                    )

                    grown = learned * 2
                    updates["pass_capture_budget"] = (
                        None if grown >= pass_max_captures() else grown
                    )
                MetricProjectRefresh.objects.filter(pk=state.pk).update(**updates)
            return True
        except MetricPassDeadline:
            if claimed and not privacy_only:
                busy_guard.record_yield(lease, previous_frontier)
            # Published chunk cursors remain durable. A pass that made progress
            # must not inherit the OLD failure's exponential backoff when yielding.
            # Do not acknowledge Run now until the requested pass really finishes.
            if claimed:
                try_control_update(
                    MetricProjectRefresh.objects.filter(
                        project_id=lease.project_id,
                        fencing_epoch=lease.grant.fencing_epoch,
                        lease_token=lease.grant.lease_token,
                    ).exclude(head_manifest_id=previous_head),
                    consecutive_failures=0,
                    last_error_code="",
                )
            return False
        except MetricFencingError:
            return False
        except Exception:
            if claimed:
                record_failure(reservation, now=clock(), code="metric_refresh_failed")
            raise
        finally:
            if claimed:
                lease.release()


def run_project_cleanup(lease: ProjectLease, expiry_id: UUID) -> None:
    """Retry one publication debt without discovery, analytics or new metric state.

    Unlike normal work this needs no current retention-policy provider: the
    immutable intent already fixes the namespace. Existing scrub/barrier proof,
    not a successful task return, is the only authority to mark it complete.
    """
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore
    from tracer.services.derived_metrics.retention import finish_pending_expiry

    state = MetricProjectRefresh.objects.select_related("project").get(
        project_id=lease.project_id
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
        writer = RefreshWriter(
            lease,
            ClickHousePublicationStore(client, lease=lease),
            org_id=state.project.organization_id,
        )
        finish_pending_expiry(writer, expiry_id=expiry_id)
    finally:
        client.disconnect()


class NativeMetricAnalytics:
    """Keep B04's per-statement caps; interactive analytics strips these caps."""

    supports_per_query_read_settings = True

    def __init__(self, client, *, lease=None):
        self.client = client
        self.lease = lease

    def execute_ch_query(
        self, query, params, *, timeout_ms, settings, server_execution_cap_ms=None
    ):
        from tracer.services.clickhouse.query_service import QueryResult
        from tracer.services.derived_metrics.publication import cooperative_timeout

        if self.lease is not None:
            self.lease.keepalive()
            timeout_ms = min(timeout_ms, self.lease.remaining_seconds() * 1000)
        started = monotonic()
        cap = max(0.001, min(timeout_ms, server_execution_cap_ms or timeout_ms) / 1000)
        try:
            rows, columns = self.client.execute(
                query,
                params,
                with_column_types=True,
                settings={**settings, "max_execution_time": cap},
            )
        except Exception as exc:
            # A server timeout of a statement whose cap was reduced to the
            # reservation's remaining pass budget is a cooperative yield.
            cancelled = cooperative_timeout(self.lease, started + cap, exc)
            if cancelled is not None:
                raise cancelled from exc
            raise
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
        from tracer.services.derived_metrics.backfill import PAGE_SIZE

        # Bound incremental owners as well as the separate history nomination;
        # larger arrival bursts use PG's durable repair cursor, never truncation.
        self.incremental_unit_limit = PAGE_SIZE

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
    # A NULL workspace reaches the writer, which fails closed ("refresh scope
    # mismatch"). For legacy NULL-workspace projects that is a known bug: their
    # metrics never publish. TH-10607 resolves the default workspace here.
    scope = Scope(
        state.project.organization_id,
        state.project.workspace_id,  # type: ignore[arg-type]
        lease.project_id,
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
        store = ClickHousePublicationStore(client, lease=lease)
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
        analytics = NativeMetricAnalytics(client, lease=lease)
        from tracer.services.derived_metrics import size_cap

        if purpose == "expiry" or state.paused_reason:
            # Paused as too large: privacy work only, never new calculation.
            run_expiry_pass(writer)
            if MetricProjectRefresh.for_project(lease.project_id).head_manifest_id:
                writer.collect_garbage()
            return
        if state.head_manifest_id is not None and run_due_expiry(
            writer, state.head_manifest
        ):
            # Physical expiry is due (about once an expiry interval): this pass
            # rewrites the head and does nothing else, so a capture budget and
            # a head-sized rewrite never share one pass's deadline.
            writer.collect_garbage()
            return
        # A pre-upgrade range checkpoint may have lost its private receipts
        # before durable counts existed. Refuse fresh growth on unknown size;
        # explicit maintenance can rebuild the count from verified native rows.
        # Privacy work above remains independent of this capacity guard.
        if state.head_manifest_id is not None and size_cap.pause_if_too_large(
            lease.project_id, size_cap.head_rows(state.head_manifest), now=lease.clock()
        ):
            return
        if state.head_manifest_id is None and size_cap.pause_at_enrollment(
            lease, analytics, retention_days=policy(scope.organization)
        ):
            return
        # Every version decides history vs "start with no history" before it
        # can enter backfill; forward-only versions activate here, so a large
        # project never starts a history sweep for them.
        backfill.enter_backfill(
            lease, analytics, retention_days=lambda: policy(scope.organization)
        )
        runner = backfill.BackfillRunner(
            writer, analytics, scope, exhaustive_initial=True
        )
        if backfill.pending_versions(lease.project_id).exists() and (
            purpose == "backfill"
            or not DerivedMetricVersion.objects.filter(
                metric__project_id=lease.project_id,
                metric__deleted=False,
                id=F("metric__active_version_id"),
                pins_revoked_at__isnull=True,
            ).exists()
        ):
            # Initial history has no serving version to keep fresh. Give every
            # lane durable history progress instead of an uncheckpointed initial
            # incremental sweep. Each chunk still includes incremental discovery.
            runner.run_pass(max_chunks=backfill.MAX_CHUNKS_PER_PASS)
        elif purpose == "repair" and state.pending_repair:
            runner.run_repair_pages()
        else:
            run_incremental(writer, analytics, scope, state)
        writer.collect_garbage()
        size_cap.pause_if_too_large(
            lease.project_id,
            size_cap.head_rows(
                MetricProjectRefresh.for_project(lease.project_id).head_manifest
            ),
            now=lease.clock(),
        )
    finally:
        client.disconnect()


def run_due_expiry(writer, head) -> bool:
    """Run an expiry-only pass when the head holds a row past the physical cutoff."""
    if head is None or not writer._head_holds_expired_rows(head):
        return False
    run_expiry_pass(writer)
    return True


def run_expiry_pass(writer) -> None:
    """Physical expiry only, nothing new calculated (paused or expiry due)."""
    from tracer.services.derived_metrics import backfill
    from tracer.services.derived_metrics.retention import (
        expire_project,
        finish_unpublished_replan,
    )

    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    if head is None:
        finish_unpublished_replan(writer)
        return
    expire_project(
        writer,
        backfill.retirement_definitions(
            writer, head, backfill.working_definitions(writer.lease.project_id)
        ),
        retention_days=writer.retention_days,
    )


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
        # may consume that sweep's start ceiling. A forward-only project sweeps
        # only the burst (queue_overflow_sweep), never its whole enrollment.
        backfill.queue_overflow_sweep(
            analytics, writer, state.head_manifest, definitions, now=now
        )
        backfill.BackfillRunner(writer, analytics, scope).run_repair_pages(
            skip_incremental=True
        )
        return
    # A window more than one pass can capture is paged through the durable
    # burst sweep instead (bug 6: one BIG pass planned 107 captures, ran past
    # its lease and published nothing, and the next pass repeated it).
    allowance = backfill.pass_unit_allowance(definitions, state)
    discovered = sum(1 for unit in batch.units if unit.level == "conversation")
    if allowance is not None and discovered > allowance:
        backfill.queue_overflow_sweep(
            analytics, writer, state.head_manifest, definitions, now=now
        )
        backfill.BackfillRunner(writer, analytics, scope).run_repair_pages(
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
    if allowance is not None:
        # Oldest due first, inside what is left of the pass's capture budget;
        # the rest stay due for the next pass.
        due = due[: max(0, allowance - discovered)]
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
