"""The Insights evidence store without a database or a bucket: one gzip
document per unit, named by run and unit, in a host's directory ('local') or
in a private bucket every web pod reads ('object', over a fake storage
client); the worker's pre-build, the broker serving from the store, the
cutoff that keeps a rebuild's digest, cleanup, the sweep, and the refusal of
a bucket anyone can read.

Runs and units are plain objects; snapshots are built by the real builder
over ``test_evidence.FakeClickHouse``.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from ee.agenthub.insights import broker, evidence, evidence_store, llm, pipeline
from ee.agenthub.insights.evidence import EvidenceUnavailable
from ee.agenthub.insights.evidence_store import RunState
from ee.agenthub.insights.tests.test_evidence import PNG, FakeClickHouse, span
from ee.agenthub.insights.tests.test_insights_broker import (
    CREATED,
    RUN_ID,
    make_run,
    token,
)
from tracer.views.insights_evidence import InsightEvidenceView

BUCKET = "fi-insights-evidence"
OTHER_RUN = "77777777-7777-4777-8777-777777777777"


class StorageError(Exception):
    """What the storage client raises: its S3 code and nothing else."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Response:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)
        self.closed = self.released = False

    def read(self, amt=None):
        return self.stream.read(amt)

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeStorage:
    """The platform's storage client over a dict. ``fail`` names the
    operations that raise a storage error, ``policy_error`` the code a
    policy read fails with; ``calls`` counts them all."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads: dict[tuple[str, str], dict] = {}
        self.policy = None
        self.policy_error: str | None = None
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def _call(self, op: str) -> None:
        self.calls.append(op)
        if op in self.fail:
            raise StorageError("InternalError")

    def get_bucket_policy(self, bucket):
        self._call("get_bucket_policy")
        if self.policy_error:
            raise StorageError(self.policy_error)
        if self.policy is None:
            raise StorageError("NoSuchBucketPolicy")
        return json.dumps(self.policy)

    def put_object(self, bucket, name, data, length, content_type=None, sse=None):
        self._call("put_object")
        body = data.read()
        assert len(body) == length
        self.objects[(bucket, name)] = body
        self.uploads[(bucket, name)] = {"content_type": content_type, "sse": sse}

    def get_object(self, bucket, name):
        self._call("get_object")
        if (bucket, name) not in self.objects:
            raise StorageError("NoSuchKey")
        return Response(self.objects[(bucket, name)])

    def stat_object(self, bucket, name):
        self._call("stat_object")
        if (bucket, name) not in self.objects:
            raise StorageError("NoSuchKey")
        return SimpleNamespace(object_name=name)

    def list_objects(self, bucket, prefix=None, recursive=False):
        self._call("list_objects")
        prefix = prefix or ""
        names = sorted(
            name for b, name in self.objects if b == bucket and name.startswith(prefix)
        )
        if recursive:
            return [SimpleNamespace(object_name=n, is_dir=False) for n in names]
        dirs = {prefix + n[len(prefix) :].split("/", 1)[0] + "/" for n in names}
        return [SimpleNamespace(object_name=d, is_dir=True) for d in sorted(dirs)]

    def remove_object(self, bucket, name):
        self._call("remove_object")
        self.objects.pop((bucket, name), None)

    def runs(self) -> set[str]:
        return {name.split("/")[1] for _, name in self.objects}


@pytest.fixture(autouse=True)
def evidence_settings(settings, tmp_path, monkeypatch):
    settings.INSIGHTS_EVIDENCE_SECRET = "current-secret"
    settings.INSIGHTS_EVIDENCE_SECRET_PREVIOUS = ""
    settings.INSIGHTS_EVIDENCE_STORE = "local"
    settings.INSIGHTS_EVIDENCE_BUCKET = ""
    settings.INSIGHTS_EVIDENCE_CACHE_DIR = str(tmp_path / "cache")
    settings.INSIGHTS_EVIDENCE_CACHE_MAX_BYTES = 2 * 1024**3
    settings.INSIGHTS_EVIDENCE_FETCH_EXTERNAL_MEDIA = False
    from django.core.cache import cache

    cache.clear()
    evidence_store._recent.clear()
    evidence_store._checked_buckets.clear()
    # Sweeps only when a test says so: a bucket's sweep would otherwise run
    # on a thread that may outlive the test's fakes.
    monkeypatch.setattr(evidence_store, "_swept_at", time.time() + 3600)
    monkeypatch.setattr(evidence_store, "_arm_sweeper", lambda: None)
    return settings


@pytest.fixture
def storage(settings, monkeypatch):
    """The 'object' store over a fake bucket, on S3."""
    from tfc.utils import storage_client

    fake = FakeStorage()
    settings.INSIGHTS_EVIDENCE_STORE = "object"
    settings.INSIGHTS_EVIDENCE_BUCKET = BUCKET
    monkeypatch.setattr(storage_client, "get_storage_client", lambda: fake)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    return fake


@pytest.fixture
def world(monkeypatch):
    """A running run whose units 17 and 21 read trace T1, 18 reads T2 and 19
    reads T9, which ClickHouse does not have; ``world.builds`` counts builds
    that read ClickHouse."""
    image = f"data:image/png;base64,{PNG}"
    content = [
        {"type": "text", "text": "Is this damaged?"},
        {"type": "image_url", "image_url": {"url": image}},
    ]
    rows = [
        span(
            "T1",
            "root",
            input=json.dumps({"messages": [{"role": "user", "content": content}]}),
            output="Yes, the box is crushed.",
        ),
        span("T1", "tool", at=1, parent="root", output="refund=declined"),
        span("T2", "root", output="other trace"),
    ]
    state = SimpleNamespace(run=make_run(), builds=0, analytics=FakeClickHouse(rows))
    units = {"17": "T1", "18": "T2", "19": "T9", "21": "T1"}

    def analytics():
        state.builds += 1
        return state.analytics

    monkeypatch.setattr(
        broker, "_load_run", lambda run_id: state.run if run_id == RUN_ID else None
    )
    monkeypatch.setattr(
        broker,
        "_load_unit",
        lambda run, key: row(int(key), units[key]) if key in units else None,
    )
    monkeypatch.setattr(broker, "_analytics", analytics)
    monkeypatch.setattr(broker, "_lookup_ids", lambda kind, key: [key])
    monkeypatch.setattr(
        evidence_store,
        "_run_states",
        lambda ids: {i: (state.run.status, False) for i in ids if i == RUN_ID},
    )
    return state


def row(pk, unit_id):
    return SimpleNamespace(pk=pk, unit_id=unit_id)


def object_name(run_id, key):
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"insights-evidence/{run_id}/{digest}.json.gz"


def store():
    return evidence_store.current()


class TestConfiguration:
    def test_local_by_default_and_object_only_with_its_own_bucket(self, settings):
        assert evidence_store.configuration_error() is None
        assert isinstance(store(), evidence_store.LocalStore)
        assert broker.configured()

        settings.INSIGHTS_EVIDENCE_STORE = "object"
        cases = [
            ("", "bucket_unset"),
            # Anyone can read the upload bucket's objects by their URL.
            (settings.UPLOAD_BUCKET_NAME.upper(), "bucket_is_upload_bucket"),
        ]
        for bucket, reason in cases:
            settings.INSIGHTS_EVIDENCE_BUCKET = bucket
            assert evidence_store.configuration_error() == reason
            with pytest.raises(evidence_store.EvidenceStoreMisconfigured) as refused:
                store()
            assert refused.value.reason == reason
            # No run is pinned to v2 while the store refuses to start.
            assert not broker.configured()

        settings.INSIGHTS_EVIDENCE_BUCKET = BUCKET
        assert isinstance(store(), evidence_store.ObjectStore)
        assert broker.configured()
        settings.INSIGHTS_EVIDENCE_STORE = "s3"
        assert evidence_store.configuration_error() == "unknown_backend"

    def test_a_bucket_whose_policy_lets_anyone_in_is_refused(self, storage, world):
        built = broker.build(world.run, row(17, "T1"))
        storage.policy = {
            "Statement": [
                {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::1:root"}},
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                },
            ]
        }
        with pytest.raises(evidence_store.EvidenceStoreMisconfigured) as refused:
            store().put(RUN_ID, "17", built)
        assert refused.value.reason == "bucket_is_public"
        assert storage.objects == {}

        # Named principals only, or a policy these credentials may not read
        # (GCS keeps access in IAM): the operator's call.
        storage.policy["Statement"].pop()
        assert store().put(RUN_ID, "17", built) > 0
        for code in ("AccessDenied", "NotImplemented"):
            evidence_store._checked_buckets.clear()
            storage.policy_error = code
            assert store().put(RUN_ID, "18", built) > 0

        # A read that fails otherwise checks nothing: nothing is uploaded,
        # and the next call asks again.
        storage.objects.clear()
        for code in ("InternalError", "SlowDown"):
            evidence_store._checked_buckets.clear()
            storage.policy_error = code
            for call in (
                lambda s: s.put(RUN_ID, "17", built),
                lambda s: s.get(RUN_ID, "17"),
            ):
                with pytest.raises(evidence_store.EvidenceStoreUnavailable) as error:
                    call(store())
                assert error.value.code == "cache_unavailable"
            assert BUCKET not in evidence_store._checked_buckets
        assert storage.objects == {}
        storage.policy_error = None
        storage.policy = {"Statement": {"Effect": "Allow", "Principal": "*"}}
        with pytest.raises(evidence_store.EvidenceStoreMisconfigured):
            store().put(RUN_ID, "17", built)

    def test_only_the_servers_answer_counts_as_no_policy(self):
        def error(code, status):
            exc = StorageError(code)
            exc.response = SimpleNamespace(status=status)
            return exc

        for answer in (
            error("NoSuchBucketPolicy", 404),
            error("AccessDenied", 403),
            error("NotImplemented", 501),
            error("InvalidArgument", 400),
        ):
            assert evidence_store._no_policy(answer)
        for failure in (
            error("NoSuchBucket", 404),
            error("RequestTimeout", 408),
            error("TooManyRequests", 429),
            error("SlowDown", 503),
            error("InternalError", 500),
            TimeoutError(),
        ):
            assert not evidence_store._no_policy(failure)

    def test_a_new_run_stays_on_v1_while_anyone_may_read_the_bucket(
        self, storage, monkeypatch
    ):
        storage.policy = {"Statement": {"Effect": "Allow", "Principal": "*"}}
        # Estimates don't ask the bucket; a run's creation does.
        assert broker.configured()
        assert not broker.configured(probe=True)
        assert evidence_store.probe() == "bucket_is_public"

        storage.policy = None
        assert broker.configured(probe=True)
        asked = storage.calls.count("get_bucket_policy")
        assert broker.configured(probe=True)
        # Checked once per process.
        assert storage.calls.count("get_bucket_policy") == asked

        # A bucket that does not answer holds up no run's creation: its first
        # use decides.
        evidence_store._checked_buckets.clear()
        stuck = threading.Event()

        def unanswered(bucket):
            stuck.wait(10)
            raise StorageError("RequestTimeout")

        monkeypatch.setattr(storage, "get_bucket_policy", unanswered)
        monkeypatch.setattr(evidence_store, "POLICY_PROBE_S", 0.1)
        started = time.monotonic()
        try:
            assert broker.configured(probe=True)
            assert time.monotonic() - started < 2
        finally:
            stuck.set()

    def test_a_misconfigured_store_fails_the_step_for_good(self, settings, world):
        settings.INSIGHTS_EVIDENCE_STORE = "object"
        with pytest.raises(llm.InsightModelError) as error:
            broker.prebuild(world.run, [row(17, "T1")], stage="read")
        assert not error.value.unavailable
        assert world.builds == 0

    def test_a_bucket_found_open_on_first_use_fails_the_step_for_good(
        self, storage, world
    ):
        storage.policy = {"Statement": {"Effect": "Allow", "Principal": "*"}}
        for stage in ("read", "report"):
            with pytest.raises(llm.InsightModelError) as error:
                broker.prebuild(world.run, [row(17, "T1")], stage=stage)
            assert not error.value.unavailable
        assert storage.objects == {}


class TestObjectStore:
    def test_one_gzip_document_per_unit_under_the_runs_prefix(self, storage, world):
        built = broker.build(world.run, row(17, "T1"))
        size = store().put(RUN_ID, "17", built)

        name = object_name(RUN_ID, "17")
        assert list(storage.objects) == [(BUCKET, name)]
        stored = storage.objects[(BUCKET, name)]
        assert len(stored) == size
        document = {"document": built.document, "media_sources": built.media_sources}
        assert gzip.decompress(stored).decode() == evidence.canonical_json(document)
        from minio.sse import SseS3

        upload = storage.uploads[(BUCKET, name)]
        assert upload["content_type"] == "application/gzip"
        assert isinstance(upload["sse"], SseS3)

        # Another pod: nothing held, read from the bucket once.
        evidence_store._recent.clear()
        got = store().get(RUN_ID, "17")
        assert got.document == built.document
        assert got.media_sources == built.media_sources
        assert store().get(RUN_ID, "17").document == built.document
        assert storage.calls.count("get_object") == 1
        assert store().has(RUN_ID, "17") and not store().has(RUN_ID, "18")

    @pytest.mark.parametrize("backend", ["gcs", "minio"])
    def test_encryption_is_asked_for_only_where_the_backend_takes_it(
        self, storage, world, monkeypatch, backend
    ):
        from tfc.utils import storage_client

        monkeypatch.setattr(storage_client, "STORAGE_BACKEND", backend)
        store().put(RUN_ID, "17", broker.build(world.run, row(17, "T1")))
        assert storage.uploads[(BUCKET, object_name(RUN_ID, "17"))]["sse"] is None

    def test_a_document_that_fails_its_checks_is_never_served(self, storage, world):
        built = broker.build(world.run, row(17, "T1"))
        store().put(RUN_ID, "17", built)
        name = object_name(RUN_ID, "17")
        original = storage.objects[(BUCKET, name)]
        stored = json.loads(gzip.decompress(original))
        span_alias = next(iter(stored["document"]["fields"]))
        field = next(iter(stored["document"]["fields"][span_alias]))
        stored["document"]["fields"][span_alias][field] += " (edited)"
        tampered = gzip.compress(evidence.canonical_json(stored).encode())

        for content, key in (
            (tampered, "17"),
            (original[:-20], "17"),
            (b"not gzip", "17"),
            # Unit 17's document where unit 18's belongs.
            (original, "18"),
        ):
            evidence_store._recent.clear()
            storage.objects[(BUCKET, object_name(RUN_ID, key))] = content
            assert store().get(RUN_ID, key) is None
            assert (BUCKET, object_name(RUN_ID, key)) not in storage.objects

    def test_a_storage_error_is_unavailable_never_missing(self, storage, world):
        built = broker.build(world.run, row(17, "T1"))
        for op, call in (
            ("get_object", lambda s: s.get(RUN_ID, "17")),
            ("stat_object", lambda s: s.has(RUN_ID, "17")),
            ("put_object", lambda s: s.put(RUN_ID, "17", built)),
        ):
            storage.fail = {op}
            with pytest.raises(evidence_store.EvidenceStoreUnavailable) as error:
                call(store())
            assert error.value.code == "cache_unavailable"

    def test_delete_run_takes_that_run_only(self, storage, world):
        built = broker.build(world.run, row(17, "T1"))
        for run_id, key in ((RUN_ID, "17"), (RUN_ID, "18"), (OTHER_RUN, "17")):
            store().put(run_id, key, built, remember=True)

        evidence_store.delete_run(RUN_ID)

        assert storage.runs() == {OTHER_RUN}
        assert set(evidence_store._held_runs()) == {OTHER_RUN}

    def test_the_sweep_deletes_what_ended_and_keeps_what_is_read(
        self, storage, world, monkeypatch
    ):
        now = datetime.now(UTC)
        built = broker.build(world.run, row(17, "T1"))
        runs = {
            "failed": RunState("failed", False, now, now),
            "deleted": RunState("completed", True, now, now),
            "gone": None,
            "stuck": RunState("running", False, now - timedelta(hours=7)),
            "running": RunState("running", False, now - timedelta(hours=1)),
            "asked": RunState("completed", False, now, now - timedelta(hours=3)),
            "just_done": RunState("completed", False, now, now - timedelta(minutes=1)),
            "done": RunState("completed", False, now, now - timedelta(hours=1)),
        }
        ids = {name: str(uuid.uuid4()) for name in runs}
        for run_id in ids.values():
            store().put(run_id, "17", built)
        evidence_store.hold(ids["asked"], 300)
        states = {ids[n]: s for n, s in runs.items() if s is not None}
        monkeypatch.setattr(evidence_store, "_run_states", lambda wanted: states)

        evidence_store.sweep(force=True, wait=True)

        kept = {name for name, run_id in ids.items() if run_id in storage.runs()}
        assert kept == {"running", "asked", "just_done"}

        # The database can't say: nothing goes (the lifecycle rule remains).
        monkeypatch.setattr(evidence_store, "_run_states", lambda wanted: None)
        states.clear()
        evidence_store.sweep(force=True, wait=True)
        assert len(storage.runs()) == 3

    def test_a_bucket_is_swept_off_the_calling_thread(self, storage, monkeypatch):
        ran, threads = threading.Event(), []

        def apart(store, now):
            threads.append(threading.current_thread())
            ran.set()

        monkeypatch.setattr(evidence_store, "_sweep_apart", apart)
        monkeypatch.setattr(evidence_store, "_swept_at", 0.0)
        evidence_store.sweep()
        assert ran.wait(5)
        assert threads[0] is not threading.current_thread()


class TestLocalStore:
    def test_a_unit_is_one_gzip_document_and_a_bad_one_is_rebuilt(self, world):
        first = broker.snapshot(world.run, row(17, "T1"))
        path = store().path(RUN_ID, "17")
        assert path.name == object_name(RUN_ID, "17").rsplit("/", 1)[1]
        stored = json.loads(gzip.decompress(path.read_bytes()))
        assert stored["document"] == first.document

        evidence_store._recent.clear()
        path.write_bytes(gzip.compress(b'{"document": {}, "media_sources": {}}'))
        again = broker.snapshot(world.run, row(17, "T1"))
        assert world.builds == 2
        assert (
            again.document["manifest"]["digest"] == first.document["manifest"]["digest"]
        )

    def test_a_worker_that_writes_expires_its_files_too(self, world, monkeypatch):
        armed = []
        monkeypatch.setattr(evidence_store, "_arm_sweeper", lambda: armed.append(1))
        broker.prebuild(world.run, [row(17, "T1")], stage="read")
        assert store().path(RUN_ID, "17").exists()
        # Nothing held in memory, and still the timer sweeps this host.
        assert not evidence_store._recent and armed


class TestPrebuild:
    def test_a_reading_builds_what_the_store_lacks_two_at_a_time(
        self, storage, world, monkeypatch
    ):
        real, active, peak, lock = broker.build, [0], [0], threading.Lock()

        def build(run, unit):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                time.sleep(0.05)
                if unit.unit_id == "TX":
                    raise EvidenceUnavailable("deadline_exceeded")
                return real(run, unit)
            finally:
                with lock:
                    active[0] -= 1

        monkeypatch.setattr(broker, "build", build)
        rows = [row(17, "T1"), row(18, "T2"), row(19, "T9"), row(20, "TX")]
        rows.append(row(21, "T1"))

        built = broker.prebuild(world.run, rows, stage="read")

        assert built.ready == ["17", "18", "21"]
        assert built.failed == {"19": "unit_not_found", "20": "deadline_exceeded"}
        assert peak[0] == broker.PREBUILD_WORKERS == 2
        assert storage.runs() == {RUN_ID}
        assert {name for _, name in storage.objects} == {
            object_name(RUN_ID, key) for key in built.ready
        }
        uploads = storage.calls.count("put_object")

        # A retried batch builds only what is not stored.
        again = broker.prebuild(world.run, rows, stage="read")
        assert again == built
        assert storage.calls.count("put_object") == uploads

    def test_a_reading_the_store_does_not_take_is_retried(self, storage, world):
        storage.fail = {"put_object"}
        with pytest.raises(llm.InsightModelError) as error:
            broker.prebuild(world.run, [row(17, "T1")], stage="read")
        assert error.value.unavailable

    def test_a_reading_on_a_local_store_builds_too(self, world):
        built = broker.prebuild(world.run, [row(17, "T1"), row(19, "T9")], stage="read")
        assert (built.ready, built.failed) == (["17"], {"19": "unit_not_found"})
        assert store().path(RUN_ID, "17").exists()

    def test_reports_and_questions_build_only_on_a_shared_store(self, world):
        for stage in ("report", "ask"):
            built = broker.prebuild(world.run, [row(17, "T1")], stage=stage)
            assert built.ready == ["17"]
        assert world.builds == 0

    def test_a_report_rebuilds_what_is_missing_and_leaves_out_the_rest(
        self, storage, world
    ):
        broker.prebuild(world.run, [row(18, "T2")], stage="read")
        rows = [row(17, "T1"), row(18, "T2"), row(19, "T9")]
        built = broker.prebuild(world.run, rows, stage="report")
        assert (built.ready, built.failed) == (["17", "18"], {"19": "unit_not_found"})

        # A unit the bucket would not take is one investigators can't open.
        storage.fail = {"put_object"}
        evidence_store._recent.clear()
        built = broker.prebuild(world.run, [row(21, "T1")], stage="report")
        assert built == broker.Prebuilt([], {})

    def test_a_question_waits_a_while_and_holds_the_runs_documents(
        self, storage, world, monkeypatch
    ):
        world.run.status = "completed"
        real, release = broker.build, threading.Event()

        def build(run, unit):
            if unit.unit_id == "T2":
                release.wait(5)
            return real(run, unit)

        monkeypatch.setattr(broker, "build", build)
        monkeypatch.setattr(broker, "ASK_PREBUILD_S", 0.3)

        built = broker.prebuild(
            world.run, [row(17, "T1"), row(18, "T2")], stage="ask", ttl_s=180
        )

        assert built == broker.Prebuilt(["17"], {})
        assert evidence_store.held(RUN_ID)
        # The late build finishes on its own and is stored for the next one.
        release.set()
        deadline = time.time() + 5
        while (BUCKET, object_name(RUN_ID, "18")) not in storage.objects:
            assert time.time() < deadline
            time.sleep(0.02)

    def test_a_question_stores_what_only_this_pod_holds(self, storage, world):
        rows = [row(17, "T1"), row(18, "T2")]
        broker.prebuild(world.run, rows, stage="read")
        # This pod served unit 17; finishing then deleted the run's documents
        # from the worker, which this pod's memory never hears of.
        evidence_store._recent.clear()
        store().get(RUN_ID, "17")
        storage.objects.clear()
        world.run.status = "completed"
        builds = world.builds

        built = broker.prebuild(world.run, rows, stage="ask", ttl_s=1)

        # Both in the bucket for whichever pod the agent reaches; 17 from
        # memory, not built again.
        assert built.ready == ["17", "18"]
        assert {name for _, name in storage.objects} == {
            object_name(RUN_ID, key) for key in ("17", "18")
        }
        assert world.builds == builds + 1

        # The broker's own build, held in memory when the bucket refused it.
        world.run.status = "running"
        storage.fail = {"put_object"}
        broker.snapshot(world.run, row(21, "T1"))
        storage.fail = set()
        world.run.status = "completed"
        built = broker.prebuild(world.run, [row(21, "T1")], stage="ask", ttl_s=1)
        assert built.ready == ["21"]
        assert (BUCKET, object_name(RUN_ID, "21")) in storage.objects
        assert world.builds == builds + 2

    def test_questions_build_as_the_broker_does(self, storage, world, monkeypatch):
        world.run.status = "completed"
        real, lock, release = broker.build, threading.Lock(), threading.Event()
        active, peak, built = [0], [0], []

        def build(run, unit):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                built.append(unit.pk)
            try:
                release.wait(5)
                time.sleep(0.1)
                return real(run, unit)
            finally:
                with lock:
                    active[0] -= 1

        monkeypatch.setattr(broker, "build", build)
        rows = [row(17, "T1"), row(18, "T2"), row(21, "T1")]
        answers = []

        def ask():
            answers.append(broker.prebuild(world.run, rows, stage="ask", ttl_s=1))

        questions = [threading.Thread(target=ask) for _ in range(4)]
        for question in questions:
            question.start()
        time.sleep(0.2)
        release.set()
        for question in questions:
            question.join(10)

        # Four questions at once: the pod's two build turns, each unit once.
        assert peak[0] <= broker.MAX_BUILDS
        assert sorted(built) == [17, 18, 21]
        assert [answer.ready for answer in answers] == [["17", "18", "21"]] * 4
        assert storage.calls.count("put_object") == 3

    def test_a_question_with_no_turn_to_build_leaves_the_unit_out(
        self, storage, world, monkeypatch
    ):
        world.run.status = "completed"
        monkeypatch.setattr(broker, "_builds", threading.BoundedSemaphore(1))
        monkeypatch.setattr(broker, "SLOT_WAIT_S", 0.05)
        broker._builds.acquire()
        try:
            built = broker.prebuild(world.run, [row(17, "T1")], stage="ask", ttl_s=1)
        finally:
            broker._builds.release()
        # Not openable this time, not failed: the next question builds it.
        assert built == broker.Prebuilt([], {})
        assert world.builds == 0

        # The worker's reading is bounded per batch, not by the pod's turns.
        broker._builds.acquire()
        try:
            world.run.status = "running"
            built = broker.prebuild(world.run, [row(17, "T1")], stage="read")
        finally:
            broker._builds.release()
        assert built.ready == ["17"]

    def test_a_unit_that_cannot_be_stored_fails_alone(
        self, storage, world, monkeypatch
    ):
        # A media URL whose JSON escape left a lone surrogate.
        content = [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/a\ud800.png"},
            }
        ]
        message = {"messages": [{"role": "user", "content": content}]}
        world.analytics.rows.append(
            span("T5", "root", input=json.dumps(message), output="ok")
        )
        real = evidence_store.ObjectStore.put

        def put(self, run_id, key, cached, *, remember=False):
            if key == "18":
                raise TypeError("not canonical JSON")
            return real(self, run_id, key, cached, remember=remember)

        monkeypatch.setattr(evidence_store.ObjectStore, "put", put)
        rows = [row(30, "T5"), row(17, "T1"), row(18, "T2")]

        built = broker.prebuild(world.run, rows, stage="read")

        assert (built.ready, built.failed) == (["30", "17"], {"18": "internal_error"})
        # Stored as the rendered text would be: the surrogate replaced.
        sources = store().get(RUN_ID, "30").media_sources
        assert sources["m1"][1] == "https://example.com/a�.png"


class TestCutoff:
    def test_a_rebuild_has_the_digest_of_the_first_build(self, storage, world):
        broker.prebuild(world.run, [row(17, "T1")], stage="read")
        evidence_store._recent.clear()
        first = store().get(RUN_ID, "17").document["manifest"]["digest"]

        # Ingested after the run was created, then the stored copy is lost.
        late = span("T1", "late", at=2, parent="root", output="after the run began")
        world.analytics.rows.append(
            {**late, "_version": evidence.version_of(CREATED) + 1}
        )
        storage.objects.clear()
        evidence_store._recent.clear()

        rebuilt = broker.snapshot(world.run, row(17, "T1"))

        assert world.builds == 2
        assert rebuilt.document["manifest"]["digest"] == first
        assert rebuilt.document["manifest"]["built_at"] == "2026-10-20T08:00:00.000000Z"
        assert (BUCKET, object_name(RUN_ID, "17")) in storage.objects


class TestBrokerFromStore:
    def post(self, op, body, capability):
        request = RequestFactory().post(
            f"/tracer/internal/insights/evidence/{op}",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {capability}",
        )
        return InsightEvidenceView.as_view(op=op)(request)

    def test_the_views_serve_what_the_worker_stored(self, storage, world):
        broker.prebuild(world.run, [row(17, "T1")], stage="read")
        assert world.builds == 1
        # Another pod, holding nothing.
        evidence_store._recent.clear()
        capability = token(keys=["17"])

        manifest = self.post("manifest", {"unit": "17"}, capability)
        document = json.loads(manifest.content)["manifest"]
        root = next(s for s in document["spans"] if s["parent"] is None)
        fields = self.post(
            "fields",
            {
                "unit": "17",
                "items": [{"span": root["alias"], "field": "output"}],
                "max_bytes": 1 << 20,
            },
            capability,
        )
        image = self.post("media", {"unit": "17", "media": "m1"}, capability)

        assert [r.status_code for r in (manifest, fields, image)] == [200] * 3
        assert json.loads(fields.content)["fields"][root["alias"]]["output"] == (
            "Yes, the box is crushed."
        )
        assert image["Content-Type"] == "image/png"
        # Nothing built on the web pod; the bucket read once.
        assert world.builds == 1
        assert storage.calls.count("get_object") == 1

    def test_a_running_runs_missing_unit_is_built_once_and_stored(self, storage, world):
        capability = token(keys=["17"])
        for _ in range(2):
            assert self.post("manifest", {"unit": "17"}, capability).status_code == 200
        assert world.builds == 1
        assert (BUCKET, object_name(RUN_ID, "17")) in storage.objects

    def test_a_questions_missing_unit_is_not_found(self, storage, world):
        world.run.status = "completed"
        response = self.post("manifest", {"unit": "17"}, token(stage="ask"))
        assert response.status_code == 404
        assert json.loads(response.content) == {"error": {"code": "unit_not_found"}}
        assert world.builds == 0

    def test_a_store_that_cannot_answer_is_a_transient_503(self, storage, world):
        storage.fail = {"get_object"}
        response = self.post("manifest", {"unit": "17"}, token(keys=["17"]))
        assert response.status_code == 503
        assert json.loads(response.content) == {"error": {"code": "cache_unavailable"}}
        assert world.builds == 0

    def test_a_busy_broker_says_when_to_ask_again(self, world, monkeypatch):
        def busy(run, unit):
            raise broker.BrokerError(503, "broker_busy")

        monkeypatch.setattr(broker, "snapshot", busy)
        response = self.post("manifest", {"unit": "17"}, token(keys=["17"]))
        assert response.status_code == 503
        assert response["Retry-After"] == "1"


class TestCleanup:
    def test_the_worker_deletes_a_runs_documents_from_the_bucket(self, storage, world):
        broker.prebuild(world.run, [row(17, "T1"), row(18, "T2")], stage="read")
        assert storage.runs() == {RUN_ID}

        pipeline._forget_evidence(RUN_ID)

        assert storage.objects == {}
        assert not evidence_store._held_runs()

    def test_and_from_its_own_directory(self, world):
        broker.prebuild(world.run, [row(17, "T1")], stage="read")
        assert store().run_dir(RUN_ID).is_dir()
        pipeline._forget_evidence(RUN_ID)
        assert not store().run_dir(RUN_ID).exists()

    def test_a_slow_delete_stops_at_its_budget_and_the_sweep_finishes(
        self, storage, world, monkeypatch
    ):
        built = broker.build(world.run, row(17, "T1"))
        for key in ("17", "18", "21"):
            store().put(RUN_ID, key, built)
        asked = []
        real = evidence_store.delete_run

        def delete_run(run_id, *, budget_s=None):
            asked.append(budget_s)
            real(run_id, budget_s=-1)

        monkeypatch.setattr(evidence_store, "delete_run", delete_run)
        pipeline._forget_evidence(RUN_ID)
        # The fail stage has 30 s in all.
        assert asked == [pipeline.EVIDENCE_DELETE_S] and asked[0] < 30
        assert len(storage.objects) == 3

        world.run.status = "failed"
        evidence_store.sweep(force=True, wait=True)
        assert storage.objects == {}

    def test_a_store_that_does_not_answer_holds_up_no_stage(
        self, storage, world, monkeypatch
    ):
        store().put(RUN_ID, "17", broker.build(world.run, row(17, "T1")))
        stuck = threading.Event()

        def unanswered(bucket, prefix=None, recursive=False):
            stuck.wait(10)
            return []

        monkeypatch.setattr(storage, "list_objects", unanswered)
        monkeypatch.setattr(pipeline, "EVIDENCE_DELETE_S", 0.2)
        started = time.monotonic()
        try:
            pipeline._forget_evidence(RUN_ID)
            # Back within its budget although the storage client still waits.
            assert time.monotonic() - started < 2
        finally:
            stuck.set()

    def test_cleanup_never_fails_the_stage(self, storage, settings):
        storage.fail = {"list_objects"}
        pipeline._forget_evidence(RUN_ID)
        settings.INSIGHTS_EVIDENCE_BUCKET = ""
        pipeline._forget_evidence(RUN_ID)
