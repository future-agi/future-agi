"""One mirror's failure loses no other thread's rows (real driver, live ClickHouse).

The span and trace mirrors share one cached clickhouse-connect client per
process (``trace_writer._get_client``). A mirror whose batch fails logs it and
calls ``trace_writer._reset_client()``, which closes that client and drops it
while other threads may be inserting on it or fetching it.

Every client here is the real driver, writing to the test ClickHouse the
integration suite seeds (``get_v2_config()``). A wrapper on each client the
writers build counts its open data requests and records each close, calling
straight through: nothing holds an insert open or stands in for the driver.
Rows are counted by id over the suite's native ClickHouse connection.

The trace mirror reads traces on worker threads, each with its own database
connection, and those threads cannot see rows inside the per-test
transaction. So the project and every trace a test hands the mirrors are
committed once for the module, before any test's transaction opens, and each
test takes trace ids no other test here has taken. The module removes its
ClickHouse rows and deletes its project and traces when it ends.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import clickhouse_connect
import pytest
from django.conf import settings
from django.db import connections
from structlog.processors import format_exc_info
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import span_writer, trace_writer

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.django_db]

SPAN_THREADS = 3
TRACE_THREADS = 2
ROUNDS = 3
SPANS_PER_BATCH = 2000
TRACES_PER_BATCH = 200
LONE_BATCH = 100
WAIT_S = 60
FORCED = "forced mirror failure"
# Committed traces the module makes once. One ``_mirror_at_once`` run takes
# RUN_TRACES; the tests here run it twice and take one lone trace.
RUN_TRACES = TRACE_THREADS * ROUNDS * TRACES_PER_BATCH + ROUNDS
TRACE_POOL = 2 * RUN_TRACES + 1


class Probe:
    """Each real client the writers build: its open data requests and closes."""

    def __init__(self):
        self.changed = threading.Condition()
        self.open = []  # data requests open, per client in build order
        self.span_started = 0  # span data requests started, all clients
        self.span_open = 0  # span data requests open now, all clients
        self.closes = []  # (client number, data requests open on it)

    def watch(self, client):
        with self.changed:
            number = len(self.open)
            self.open.append(0)
        insert, close = client.data_insert, client.close

        def data_insert(context):
            # Runs after the insert's DESCRIBE; the data request is sent inside.
            spans = "spans" in str(context.table)
            self._step(number, spans, 1)
            try:
                return insert(context)
            finally:
                self._step(number, spans, -1)

        def closing():
            with self.changed:
                self.closes.append((number, self.open[number]))
            return close()

        client.data_insert = data_insert
        client.close = closing
        return client

    def _step(self, number, spans, step):
        with self.changed:
            self.open[number] += step
            if spans:
                self.span_open += step
                if step > 0:
                    self.span_started += 1
            self.changed.notify_all()

    def span_requests_started(self):
        with self.changed:
            return self.span_started

    def wait_for_span_request(self, started_before):
        """Until a span data request that started after the mark is open."""
        with self.changed:
            return self.changed.wait_for(
                lambda: self.span_started > started_before and self.span_open > 0,
                WAIT_S,
            )


@pytest.fixture(scope="module")
def committed(integration_setup, django_db_blocker, ch_schema):
    """The project and every trace the mirrors read, committed for the module.

    The trace mirror re-reads traces by id on worker threads, each with its
    own database connection, so it finds only committed rows. A module fixture
    is set up before any test's transaction opens, so what it writes under
    ``unblock()`` commits, as in test_rest_trace_listed_in_observe.py. The
    session organization and workspace from ``integration_setup`` are
    committed too. ``pool`` holds the trace ids no test has taken yet.
    """
    database = settings.CLICKHOUSE["CH_DATABASE"]
    with django_db_blocker.unblock():
        project = Project.objects.create(
            name=f"Mirror reset {uuid.uuid4().hex[:8]}",
            organization=integration_setup.organization,
            workspace=integration_setup.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )
        made = Trace.objects.bulk_create(
            [Trace(project=project, name=f"mirrored {n}") for n in range(TRACE_POOL)],
            batch_size=TRACES_PER_BATCH,
        )
    yield SimpleNamespace(project=project, pool=[str(trace.id) for trace in made])

    for table in ("spans", "traces"):
        try:
            ch_schema.command(
                f"ALTER TABLE {database}.{table} DELETE WHERE "
                f"project_id = '{project.id}' SETTINGS mutations_sync = 2"
            )
        except Exception:
            pass  # best effort, as the suite's clean_ch
    with django_db_blocker.unblock():
        Trace.all_objects.filter(project_id=project.id).delete()
        Project.with_simulation.filter(id=project.id).delete()


@pytest.fixture
def live(monkeypatch, committed, ch_schema, django_db_blocker):
    """Mirrors on, through the real driver, into the suite's test ClickHouse.

    The module's ``django_db`` mark is not ``transaction=True``: a
    transactional flush would wipe the session corpus the other tests read.
    So this thread runs each test inside a transaction the mirrors' threads
    cannot see into, and every row they read comes from ``committed``.
    """
    config = trace_writer.get_v2_config()
    database = settings.CLICKHOUSE["CH_DATABASE"]
    if not str(config["database"]).lower().lstrip("_").startswith("test_"):
        pytest.skip(f"not writing to {config['database']!r}: not a test_* database")
    assert config["database"] == database, (
        f"the writers use {config['database']!r}; the suite reads {database!r}"
    )
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.delenv("CH25_SPAN_DUAL_WRITE", raising=False)
    monkeypatch.delenv("CH25_TRACE_DUAL_WRITE", raising=False)
    monkeypatch.setattr(trace_writer, "_client", None)

    probe = Probe()
    build = clickhouse_connect.get_client
    monkeypatch.setattr(
        clickhouse_connect, "get_client", lambda **kw: probe.watch(build(**kw))
    )

    failing = set()
    to_row = trace_writer._trace_to_row

    def trace_to_row(trace, **kwargs):
        if str(trace.id) in failing:
            raise RuntimeError(f"{FORCED} {trace.id}")
        return to_row(trace, **kwargs)

    monkeypatch.setattr(trace_writer, "_trace_to_row", trace_to_row)

    project = committed.project

    def traces(count):
        """``count`` committed trace ids no test here has taken yet.

        Each id goes out once: rows are counted by id in one shared project,
        so an id one test mirrored must never count for another.
        """
        left = len(committed.pool)
        assert count <= left, (
            f"{count} committed traces asked for, {left} left: raise TRACE_POOL"
        )
        taken = committed.pool[:count]
        del committed.pool[:count]
        return taken

    def missing(span_batches, trace_batches):
        """Rows of each batch not in ClickHouse, by batch name."""
        where = f"WHERE project_id = '{project.id}'"
        spans_found = dict(
            ch_schema.query(
                f"SELECT trace_id, uniqExact(id) FROM {database}.spans {where} "
                "GROUP BY trace_id"
            ).result_rows
        )
        traces_found = {
            row[0]
            for row in ch_schema.query(
                f"SELECT toString(id) FROM {database}.traces {where}"
            ).result_rows
        }
        lost = {}
        for name, (marker, rows) in span_batches.items():
            found = spans_found.get(marker, 0)
            if found != len(rows):
                lost[name] = f"{found} of {len(rows)} spans"
        for name, ids in trace_batches.items():
            found = sum(trace_id in traces_found for trace_id in ids)
            if found != len(ids):
                lost[name] = f"{found} of {len(ids)} traces"
        return lost

    try:
        yield SimpleNamespace(
            project=project,
            probe=probe,
            failing=failing,
            traces=traces,
            missing=missing,
            # Under the django_db mark every thread may connect for the whole
            # test (the blocker patches the connection class, not a thread),
            # so this is a nested no-op. It commits nothing and does not take
            # this thread out of the test's transaction.
            unblock=django_db_blocker.unblock,
        )
    finally:
        trace_writer._reset_client()


def _span_batch(project_id, size):
    """``size`` span rows of one new trace, as the span mirror takes them."""
    trace_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    rows = [
        {
            "id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "project_id": project_id,
            "observation_type": "llm",
            "name": "answer",
            "start_time": now,
            "created_at": now,
            "updated_at": now,
            "span_attributes": {"llm.model_name": "gpt-4o", "streaming": True},
            "input": {"question": "hello"},
            "output": "hi",
        }
        for _ in range(size)
    ]
    return trace_id, rows


def _failures(logs):
    """Every ``*_dual_write_failed`` line, from any thread."""
    failed = "_dual_write_failed"
    return [line for line in logs if str(line.get("event", "")).endswith(failed)]


def _forced(line):
    return line["event"] == "trace_dual_write_failed" and str(
        line.get("err", "")
    ).startswith(FORCED)


def _said(lines):
    """Each failure line's event and error (a traceback's last line)."""
    said = []
    for line in lines:
        error = line.get("err") or str(line.get("exception", "")).strip()
        said.append((line["event"], error.rsplit("\n", 1)[-1]))
    return said


def _mirror_at_once(live, *, fail_one_thread):
    """Span and trace threads each mirror one batch per round, side by side.

    One more thread waits each round until a span insert's data request is
    open, then mirrors one trace of its own. With ``fail_one_thread`` that
    trace's row cannot be built, so the mirror logs the failure and resets the
    shared client while the span insert is in flight.
    """
    project_id = str(live.project.id)
    span_batches, trace_batches = {}, {}
    span_plans, trace_plans = [], []
    for thread in range(SPAN_THREADS):
        plan = []
        for round_ in range(ROUNDS):
            marker, rows = _span_batch(project_id, SPANS_PER_BATCH)
            span_batches[f"span thread {thread} round {round_}"] = (marker, rows)
            plan.append(rows)
        span_plans.append(plan)
    for thread in range(TRACE_THREADS):
        plan = []
        for round_ in range(ROUNDS):
            ids = live.traces(TRACES_PER_BATCH)
            trace_batches[f"trace thread {thread} round {round_}"] = ids
            plan.append(ids)
        trace_plans.append(plan)
    own = [live.traces(1)[0] for _ in range(ROUNDS)]
    if fail_one_thread:
        live.failing.update(own)
    else:
        trace_batches["one more thread"] = own
    barrier = threading.Barrier(SPAN_THREADS + TRACE_THREADS + 1)

    def mirror_each_round(mirror, plan):
        try:
            for batch in plan:
                barrier.wait(WAIT_S)
                mirror(batch)
        finally:
            connections.close_all()

    def mirror_once_an_insert_is_open():
        try:
            for trace_id in own:
                barrier.wait(WAIT_S)
                mark = live.probe.span_requests_started()
                live.probe.wait_for_span_request(mark)
                trace_writer.mirror_traces_to_clickhouse([trace_id])
        finally:
            connections.close_all()

    spans_mirror = span_writer.mirror_spans_to_clickhouse
    traces_mirror = trace_writer.mirror_traces_to_clickhouse
    jobs = [(mirror_each_round, spans_mirror, plan) for plan in span_plans]
    jobs += [(mirror_each_round, traces_mirror, plan) for plan in trace_plans]
    jobs.append((mirror_once_an_insert_is_open,))
    # live.unblock() is a no-op under the django_db mark (see ``live``). Each
    # thread that mirrors traces reads the committed rows on its own
    # autocommit connection and closes it in its ``finally``; the span threads
    # read no table, and this thread reads no Postgres table at all.
    with live.unblock(), capture_logs(processors=[format_exc_info]) as logs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(*job) for job in jobs]
    errors = [repr(f.exception()) for f in futures if f.exception() is not None]
    return SimpleNamespace(
        logs=logs,
        errors=errors,
        lost=live.missing(span_batches, trace_batches),
    )


def test_a_failed_mirror_resetting_the_client_loses_no_other_threads_rows(live):
    run = _mirror_at_once(live, fail_one_thread=True)
    failures = _failures(run.logs)
    others = [line for line in failures if not _forced(line)]
    assert run.errors == []
    assert run.lost == {}, f"rows lost: {run.lost}; failures: {_said(others)}"
    assert _said(others) == []
    # The failure branch ran each round, and at least one reset closed a client
    # while an insert's data request on that same client was open. Without
    # both, a pass here would prove nothing.
    assert len(failures) - len(others) == ROUNDS
    assert any(open_ for _, open_ in live.probe.closes), (
        f"no reset landed under an open insert: closes={live.probe.closes}"
    )


def test_the_same_mirrors_without_a_failure_lose_no_rows(live):
    run = _mirror_at_once(live, fail_one_thread=False)
    failures = _failures(run.logs)
    assert run.errors == []
    assert run.lost == {}, f"rows lost: {run.lost}; failures: {_said(failures)}"
    assert _said(failures) == []
    assert live.probe.closes == []


def test_a_reset_as_another_mirror_builds_the_client_loses_none_of_its_rows(
    live, monkeypatch
):
    """A mirror builds the shared client and leaves the lock; before it reads
    the client back, another mirror fails and resets it. The interpreter may
    switch threads right after the lock's release call returns, and the
    writers' lock is wrapped only to make that switch happen every run.

    Only the building thread is held, just after it releases the client
    lock; the driver, the ClickHouse server, the client and the inserts are
    all real."""
    marker, rows = _span_batch(str(live.project.id), LONE_BATCH)
    failing_id = live.traces(1)[0]
    live.failing.add(failing_id)
    left_lock = threading.Event()
    failed = threading.Event()
    builder = {}
    lock = trace_writer._client_lock

    class PausingLock:
        """The writers' lock; the building thread waits right after leaving it."""

        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *exc_info):
            lock.__exit__(*exc_info)
            mine = threading.current_thread() is builder.get("thread")
            if mine and not left_lock.is_set():
                left_lock.set()
                failed.wait(WAIT_S)
            return False

    monkeypatch.setattr(trace_writer, "_client_lock", PausingLock())

    def build_and_mirror():
        builder["thread"] = threading.current_thread()
        span_writer.mirror_spans_to_clickhouse(rows)

    def fail_meanwhile():
        try:
            left_lock.wait(WAIT_S)
            trace_writer.mirror_traces_to_clickhouse([failing_id])
        finally:
            failed.set()
            connections.close_all()

    with live.unblock(), capture_logs(processors=[format_exc_info]) as logs:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(build_and_mirror), pool.submit(fail_meanwhile)]
    errors = [repr(f.exception()) for f in futures if f.exception() is not None]
    failures = _failures(logs)
    others = [line for line in failures if not _forced(line)]
    lost = live.missing({"building mirror": (marker, rows)}, {})
    assert errors == []
    assert lost == {}, (
        f"rows lost: {lost}; failures: {_said(others)}; "
        f"paused after the lock: {left_lock.is_set()}"
    )
    assert _said(others) == []
    assert len(failures) - len(others) == 1
