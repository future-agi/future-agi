"""Deterministic smoke-gate tests, not live ingestion or per-batch SLO proof."""

import json
from types import SimpleNamespace as NS

import ingestion_smoke as smoke
import pytest


def test_process_envelope_covers_phases_without_widening_sustained_gates():
    assert smoke._CONTINUOUS_TIMEOUT_SECONDS == 100
    assert smoke._CONTINUOUS_VISIBILITY_SECONDS == 45
    assert smoke.INGESTION_PROCESS_TIMEOUT_SECONDS == 300
    assert smoke.INGESTION_PROCESS_TIMEOUT_SECONDS == (
        20 + 2 * (10 + 15 + 45) + smoke._CONTINUOUS_TIMEOUT_SECONDS + 40
    )


class Lane:
    def __init__(self, tmp_path, monkeypatch):
        self.now = 0.0
        self.fixture = {
            "project_id": "owned-project",
            "workspace_id": "owned-workspace",
            "organization_id": "owned-organization",
        }
        self.first = {"live": 29.0, "late": 28.5}
        self.complete = {"live": 80.0, "late": 80.0}
        self.posts = []
        self.reports = {}
        self.reads = []
        self.commands = []
        self.status_code = 200
        self.rejected = "0"
        self.has_more = False
        self.canonical_delay = 0
        self.canonical_override = {}
        self.values = self.default_values
        self.before_get = lambda *_: None
        self.property_present = lambda *_: True
        self.run = NS(directory=tmp_path, execute=self.execute)
        monkeypatch.setattr(
            smoke,
            "time",
            NS(
                monotonic=lambda: self.now,
                time_ns=lambda: 1_000_000_000_000 + int(self.now * 1_000_000_000),
                sleep=self.advance,
            ),
        )
        monkeypatch.setattr(
            smoke, "save", lambda path, report: self.reports.update({path.name: report})
        )

    def advance(self, seconds):
        self.now += seconds

    def default_values(self, family):
        if self.now >= self.complete[family]:
            return {str(i) for i in range(24)}
        return {"0"} if self.now >= self.first[family] else set()

    def post(self, url, *, json, headers, timeout, allow_redirects):
        assert (url, headers, timeout, allow_redirects) == (
            "http://owned.invalid/v1/traces",
            {"X-Api-Key": "offline"},
            10,
            False,
        )
        index = len(self.posts)
        assert len(json) == 2
        for item, family in zip(json, ("live", "late"), strict=True):
            assert item["name"] == f"otlp_stream_{family}_{index}"
            assert item["attributes"] == [
                {"key": f"otlp_stream_{family}", "value": str(index)}
            ]
        assert json[0]["timestamp"] == (
            1_000_000_000_000 + int(self.now * 1_000_000_000)
        )
        assert json[1]["timestamp"] == 1_000 + index * 1_000_000
        self.posts.append(self.now)
        return NS(
            status_code=self.status_code,
            json=lambda: {"partialSuccess": {"rejectedSpans": self.rejected}},
        )

    def get(self, action, params):
        assert params["project_ids"] == self.fixture["project_id"]
        assert params["source"] == "traces" and params["page_size"] == 50
        name = params.get("search") or params["property_id"].split(":", 1)[1]
        family = name.removeprefix("otlp_stream_")
        assert family in ("live", "late")
        self.reads.append((action, family, self.now))
        self.before_get(action, family)
        if action == "metrics":
            assert params["cursor_mode"] == "true"
            return {
                "metrics": [{"name": name}] if self.property_present(family) else []
            }, None
        assert action == "filter_values"
        return {
            "values": [{"value": value} for value in sorted(self.values(family))],
            "has_more": self.has_more,
            "catalog_revision": 8 if self.now < 80 else 9,
        }, None

    def execute(self, service, args):
        self.commands.append((service, args))
        assert service == "clickhouse" and args[:2] == ["clickhouse-client", "--query"]
        assert "FROM default.spans WHERE startsWith(name, 'otlp_stream_')" in args[2]
        assert "max_execution_time=2, max_threads=1" in args[2]
        self.advance(self.canonical_delay)
        return NS(
            stdout=json.dumps(
                {
                    "rows": len(self.posts) * 2,
                    "ids": len(self.posts) * 2,
                    "projects": 1,
                    "project": self.fixture["project_id"],
                    "organization": self.fixture["organization_id"],
                    **self.canonical_override,
                }
            )
        )

    def verify(self):
        return smoke.verify_continuous(
            self.run,
            self.fixture,
            self.get,
            self,
            "http://owned.invalid/v1/traces",
            {"X-Api-Key": "offline"},
            lambda name, timestamp, value: {
                "name": name,
                "timestamp": timestamp,
                "attributes": [{"key": name, "value": value}],
            },
            lambda items: items,
            1_000,
        )

    def failed_report(self):
        with pytest.raises(RuntimeError, match="sustained visibility"):
            self.verify()
        report = self.reports["collector-continuous.json"]
        assert report["status"] == "failed"
        return report


@pytest.fixture
def lane(tmp_path, monkeypatch):
    return Lane(tmp_path, monkeypatch)


def test_sustained_exact_24_batches_same_cadence_and_all_48_values(lane):
    report = lane.verify()
    assert report["status"] == "passed"
    assert lane.posts == list(range(0, 48, 2))
    assert report["batch_count"] == 24
    assert report["submission_interval_seconds"] == 2
    assert report["total_budget_seconds"] == 100
    assert report["visibility_budget_seconds"] == 45
    assert report["first_visible_seconds"] == {"live": 29, "late": 28.5}
    assert (
        report["first_visible_while_sending_seconds"] == report["first_visible_seconds"]
    )
    assert report["converged_seconds"] == 80
    assert report["catch_up_after_last_submission_seconds"] == 34
    assert report["canonical"]["rows"] == report["canonical"]["ids"] == 48
    assert all(
        set(values) == {str(i) for i in range(24)}
        for values in report["visible"].values()
    )
    # This deliberately passes even though a middle batch waits over 45s:
    # the qualification is first visibility + final catch-up, not per-batch.
    assert report["converged_seconds"] - report["submitted"][2]["seconds"] == 76


@pytest.mark.parametrize("family", ["live", "late"])
@pytest.mark.parametrize("first", [44.75, 45.0])
def test_first_visibility_45_second_boundary_is_inclusive(lane, family, first):
    lane.first[family] = first
    report = lane.verify()
    assert report["first_visible_while_sending_seconds"][family] == first


@pytest.mark.parametrize("family", ["live", "late"])
def test_first_late_before_traffic_stops_still_fails(lane, family):
    lane.first[family] = 45.25
    report = lane.failed_report()
    assert report["first_visible_while_sending_seconds"][family] == 45.25
    assert report["catch_up_after_last_submission_seconds"] == 34


@pytest.mark.parametrize("complete", [90.75, 91.0])
def test_final_catch_up_45_second_boundary_is_inclusive(lane, complete):
    lane.complete = dict.fromkeys(("live", "late"), complete)
    report = lane.verify()
    assert report["catch_up_after_last_submission_seconds"] == complete - 46


@pytest.mark.parametrize("family", ["live", "late"])
def test_one_family_catching_up_late_fails_despite_eventual_exact_rows(lane, family):
    lane.complete[family] = 91.25
    report = lane.failed_report()
    assert report["catch_up_after_last_submission_seconds"] == 45.25
    assert report["canonical"]["rows"] == 48


def test_first_visibility_only_after_submissions_stop_fails(lane):
    lane.first = dict.fromkeys(("live", "late"), 47)
    report = lane.failed_report()
    assert report["first_visible_seconds"] == {"live": 47, "late": 47}
    assert report["first_visible_while_sending_seconds"] == {}


@pytest.mark.parametrize("family", ["live", "late"])
def test_partial_family_never_converges_and_times_out(lane, family):
    lane.complete[family] = float("inf")
    report = lane.failed_report()
    assert report["duration_seconds"] == 100
    assert report["converged_seconds"] is None
    assert report["catch_up_after_last_submission_seconds"] is None
    assert report["visible"][family] == ["0"]
    assert len(lane.posts) == 24


@pytest.mark.parametrize("delay,passed", [(20, True), (20.25, False)])
def test_total_deadline_includes_canonical_read_without_charging_it_to_catch_up(
    lane, delay, passed
):
    lane.canonical_delay = delay
    report = lane.verify() if passed else lane.failed_report()
    assert report["duration_seconds"] == 80 + delay
    assert report["converged_seconds"] == 80
    assert report["catch_up_after_last_submission_seconds"] == 34


def test_values_io_crossing_total_deadline_cannot_pass(lane):
    def delay(action, family):
        if action == "filter_values" and family == "late" and lane.now == 80:
            lane.advance(20.25)

    lane.before_get = delay
    report = lane.failed_report()
    assert report["converged_seconds"] == 100.25


def test_external_read_timeout_propagates_without_retry(lane):
    def timeout(*_):
        raise TimeoutError("bounded external read")

    lane.before_get = timeout
    with pytest.raises(TimeoutError, match="bounded external read"):
        lane.verify()
    assert len(lane.reads) == len(lane.posts) == 1
    assert not lane.commands


@pytest.mark.parametrize("status,rejected", [(503, "0"), (200, "1")])
def test_rejected_or_partial_otlp_is_never_retried(lane, status, rejected):
    lane.status_code, lane.rejected = status, rejected
    with pytest.raises(RuntimeError, match="batch 0 was not accepted"):
        lane.verify()
    assert len(lane.posts) == 1
    assert not lane.reads and not lane.commands


@pytest.mark.parametrize("found", [{"foreign"}, {"23"}])
def test_foreign_or_not_yet_submitted_values_fail(lane, found):
    lane.values = lambda _: found
    with pytest.raises(RuntimeError, match="leaked or disappeared"):
        lane.verify()


def test_previously_visible_values_cannot_disappear(lane):
    lane.values = lambda family: (
        set() if lane.now >= 30 else lane.default_values(family)
    )
    with pytest.raises(RuntimeError, match="leaked or disappeared"):
        lane.verify()


def test_previously_visible_property_cannot_disappear(lane):
    lane.property_present = lambda _: lane.now < 30
    with pytest.raises(RuntimeError, match="property disappeared"):
        lane.verify()


def test_paginated_partial_values_are_not_full_proof(lane):
    lane.has_more = True
    with pytest.raises(RuntimeError, match="leaked or disappeared"):
        lane.verify()


@pytest.mark.parametrize(
    "override",
    [
        {"rows": 49},
        {"ids": 47},
        {"projects": 2},
        {"project": "foreign"},
        {"organization": "foreign"},
    ],
)
def test_canonical_counts_and_tenant_isolation_remain_required(lane, override):
    lane.canonical_override = override
    lane.failed_report()


@pytest.fixture
def candidates(tmp_path, monkeypatch):
    fixture = {"organization_id": "owned-org", "workspace_id": "owned-workspace"}
    (tmp_path / "source-fixture.json").write_text(json.dumps(fixture))
    pairs = [
        ("otlp_live", "initial"),
        ("otlp_late", "initial"),
        ("otlp_late", "updated"),
        *[
            (f"otlp_stream_{family}", str(i))
            for i in range(24)
            for family in ("live", "late")
        ],
    ]
    records = [
        {
            "version": 2,
            **fixture,
            "values": [{"attribute_key": name, "value_json": json.dumps(value)}],
        }
        for name, value in pairs
    ]
    saved, calls = {}, []

    def execute(service, args, *, timeout, check):
        calls.append(args)
        assert service == "kafka" and timeout == 20 and check is False
        assert args[args.index("--timeout-ms") + 1] == "5000"
        assert args[args.index("--topic") + 1] == "owned.candidate.application"
        limit = int(args[args.index("--max-messages") + 1])
        assert limit == 24 * 2 + 20 == 68
        return NS(stdout="\n".join(map(json.dumps, records[:limit])))

    monkeypatch.setattr(
        smoke, "save", lambda path, report: saved.update({path.name: report})
    )
    return NS(
        run=NS(
            directory=tmp_path,
            manifest={"candidate_topic": "owned.candidate"},
            execute=execute,
        ),
        records=records,
        saved=saved,
        calls=calls,
    )


def test_candidate_arithmetic_reads_all_48_continuous_observations(candidates):
    smoke.verify_candidates(candidates.run)
    report = candidates.saved["collector-candidates.json"]
    assert report["status"] == "passed"
    assert len(report["managed_candidates"]) == 3 + 24 * 2 == 51
    assert len(candidates.calls) == 1


@pytest.mark.parametrize("index", [0, 1, 2, 3, 26, 50])
def test_missing_initial_update_or_either_half_of_continuous_candidates_fails(
    candidates, index
):
    candidates.records.pop(index)
    with pytest.raises(
        RuntimeError, match="initial and updated|continuous observations"
    ):
        smoke.verify_candidates(candidates.run)
    assert not candidates.saved


def test_extra_continuous_candidate_value_fails(candidates):
    candidates.records.append(
        {
            **candidates.records[-1],
            "values": [{"attribute_key": "otlp_stream_late", "value_json": '"24"'}],
        }
    )
    with pytest.raises(RuntimeError, match="continuous observations"):
        smoke.verify_candidates(candidates.run)


@pytest.mark.parametrize(
    "change",
    [
        {"version": 1},
        {"catalog_epoch": 1},
        {"projection_version": 1},
        {"organization_id": "foreign"},
        {"workspace_id": "foreign"},
    ],
)
def test_candidate_identity_contract_stays_fail_closed(candidates, change):
    candidates.records[-1].update(change)
    with pytest.raises(RuntimeError, match="identity/emission"):
        smoke.verify_candidates(candidates.run)
    assert not candidates.saved
