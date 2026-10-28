"""The Insights evidence broker without a database: capabilities, the checks
every call makes against its run, the 'local' evidence store it builds into
and serves from, field pages, media views and the internal views that serve
them (PROTOCOL_V2 section 3).

Runs and units are plain objects behind the broker's two loaders; snapshots
are built by the real builder over ``test_evidence.FakeClickHouse``.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import stat
import threading
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.test import RequestFactory
from PIL import Image

from ee.agenthub.insights import broker, evidence, evidence_store
from ee.agenthub.insights.evidence import EvidenceUnavailable, verify_snapshot
from ee.agenthub.insights.tests.test_evidence import (
    ORG,
    PNG,
    PROJECT,
    FakeClickHouse,
    span,
)
from tracer.views.insights_evidence import InsightEvidenceView

RUN_ID = "88888888-8888-4888-8888-888888888888"
CREATED = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
ARM_SWEEPER = evidence_store._arm_sweeper


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
    # Each test starts with nothing held, and sweeps only when it says so.
    evidence_store._recent.clear()
    monkeypatch.setattr(evidence_store, "_swept_at", 0.0)
    monkeypatch.setattr(evidence_store, "_arm_sweeper", lambda: None)
    return settings


def make_run(**fields):
    run = {
        "id": uuid.UUID(RUN_ID),
        "organization_id": uuid.UUID(ORG),
        "project_id": uuid.UUID(PROJECT),
        "unit": "traces",
        "status": "running",
        "scope": {},
        "created_at": CREATED,
    }
    run.update(fields)
    return SimpleNamespace(**run)


@pytest.fixture
def world(monkeypatch):
    """One run with units 17 (trace T1) and 18 (trace T2), served from the
    fake ClickHouse; ``world.builds`` counts snapshot builds."""
    image = f"data:image/png;base64,{PNG}"
    rows = [
        span(
            "T1",
            "root",
            input=json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Is this damaged?"},
                                {"type": "image_url", "image_url": {"url": image}},
                            ],
                        }
                    ]
                }
            ),
            output="Yes, the box is crushed.",
        ),
        span("T1", "tool", at=1, parent="root", output="refund=declined"),
        span("T2", "root", output="other trace"),
    ]
    state = SimpleNamespace(
        run=make_run(), builds=0, analytics=FakeClickHouse(rows), others={}
    )
    units = {"17": "T1", "18": "T2"}

    def load_unit(run, key):
        if key not in units:
            return None
        return SimpleNamespace(pk=int(key), unit_id=units[key])

    def analytics():
        state.builds += 1
        return state.analytics

    monkeypatch.setattr(
        broker, "_load_run", lambda run_id: state.run if run_id == RUN_ID else None
    )
    monkeypatch.setattr(broker, "_load_unit", load_unit)
    monkeypatch.setattr(broker, "_analytics", analytics)
    monkeypatch.setattr(broker, "_lookup_ids", lambda kind, key: [key])

    def run_states(ids):
        known = {RUN_ID: (state.run.status, False), **state.others}
        return {i: known[i] for i in ids if i in known}

    monkeypatch.setattr(evidence_store, "_run_states", run_states)
    return state


def token(run=None, keys=("17",), stage="read", ttl_s=120, now=None):
    return broker.mint(run or make_run(), list(keys), stage=stage, ttl_s=ttl_s, now=now)


def run_dir():
    return evidence_store.current().run_dir(RUN_ID)


def unit_path(key):
    return evidence_store.current().path(RUN_ID, key)


def stored_text(path):
    return gzip.decompress(path.read_bytes()).decode()


def claims_of(capability):
    payload = capability.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class TestCapability:
    def test_the_token_is_ie1_claims_and_signature_without_padding(self):
        capability = token(keys=["17", "18"])
        version, payload, signature = capability.split(".")

        assert version == "ie1"
        assert "=" not in payload + signature
        claims = claims_of(capability)
        assert claims == {
            "v": 1,
            "run": RUN_ID,
            "org": ORG,
            "project": PROJECT,
            "kind": "trace",
            "units": ["17", "18"],
            "ops": ["manifest", "fields", "media"],
            "stage": "read",
            "exp": claims["exp"],
            "max_bytes": 2 * broker.UNIT_BYTES,
            "max_calls": 2 * broker.UNIT_CALLS,
        }
        # Compact JSON with sorted keys, as the protocol signs it.
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        assert raw == json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        assert broker.verify(capability) == claims
        assert claims["exp"] - time.time() == pytest.approx(180, abs=5)

    def test_tampered_foreign_or_expired_tokens_are_refused(self, settings):
        capability = token()
        version, payload, signature = capability.split(".")
        claims = claims_of(capability)
        claims["units"] = ["17", "99"]
        forged = base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        for bad in (
            f"{version}.{forged.decode().rstrip('=')}.{signature}",
            f"ie2.{payload}.{signature}",
            capability + "x",
            "",
            None,
            "a.b",
        ):
            with pytest.raises(broker.BrokerError) as refused:
                broker.verify(bad)
            assert (refused.value.status, refused.value.code) == (
                401,
                "capability_invalid",
            )

        expired = token(now=time.time() - 3600)
        with pytest.raises(broker.BrokerError) as refused:
            broker.verify(expired)
        assert refused.value.code == "capability_expired"

        settings.INSIGHTS_EVIDENCE_SECRET = "rotated-secret"
        with pytest.raises(broker.BrokerError):
            broker.verify(capability)
        # While the new secret rolls out, the previous one still verifies.
        settings.INSIGHTS_EVIDENCE_SECRET_PREVIOUS = "current-secret"
        assert broker.verify(capability)["units"] == ["17"]

    def test_unit_counts_and_token_sizes_are_bounded(self):
        with pytest.raises(ValueError):
            token(keys=[str(i) for i in range(9)])
        with pytest.raises(ValueError):
            token(keys=[])
        # Eight 128-character keys fit the read token's 4,096 characters.
        assert len(token(keys=["k" * 120 + str(i) for i in range(8)])) <= 4_096
        report = token(keys=[str(i) for i in range(64)], stage="report", ttl_s=420)
        assert len(report) <= 16_384
        claims = claims_of(report)
        assert claims["stage"] == "report"
        # Investigators open a few units: the whole token is capped too.
        assert (claims["max_bytes"], claims["max_calls"]) == (512 * 1024**2, 1_024)

    def test_a_token_outside_its_alphabet_is_refused_before_hashing(self):
        capability = token()
        version, payload, signature = capability.split(".")
        for bad in (f"{version}.{payload}é.{signature}", f"ie1.{payload}.{signature}="):
            with pytest.raises(broker.BrokerError) as refused:
                broker.verify(bad)
            assert refused.value.code == "capability_invalid"

    def test_minting_needs_the_secret(self, settings):
        from ee.agenthub.insights.llm import InsightModelError

        settings.INSIGHTS_EVIDENCE_SECRET = ""
        assert not broker.configured()
        with pytest.raises(InsightModelError) as error:
            token()
        assert not error.value.unavailable


class TestAuthorize:
    def refused(self, claims, op="manifest", unit="17"):
        with pytest.raises(broker.BrokerError) as refused:
            broker.authorize(claims, op, unit)
        return refused.value.status, refused.value.code

    def test_the_op_unit_run_scope_and_status_are_checked(self, world):
        claims = broker.verify(token())
        run, row = broker.authorize(claims, "fields", "17")
        assert (run, row.unit_id) == (world.run, "T1")

        assert self.refused({**claims, "ops": ["manifest"]}, op="media") == (
            403,
            "op_not_allowed",
        )
        assert self.refused(claims, unit="18") == (403, "unit_not_in_capability")
        assert self.refused(claims, unit=["17"]) == (403, "unit_not_in_capability")
        assert self.refused({**claims, "run": str(uuid.uuid4())}) == (
            403,
            "run_not_found",
        )
        assert self.refused({**claims, "run": "not-a-uuid"}) == (403, "run_not_found")
        for field, value in (("org", str(uuid.uuid4())), ("kind", "session")):
            assert self.refused({**claims, field: value}) == (403, "scope_mismatch")
        assert self.refused({**claims, "units": ["17", "99"]}, unit="99") == (
            403,
            "unit_not_in_run",
        )
        world.run.project_id = uuid.uuid4()
        assert self.refused(claims) == (403, "scope_mismatch")

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_a_run_that_is_not_running_refuses_and_drops_its_cache(self, world, status):
        claims = broker.verify(token())
        run, row = broker.authorize(claims, "manifest", "17")
        broker.snapshot(run, row)
        assert run_dir().is_dir()

        world.run.status = status
        assert self.refused(claims) == (409, "run_not_running")
        assert not run_dir().exists()

    def test_a_question_reads_a_completed_report_only(self, world):
        world.run.status = "completed"
        claims = broker.verify(token(stage="ask"))
        assert broker.authorize(claims, "manifest", "17")[1].unit_id == "T1"
        world.run.status = "running"
        assert self.refused(claims) == (409, "run_not_running")


class TestBudget:
    def test_calls_and_bytes_are_spent_per_token(self):
        capability = token()
        claims = {**broker.verify(capability), "max_calls": 2, "max_bytes": 100}
        assert broker.take_call(capability, claims) == 100
        broker.spend_bytes(capability, claims, 60)
        assert broker.take_call(capability, claims) == 40
        with pytest.raises(broker.BrokerError) as refused:
            broker.take_call(capability, claims)
        assert (refused.value.status, refused.value.code) == (
            429,
            "capability_budget_spent",
        )
        # Another token has its own budget.
        other = token(keys=["18"])
        assert broker.take_call(other, broker.verify(other)) == broker.UNIT_BYTES

    def test_spent_bytes_refuse_the_next_call(self):
        capability = token()
        claims = {**broker.verify(capability), "max_bytes": 10}
        broker.spend_bytes(capability, claims, 10)
        with pytest.raises(broker.BrokerError):
            broker.take_call(capability, claims)


class TestSnapshotCache:
    def test_built_once_per_run_and_unit_under_its_run_key(self, world):
        run = world.run
        row = SimpleNamespace(pk=17, unit_id="T1")
        first = broker.snapshot(run, row)
        manifest = first.document["manifest"]

        assert manifest["unit"] == {
            "kind": "trace",
            "key": "17",
            "project_id": PROJECT,
            "unit_id": "T1",
        }
        # Pinned to the run's creation, so every pod builds the same digest.
        assert manifest["built_at"] == "2026-10-20T08:00:00.000000Z"
        assert verify_snapshot(first.document)
        path = unit_path("17")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(evidence_store.cache_root()).st_mode) == 0o700
        # The cache file holds where media lives; the manifest never does.
        assert PNG not in json.dumps(manifest)
        assert first.media_sources["m1"][0] == "data_uri"

        again = broker.snapshot(run, row)
        assert world.builds == 1
        assert again.document == first.document

        evidence_store._recent.clear()
        broker.forget_run(RUN_ID)
        rebuilt = broker.snapshot(run, row)
        assert world.builds == 2
        assert rebuilt.document["manifest"]["digest"] == manifest["digest"]

    def test_a_planted_cache_directory_is_never_read(self, world, tmp_path):
        planted = tmp_path / "elsewhere"
        planted.mkdir()
        os.symlink(planted, evidence_store.cache_root())
        row = SimpleNamespace(pk=17, unit_id="T1")
        (planted / RUN_ID).mkdir()
        target = planted / RUN_ID / unit_path("17").name
        target.write_text(json.dumps({"document": {}, "media_sources": {}}))

        built = broker.snapshot(world.run, row)
        assert built.document["manifest"]["unit"]["key"] == "17"
        # Built and served from memory: the link was neither read nor written.
        assert world.builds == 1
        assert json.loads(target.read_text()) == {"document": {}, "media_sources": {}}

    def test_built_as_ingested_when_the_run_was_created(self, world):
        late = span("T1", "late", at=2, parent="root", output="after the run began")
        world.analytics.rows.append(
            {**late, "_version": evidence.version_of(CREATED) + 1}
        )

        built = broker.snapshot(world.run, SimpleNamespace(pk=17, unit_id="T1"))

        names = [s["name"] for s in built.document["manifest"]["spans"]]
        assert names == ["root", "tool"]
        assert all(
            c.params["evidence_version_cutoff"] == evidence.version_of(CREATED)
            for c in world.analytics.calls
        )

    def test_ended_and_expired_runs_are_swept_without_a_write(self, world):
        row = SimpleNamespace(pk=17, unit_id="T1")
        broker.snapshot(world.run, row)
        root = evidence_store.cache_root()
        now = time.time()

        def make_dir(status, idle_s):
            run_id = str(uuid.uuid4())
            path = root / run_id
            path.mkdir(mode=0o700)
            (path / "x.json").write_bytes(b"x")
            os.utime(path, (now - idle_s, now - idle_s))
            if status:
                world.others[run_id] = status
            return path

        expired = make_dir(("running", False), evidence_store.CACHE_TTL_S + 60)
        failed = make_dir(("failed", False), 10)
        deleted = make_dir(("completed", True), 10)
        gone = make_dir(None, 10)
        done_idle = make_dir(("completed", False), evidence_store.ENDED_GRACE_S + 60)
        done_asked = make_dir(("completed", False), 10)
        running = make_dir(("running", False), 60)

        broker.sweep()

        for path in (expired, failed, deleted, gone, done_idle):
            assert not path.exists(), path
        for path in (done_asked, running, run_dir()):
            assert path.is_dir(), path

        # The run ends: its files and what this process holds of it go.
        world.run.status = "failed"
        broker.sweep()
        assert run_dir().is_dir()  # at most once a minute
        evidence_store.sweep(force=True)
        assert not run_dir().exists()
        assert not evidence_store._recent

    def test_without_the_database_age_alone_decides(self, world, monkeypatch):
        broker.snapshot(world.run, SimpleNamespace(pk=17, unit_id="T1"))
        monkeypatch.setattr(evidence_store, "_run_states", lambda ids: None)
        broker.sweep()
        assert run_dir().is_dir()

        stale = time.time() - evidence_store.CACHE_TTL_S - 60
        os.utime(run_dir(), (stale, stale))
        with evidence_store._recent_lock:
            for held in evidence_store._recent.values():
                held.at = stale
        evidence_store.sweep(force=True)
        assert not run_dir().exists()

    def test_a_timer_sweeps_while_the_cache_holds_anything(self, world, monkeypatch):
        monkeypatch.setattr(evidence_store, "_arm_sweeper", ARM_SWEEPER)
        monkeypatch.setattr(evidence_store, "SWEEP_INTERVAL_S", 0.05)
        broker.snapshot(world.run, SimpleNamespace(pk=17, unit_id="T1"))
        timer = evidence_store._sweeper
        assert timer is not None and timer.daemon

        world.run.status = "failed"
        deadline = time.time() + 5
        while run_dir().exists() and time.time() < deadline:
            time.sleep(0.02)
        assert not run_dir().exists()
        # Nothing left to expire: the timer stops.
        while evidence_store._sweeper is not None and time.time() < deadline:
            time.sleep(0.02)
        assert evidence_store._sweeper is None

    def test_a_stale_file_is_not_read(self, world):
        row = SimpleNamespace(pk=17, unit_id="T1")
        broker.snapshot(world.run, row)
        evidence_store._recent.clear()
        stale = time.time() - evidence_store.CACHE_TTL_S - 60
        os.utime(unit_path("17"), (stale, stale))
        broker.snapshot(world.run, row)
        assert world.builds == 2

    def test_the_cap_holds_for_the_run_being_read_too(self, world, settings):
        row = SimpleNamespace(pk=17, unit_id="T1")
        need = len(evidence_store._pack(broker.build(world.run, row))[0])
        size = need + 200
        world.builds = 0
        directory = evidence_store._private_dir(run_dir())
        evidence_store._private_dir(evidence_store.cache_root())
        older = []
        for age in (300, 200, 100):
            path = directory / f"{age}.json.gz"
            path.write_bytes(b"x" * size)
            os.utime(path, (time.time() - age, time.time() - age))
            older.append(path)
        # Room for two of them and the unit's document, not three.
        settings.INSIGHTS_EVIDENCE_CACHE_MAX_BYTES = 3 * size + need - 1

        broker.snapshot(world.run, row)

        # The oldest files of this very run made room.
        assert [p.exists() for p in older] == [False, True, True]
        assert unit_path("17").exists()

    def test_a_unit_the_disk_cannot_take_is_held_in_memory(self, world, settings):
        settings.INSIGHTS_EVIDENCE_CACHE_MAX_BYTES = 10
        row = SimpleNamespace(pk=17, unit_id="T1")
        first = broker.snapshot(world.run, row)
        assert not unit_path("17").exists()

        again = broker.snapshot(world.run, row)
        assert world.builds == 1 and again.document == first.document

    def test_calls_for_a_unit_share_one_build(self, world):
        started, release = threading.Event(), threading.Event()

        def slow(kind):
            if kind == "trace_stats":
                started.set()
                release.wait(5)

        world.analytics.delay = slow
        row = SimpleNamespace(pk=17, unit_id="T1")
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(broker.snapshot(world.run, row))
            )
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        assert started.wait(5)
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(5)

        assert world.builds == 1 and len(results) == 3
        assert len({r.document["manifest"]["digest"] for r in results}) == 1

    def test_builds_wait_their_turn_then_are_refused(self, world, monkeypatch):
        monkeypatch.setattr(broker, "_builds", threading.BoundedSemaphore(1))
        monkeypatch.setattr(broker, "SLOT_WAIT_S", 0.01)
        broker._builds.acquire()
        try:
            with pytest.raises(broker.BrokerError) as refused:
                broker.snapshot(world.run, SimpleNamespace(pk=17, unit_id="T1"))
        finally:
            broker._builds.release()
        assert (refused.value.status, refused.value.code) == (503, "broker_busy")
        assert world.builds == 0

    def test_a_data_uri_too_large_to_view_is_not_kept(self, world, monkeypatch):
        monkeypatch.setattr(broker, "MAX_MEDIA_FETCH_BYTES", 10)
        built = broker.snapshot(world.run, SimpleNamespace(pk=17, unit_id="T1"))
        assert built.media_sources["m1"] == ["data_uri", None, "image/png"]
        assert PNG not in stored_text(unit_path("17"))
        with pytest.raises(broker.BrokerError) as refused:
            broker.media(built, "m1", project_id=PROJECT)
        assert (refused.value.status, refused.value.code) == (413, "oversize")

    def test_missing_and_unreadable_units_are_typed(self, world, monkeypatch):
        with pytest.raises(broker.BrokerError) as refused:
            broker.snapshot(world.run, SimpleNamespace(pk=19, unit_id="T9"))
        assert (refused.value.status, refused.value.code) == (404, "unit_not_found")

        def unavailable(run, row):
            raise EvidenceUnavailable("deadline_exceeded")

        monkeypatch.setattr(broker, "build", unavailable)
        with pytest.raises(broker.BrokerError) as refused:
            broker.snapshot(world.run, SimpleNamespace(pk=18, unit_id="T2"))
        assert (refused.value.status, refused.value.code) == (503, "deadline_exceeded")


class TestCitedImage:
    """The report page shows an image a unit's evidence cites after the run
    ended and its snapshots went."""

    @staticmethod
    def row(*evidence):
        return SimpleNamespace(pk=17, unit_id="T1", facets={"evidence": list(evidence)})

    def test_a_completed_runs_cited_image_is_built_again_and_not_stored(self, world):
        world.run.status = "completed"
        row = self.row({"kind": "media", "media": "m1", "observation": "A box."})

        image = broker.cited_image(world.run, row, "m1")

        assert image.mime == "image/png" and image.original == (1, 1)
        with Image.open(io.BytesIO(image.content)) as shown:
            assert shown.size == (1, 1)
        assert world.builds == 1
        assert not run_dir().exists()

    def test_only_media_the_evidence_cites_is_served(self, world):
        world.run.status = "completed"
        text = {"kind": "text", "quote": "Is this damaged?", "span": "s1"}
        for row, alias in (
            (self.row(text), "m1"),
            (self.row({"kind": "media", "media": "m2"}), "m1"),
            (SimpleNamespace(pk=17, unit_id="T1", facets={}), "m1"),
        ):
            with pytest.raises(broker.BrokerError) as refused:
                broker.cited_image(world.run, row, alias)
            assert (refused.value.status, refused.value.code) == (404, "unknown_media")
        assert world.builds == 0

    def test_a_cited_alias_the_rebuild_lacks_is_refused(self, world):
        world.run.status = "completed"
        row = self.row({"kind": "media", "media": "m9"})
        with pytest.raises(broker.BrokerError) as refused:
            broker.cited_image(world.run, row, "m9")
        assert refused.value.code == "unknown_media"


def cached(fields, media=(), sources=None):
    spans = [
        {"alias": alias, "fields": {name: {} for name in texts}}
        for alias, texts in fields.items()
    ]
    manifest = {"unit": {"key": "17"}, "spans": spans, "media": list(media)}
    return broker.Cached({"manifest": manifest, "fields": fields}, sources or {})


class TestFields:
    def test_items_come_back_in_order_until_the_limit(self):
        snapshot = cached(
            {"s1": {"input": "a" * 100, "output": "b" * 100}, "s2": {"input": "ü" * 50}}
        )
        items = [
            {"span": "s2", "field": "input"},
            {"span": "s1", "field": "input"},
            {"span": "s1", "field": "output"},
        ]
        whole = json.loads(broker.fields_body(snapshot, items, 1 << 20, 1 << 20))
        assert whole == {
            "fields": {
                "s2": {"input": "ü" * 50},
                "s1": {"input": "a" * 100, "output": "b" * 100},
            }
        }
        for limit in (140, 260, 300):
            body = broker.fields_body(snapshot, items, limit, 1 << 20)
            assert len(body) <= limit
            page = json.loads(body)["fields"]
            assert page["s2"] == {"input": "ü" * 50}
        first_only = broker.fields_body(snapshot, items, 140, 1 << 20)
        assert json.loads(first_only)["fields"] == {"s2": {"input": "ü" * 50}}
        # The capability's bytes left bound the page too.
        assert len(broker.fields_body(snapshot, items, 1 << 20, 260)) <= 260
        with pytest.raises(broker.BrokerError) as refused:
            broker.fields_body(snapshot, items, 1 << 20, 100)
        assert refused.value.code == "capability_budget_spent"

    def test_a_first_item_past_the_limit_and_unknown_fields_are_refused(self):
        snapshot = cached({"s1": {"input": "a" * 100}})
        cases = [
            ([{"span": "s1", "field": "input"}], 50, (413, "field_too_large")),
            ([{"span": "s1", "field": "output"}], 1000, (400, "unknown_field")),
            ([{"span": "s9", "field": "input"}], 1000, (400, "unknown_field")),
            ([{"span": ["s1"], "field": "input"}], 1000, (400, "unknown_field")),
            ([{"span": "s1", "field": ["input"]}], 1000, (400, "unknown_field")),
            ([], 1000, (400, "invalid_request")),
            ([{"span": "s1", "field": "input"}] * 2001, 1000, (400, "invalid_request")),
            (["s1"], 1000, (400, "invalid_request")),
            ([{"span": "s1", "field": "input"}], True, (400, "invalid_request")),
        ]
        for items, limit, expected in cases:
            with pytest.raises(broker.BrokerError) as refused:
                broker.fields_body(snapshot, items, limit, 1 << 20)
            assert (refused.value.status, refused.value.code) == expected


def png(size, mode="RGB", **save):
    buffer = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else 0).save(
        buffer, format=save.pop("format", "PNG"), **save
    )
    return buffer.getvalue()


def media_entry(alias="m1", kind="image", accessible=True, reason=None, mime=None):
    return {
        "alias": alias,
        "span": "s1",
        "field": "input",
        "kind": kind,
        "mime": mime,
        "accessible": accessible,
        "reason": reason,
    }


def data_uri(content, mime="image/png"):
    return ["data_uri", base64.b64encode(content).decode(), mime]


class TestMedia:
    def view(self, content=None, entry=None, source=None):
        entry = entry or media_entry()
        source = source or data_uri(content)
        snapshot = cached({"s1": {"input": "x"}}, [entry], {entry["alias"]: source})
        return broker.media(snapshot, entry["alias"], project_id=PROJECT)

    def test_an_image_is_downscaled_and_its_metadata_dropped(self):
        exif = Image.Exif()
        exif[0x010E] = "secret description"
        buffer = io.BytesIO()
        Image.new("RGB", (3200, 1600), "blue").save(buffer, "JPEG", exif=exif)

        item = self.view(
            buffer.getvalue(), source=data_uri(buffer.getvalue(), "image/jpeg")
        )

        assert item.mime == "image/jpeg" and item.original == (3200, 1600)
        with Image.open(io.BytesIO(item.content)) as image:
            assert image.size == (1568, 784)
            assert not image.getexif()
        assert b"secret description" not in item.content

    def test_formats_frames_and_size_caps(self):
        small = self.view(png((40, 30)))
        assert small.mime == "image/png" and small.original == (40, 30)

        frames = [Image.new("RGB", (50, 50), color) for color in ("red", "green")]
        buffer = io.BytesIO()
        frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:])
        gif = self.view(
            buffer.getvalue(), source=data_uri(buffer.getvalue(), "image/gif")
        )
        assert gif.mime == "image/png"
        with Image.open(io.BytesIO(gif.content)) as image:
            assert getattr(image, "n_frames", 1) == 1

        # Noise does not compress: past 5 MiB a PNG becomes a JPEG.
        noisy = Image.frombytes("RGB", (1568, 1568), os.urandom(1568 * 1568 * 3))
        buffer = io.BytesIO()
        noisy.save(buffer, "PNG")
        assert len(buffer.getvalue()) > broker.MAX_MEDIA_BYTES
        squeezed = self.view(buffer.getvalue())
        assert squeezed.mime == "image/jpeg"
        assert len(squeezed.content) <= broker.MAX_MEDIA_BYTES

    def test_too_many_pixels_are_refused_before_decoding(self):
        bomb = png((8000, 7000), mode="1")
        with pytest.raises(broker.BrokerError) as refused:
            self.view(bomb)
        assert (refused.value.status, refused.value.code) == (413, "oversize")

    def test_refusals_carry_the_protocols_codes(self):
        cases = [
            (
                {"entry": media_entry(accessible=False, reason="not_https")},
                422,
                "forbidden_host",
            ),
            (
                {
                    "entry": media_entry(
                        accessible=False, reason="provider_file_reference"
                    )
                },
                404,
                "not_exported",
            ),
            (
                {"source": ["external", "https://cdn.example.com/a.png", None]},
                422,
                "external_fetch_disabled",
            ),
            (
                {"entry": media_entry(kind="file"), "source": data_uri(b"%PDF-1")},
                422,
                "unsupported_mime",
            ),
            ({"source": data_uri(b"not an image at all")}, 422, "unsupported_mime"),
            ({"source": ["data_uri", "@@@@", "image/png"]}, 422, "unsupported_mime"),
        ]
        for kwargs, status, code in cases:
            with pytest.raises(broker.BrokerError) as refused:
                self.view(png((4, 4)), **kwargs)
            assert (refused.value.status, refused.value.code) == (status, code), kwargs

        snapshot = cached({"s1": {"input": "x"}})
        with pytest.raises(broker.BrokerError) as refused:
            broker.media(snapshot, "m7", project_id=PROJECT)
        assert refused.value.code == "unknown_media"

    def test_an_image_is_scaled_then_turned_upright(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways: rotate 90 degrees clockwise to view
        buffer = io.BytesIO()
        Image.new("RGB", (3200, 1600), "blue").save(buffer, "JPEG", exif=exif)
        item = self.view(source=data_uri(buffer.getvalue(), "image/jpeg"))
        with Image.open(io.BytesIO(item.content)) as image:
            assert image.size == (784, 1568)
            assert not image.getexif()

    def test_an_image_pillow_chokes_on_is_unsupported(self, monkeypatch):
        import PIL.Image

        def corrupt(*args, **kwargs):
            raise SyntaxError("not a TIFF file")

        monkeypatch.setattr(PIL.Image, "open", corrupt)
        with pytest.raises(broker.BrokerError) as refused:
            self.view(png((4, 4)))
        assert (refused.value.status, refused.value.code) == (422, "unsupported_mime")

    def test_decodes_wait_their_turn_then_are_refused(self, monkeypatch):
        monkeypatch.setattr(broker, "_decodes", threading.BoundedSemaphore(1))
        monkeypatch.setattr(broker, "SLOT_WAIT_S", 0.01)
        broker._decodes.acquire()
        try:
            with pytest.raises(broker.BrokerError) as refused:
                self.view(png((4, 4)))
        finally:
            broker._decodes.release()
        assert (refused.value.status, refused.value.code) == (503, "broker_busy")

    def test_platform_storage_serves_trace_media_of_the_run_only(self, monkeypatch):
        from tfc.settings.settings import UPLOAD_BUCKET_NAME

        read = []

        def reader(max_bytes):
            def read_object(url):
                read.append(url)
                return png((4, 4))

            return read_object

        monkeypatch.setattr(evidence, "_default_own_storage_reader", reader)
        bucket = f"https://{UPLOAD_BUCKET_NAME}.s3.amazonaws.com"
        item = self.view(source=["own_storage", f"{bucket}/tempcust/a.png", None])
        assert item.mime == "image/png" and len(read) == 1
        for key in (
            "knowledge-base/kb-1/a.png",
            f"call-recordings/{uuid.uuid4()}/vapi/call-1/a.png",
        ):
            with pytest.raises(broker.BrokerError) as refused:
                self.view(source=["own_storage", f"{bucket}/{key}", None])
            assert (refused.value.status, refused.value.code) == (404, "not_exported")
        assert len(read) == 1

    def test_audio_is_returned_as_it_is(self):
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24
        item = self.view(
            entry=media_entry(kind="audio", mime="audio/wav"),
            source=data_uri(wav, "audio/wav"),
        )
        assert (item.content, item.mime, item.original) == (wav, "audio/wav", None)


class TestViews:
    def post(self, op, body, capability=None, raw=None):
        request = RequestFactory().post(
            f"/tracer/internal/insights/evidence/{op}",
            data=raw if raw is not None else json.dumps(body),
            content_type="application/json",
            **({"HTTP_AUTHORIZATION": f"Bearer {capability}"} if capability else {}),
        )
        return InsightEvidenceView.as_view(op=op)(request)

    def test_manifest_fields_and_media_round_trip(self, world):
        capability = token(keys=["17"])
        manifest = self.post("manifest", {"unit": "17"}, capability)
        assert manifest.status_code == 200
        document = json.loads(manifest.content)["manifest"]
        assert document["unit"]["key"] == "17"
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
        assert json.loads(fields.content) == {
            "fields": {root["alias"]: {"output": "Yes, the box is crushed."}}
        }

        image = self.post("media", {"unit": "17", "media": "m1"}, capability)
        assert image.status_code == 200
        assert image["Content-Type"] == "image/png"
        assert image["x-insights-media-original"] == "1x1"
        assert world.builds == 1

    def test_errors_are_a_code_and_never_echo_the_request(self, world):
        capability = token(keys=["17"])
        cases = [
            ("manifest", {"unit": "17"}, None, 401, "capability_invalid"),
            ("manifest", {"unit": "18"}, capability, 403, "unit_not_in_capability"),
            (
                "fields",
                {"unit": "17", "items": "SECRET-ITEMS", "max_bytes": 9},
                capability,
                400,
                "invalid_request",
            ),
            (
                "media",
                {"unit": "17", "media": "SECRET-ALIAS"},
                capability,
                400,
                "unknown_media",
            ),
        ]
        for op, body, bearer, status, code in cases:
            response = self.post(op, body, bearer)
            assert response.status_code == status
            assert json.loads(response.content) == {"error": {"code": code}}
        broken = self.post("manifest", None, capability, raw="SECRET{not json")
        assert json.loads(broken.content) == {"error": {"code": "invalid_request"}}
        assert b"SECRET" not in broken.content

        world.run.status = "failed"
        response = self.post("manifest", {"unit": "17"}, capability)
        assert response.status_code == 409
        assert json.loads(response.content) == {"error": {"code": "run_not_running"}}

        unicode = self.post("manifest", {"unit": "17"}, capability[:-1] + "é")
        assert unicode.status_code == 401

    def test_every_call_sweeps_what_has_ended(self, world):
        capability = token(keys=["17"])
        ended = evidence_store.cache_root() / str(uuid.uuid4())
        ended.mkdir(parents=True, mode=0o700)
        assert self.post("manifest", {"unit": "17"}, capability).status_code == 200
        assert not ended.exists()

    def test_the_tokens_budget_is_spent_across_calls(self, world, monkeypatch):
        monkeypatch.setattr(broker, "UNIT_CALLS", 2)
        capability = token(keys=["17"])
        assert self.post("manifest", {"unit": "17"}, capability).status_code == 200
        assert self.post("manifest", {"unit": "17"}, capability).status_code == 200
        spent = self.post("manifest", {"unit": "17"}, capability)
        assert spent.status_code == 429
        assert json.loads(spent.content) == {
            "error": {"code": "capability_budget_spent"}
        }

    def test_routes_have_no_trailing_slash(self):
        from django.urls import resolve

        for op in broker.OPS:
            match = resolve(f"/internal/insights/evidence/{op}", urlconf="tracer.urls")
            assert match.func.view_class is InsightEvidenceView
            assert match.func.view_initkwargs == {"op": op}
