# ruff: noqa: F811 -- shared isolated ClickHouse / PostgreSQL fixtures
"""A native timeout caused by OUR pass-budget clamp is a cooperative yield.

Real native ClickHouse statements, real fenced admission. Only the remaining
reservation budget is shortened (as an expiring reservation would); the native
60-second statement cap, the server timeout code and the transports are real.
"""

from time import monotonic

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import admission
from tracer.services.derived_metrics.lease import MetricPassDeadline
from tracer.services.derived_metrics.publication import (
    SERVER_TIMEOUT_CODE,
    ClickHousePublicationStore,
    MetricVisibilityError,
)
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)

# About five seconds of server work, one row per block so the server observes
# max_execution_time between rows (sleepEachRow is capped at 3 s per block).
SLOW = "SELECT sum(sleepEachRow(0.25)) FROM numbers(20) SETTINGS max_block_size = 1"


@pytest.fixture
def reservation(settings, workspace, observe_project):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    make_version(observe_project, workspace)
    # A prior failure streak: a cooperative yield must neither extend it (more
    # backoff) nor be recorded as a refresh failure.
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        consecutive_failures=2, last_error_code="metric_refresh_failed"
    )
    (granted,) = admission.reserve_projects()
    return granted


def _state(project_id):
    state = MetricProjectRefresh.for_project(project_id)
    return state.consecutive_failures, state.last_error_code, state.lease_token


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["store", "analytics"])
def test_pass_clamped_native_timeout_yields_without_failure(
    publication_store, reservation, observe_project, transport
):
    observed = []

    def runner(lease, purpose):
        # Two seconds of reservation budget remain: every statement's server
        # max_execution_time (and the store's socket wall) is clamped to it.
        lease.deadline = monotonic() + 2
        began = monotonic()
        try:
            if transport == "store":
                ClickHousePublicationStore(
                    publication_store.client, lease=lease
                ).execute(SLOW)
            else:
                admission.NativeMetricAnalytics(
                    publication_store.client, lease=lease
                ).execute_ch_query(SLOW, {}, timeout_ms=60_000, settings={})
        except BaseException as exc:
            observed.append((type(exc), monotonic() - began, exc.__cause__))
            raise
        pytest.fail("the clamped statement was not stopped")

    assert admission.execute_reservation(reservation.payload(), runner=runner) is False
    ((kind, elapsed, cause),) = observed
    assert kind is MetricPassDeadline
    # A real native stop (server code 159 or the clamped socket wall), not a
    # pre-send refusal and not the 60-second statement cap.
    assert cause is not None and elapsed < 10
    if isinstance(cause, ServerException):
        assert cause.code == SERVER_TIMEOUT_CODE
    failures, code, token = _state(observe_project.id)
    assert (failures, code) == (2, "metric_refresh_failed")
    assert token is None
    # The native client is reusable for the next reservation.
    assert publication_store.execute("SELECT 1") == [(1,)]


@pytest.mark.django_db(transaction=True)
def test_unclamped_statement_timeout_is_still_a_refresh_failure(
    publication_store, reservation, observe_project
):
    def runner(lease, purpose):
        # Plenty of pass budget left: a one-second statement cap of the caller
        # binds, so the server timeout is a genuine statement failure.
        admission.NativeMetricAnalytics(
            publication_store.client, lease=lease
        ).execute_ch_query(
            SLOW, {}, timeout_ms=60_000, settings={}, server_execution_cap_ms=1000
        )

    with pytest.raises(ServerException) as raised:
        admission.execute_reservation(reservation.payload(), runner=runner)
    assert raised.value.code == SERVER_TIMEOUT_CODE
    failures, code, token = _state(observe_project.id)
    assert (failures, code) == (3, "metric_refresh_failed")
    assert token is None


@pytest.mark.django_db(transaction=True)
def test_compaction_wall_timeout_is_not_a_cooperative_yield(
    publication_store, reservation, observe_project
):
    def runner(lease, purpose):
        store = ClickHousePublicationStore(publication_store.client, lease=lease)
        # The checkpoint invocation wall, not the reservation, binds here.
        with store.compaction_budget(monotonic() + 2):
            store.execute(SLOW)

    with pytest.raises((ServerException, MetricVisibilityError, TimeoutError)):
        admission.execute_reservation(reservation.payload(), runner=runner)
    failures, code, token = _state(observe_project.id)
    assert (failures, code) == (3, "metric_refresh_failed")
    assert token is None


@pytest.mark.django_db(transaction=True)
def test_genuine_backend_failure_inside_a_short_budget_still_fails(
    publication_store, reservation, observe_project
):
    def runner(lease, purpose):
        lease.deadline = monotonic() + 2
        ClickHousePublicationStore(publication_store.client, lease=lease).execute(
            "SELECT throwIf(1, 'genuine backend failure')"
        )

    with pytest.raises(ServerException) as raised:
        admission.execute_reservation(reservation.payload(), runner=runner)
    assert raised.value.code != SERVER_TIMEOUT_CODE
    failures, code, token = _state(observe_project.id)
    assert (failures, code) == (3, "metric_refresh_failed")
    assert token is None
