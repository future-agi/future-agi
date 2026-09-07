"""Safety and failure-propagation checks, not substitutes for live freshness."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from old_version_smoke import settled_repairs, verify


@pytest.fixture
def setup(tmp_path):
    run = SimpleNamespace(
        directory=tmp_path,
        manifest={"application": True, "run_id": "0123456789abcdef"},
        owned=Mock(return_value=["owned"]),
    )
    fixture = {
        "organization_id": "00000000-0000-4000-8000-000000000001",
        "workspace_id": "00000000-0000-4000-8000-000000000002",
        "project_id": "00000000-0000-4000-8000-000000000003",
    }
    before = {
        "deleted": 0,
        "value": "midscan_updated",
        "version": "101",
        "seen_us": "10",
    }
    after = {**before, "value": "historical_import", "version": "102"}
    plan = [{"plan": json.dumps({"source_scope": {"span_until_us": 1000}})}]
    return run, fixture, before, after, plan


def response(value):
    return {"catalog_epoch": 1, "catalog_revision": 9, "values": [{"value": value}]}, 0


def test_unowned_never_reads_or_writes_source(setup):
    run, fixture, *_ = setup
    run.owned.return_value = []
    with patch("old_version_smoke.latest") as read:
        with pytest.raises(RuntimeError, match="owned application"):
            verify(run, fixture, None, Mock())
        read.assert_not_called()


def test_only_one_exact_source_append_preserves_times_and_scope(setup):
    run, fixture, before, after, plan = setup
    get = Mock(
        side_effect=[
            response("midscan_updated"),
            response("historical_import"),
            response("Pro"),
        ]
    )
    with (
        patch("old_version_smoke.latest", side_effect=[before, after]),
        patch("old_version_smoke.rows", return_value=plan) as read_plan,
        patch(
            "old_version_smoke.settled_repairs", return_value=(True, {"signal": b"1"})
        ),
        patch(
            "old_version_smoke.query",
            side_effect=["attrs_string\n_version\nstart_time\nupdated_at\nid", ""],
        ) as query,
    ):
        assert verify(run, fixture, None, get)["status"] == "passed"
    sql = query.call_args_list[-1].args[1]
    assert sql.startswith("INSERT INTO default.spans")
    assert "toUInt64(102)" in sql and "now" not in sql
    assert "`start_time`" in sql and "`updated_at`" in sql
    assert (
        fixture["project_id"] in sql and "name='otlp_live'" in sql and "LIMIT 1" in sql
    )
    assert fixture["organization_id"] in read_plan.call_args.args[1]
    assert fixture["workspace_id"] in read_plan.call_args.args[1]
    assert "catalog_epoch=1" in read_plan.call_args.args[1]
    assert (run.directory / "old-version-import-attempt.json").exists()


def test_ambiguous_source_write_cannot_be_retried(setup):
    run, fixture, before, _, plan = setup
    writes = []

    def query(_run, sql):
        if sql.startswith("INSERT"):
            writes.append(sql)
            raise TimeoutError("uncertain commit")
        return "attrs_string\n_version\nid"

    with (
        patch("old_version_smoke.latest", return_value=before),
        patch("old_version_smoke.rows", return_value=plan),
        patch(
            "old_version_smoke.settled_repairs", return_value=(True, {"signal": b"1"})
        ),
        patch("old_version_smoke.query", side_effect=query),
    ):
        with pytest.raises(TimeoutError, match="uncertain commit"):
            verify(run, fixture, None, Mock(return_value=response("midscan_updated")))
        with pytest.raises(FileExistsError):
            verify(run, fixture, None, Mock(return_value=response("midscan_updated")))
    assert len(writes) == 1


@pytest.mark.parametrize("primary_failure", [False, True])
def test_diagnostic_failure_never_hides_primary_failure_or_makes_pass(
    setup, primary_failure
):
    run, fixture, before, after, plan = setup
    sequence = [response("midscan_updated")]
    sequence += (
        [RuntimeError("HTTP 503 primary")]
        if primary_failure
        else [response("historical_import"), response("Pro")]
    )
    with (
        patch("old_version_smoke.latest", side_effect=[before, after]),
        patch("old_version_smoke.rows", return_value=plan),
        patch(
            "old_version_smoke.settled_repairs",
            side_effect=[(True, {"signal": b"1"}), RuntimeError("diagnostic failed")],
        ),
        patch(
            "old_version_smoke.query", side_effect=["attrs_string\n_version\nid", ""]
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="HTTP 503 primary" if primary_failure else "diagnostic failed",
        ):
            verify(run, fixture, None, Mock(side_effect=sequence))
    report = json.loads((run.directory / "catalog-old-version-import.json").read_text())
    assert report["status"] == "failed"
    assert report["repair_observation_error"] == "diagnostic failed"


def test_settled_repairs_requires_existing_evidence_and_matching_ack(tmp_path):
    run = SimpleNamespace(directory=tmp_path)
    with pytest.raises(RuntimeError, match="evidence missing"):
        settled_repairs(run)
    folder = tmp_path / "application-runtime/source-repairs"
    folder.mkdir(parents=True)
    request = folder / "repair-fixture.json"
    request.write_text('{"generation":2,"since":"owned"}')
    ack = folder / "repair-fixture.json.ack"
    ack.write_text('{"generation":1,"since":"owned"}')
    assert settled_repairs(run)[0] is False
    ack.write_text(request.read_text())
    assert settled_repairs(run)[0] is True


def test_changed_source_event_time_is_not_a_valid_old_version_import(setup):
    run, fixture, before, after, plan = setup
    with (
        patch(
            "old_version_smoke.latest", side_effect=[before, {**after, "seen_us": "99"}]
        ),
        patch("old_version_smoke.rows", return_value=plan),
        patch(
            "old_version_smoke.settled_repairs", return_value=(True, {"signal": b"1"})
        ),
        patch(
            "old_version_smoke.query", side_effect=["attrs_string\n_version\nid", ""]
        ),
    ):
        with pytest.raises(RuntimeError, match="independently observed"):
            verify(run, fixture, None, Mock(return_value=response("midscan_updated")))
