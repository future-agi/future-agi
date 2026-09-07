"""Offline injection safety/order tests; live freshness is a separate gate."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from post_audit_smoke import (
    NAME,
    exact_scope,
    inject,
    install_fault,
    isolated_merge_pause,
    observed_part_watch,
    settled_part_baseline,
    verify,
)
from source_change_smoke import publish

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CheckedInPropertyCatalogDevRuntime as Runtime,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    LifecycleRunMode,
)


@pytest.fixture
def fixture(tmp_path):
    scope = {
        "organization_id": "00000000-0000-4000-8000-000000000001",
        "workspace_id": "00000000-0000-4000-8000-000000000002",
        "project_id": "00000000-0000-4000-8000-000000000003",
    }
    run = SimpleNamespace(
        directory=tmp_path,
        manifest={"application": True, "run_id": "0123456789abcdef"},
        owned=Mock(return_value=["owned"]),
    )
    since = datetime(2026, 9, 6, tzinfo=UTC)
    window = SimpleNamespace(since=since, until=since + timedelta(seconds=2))
    lease = SimpleNamespace(
        build_token="00000000-0000-4000-8000-000000000004", catalog_revision=9
    )
    prepared = SimpleNamespace(
        mode=LifecycleRunMode.INCREMENTAL,
        resumed=False,
        scope=SimpleNamespace(
            organization_id=scope["organization_id"],
            workspace_id=scope["workspace_id"],
            project_ids=(scope["project_id"],),
        ),
        cutoffs=SimpleNamespace(span_window=window),
        lease=lease,
    )
    execution = SimpleNamespace(
        prepared=prepared,
        lease=lease,
        manifest=object(),
        fence=object(),
        activation=None,
    )
    audit = {
        "build_token": lease.build_token,
        "result": {"final_span_audit_count": 2, "final_span_audit_digest": "a" * 64},
    }
    event_us = (
        since + timedelta(seconds=1) - datetime(1970, 1, 1, tzinfo=UTC)
    ) // timedelta(microseconds=1)
    after = {
        "version": "1",
        "seen_us": str(event_us),
        "value": "arrived",
        "part_name": "20260906_2_2_0",
    }
    columns = "id\ntrace_id\nname\nstart_time\nend_time\ncreated_at\nupdated_at\nattrs_string\nattrs_number\nattrs_bool\nattributes_extra\n_version"
    return run, scope, execution, audit, after, columns


@pytest.mark.parametrize(
    "wrong", ["unowned", "organization", "workspace", "projects", "resumed", "mode"]
)
def test_no_insert_outside_exact_owned_fresh_increment(fixture, wrong):
    run, scope, execution, audit, *_ = fixture
    if wrong == "unowned":
        run.owned.return_value = []
    elif wrong in {"organization", "workspace"}:
        setattr(execution.prepared.scope, wrong + "_id", "foreign")
    elif wrong == "projects":
        execution.prepared.scope.project_ids += ("foreign",)
    elif wrong == "resumed":
        execution.prepared.resumed = True
    else:
        execution.prepared.mode = LifecycleRunMode.FULL_REPAIR
    with patch("post_audit_smoke.query") as query:
        with pytest.raises(RuntimeError, match="exact owned incremental"):
            inject(run, scope, execution, audit)
        query.assert_not_called()


@pytest.mark.parametrize("wrong", ["audit", "build", "manifest", "fence", "activated"])
def test_no_insert_before_qualified_real_audit_or_after_activation(fixture, wrong):
    run, scope, execution, audit, *_ = fixture
    if wrong == "audit":
        audit["result"]["final_span_audit_digest"] = "not-a-proof"
    elif wrong == "build":
        audit["build_token"] = "different"
    else:
        setattr(
            execution,
            "activation" if wrong == "activated" else wrong,
            object() if wrong == "activated" else None,
        )
    with patch("post_audit_smoke.query") as query:
        with pytest.raises(RuntimeError):
            inject(run, scope, execution, audit)
        query.assert_not_called()


def test_one_source_only_insert_inside_audited_interval(fixture):
    run, scope, execution, audit, after, columns = fixture
    with (
        patch("post_audit_smoke.rows", side_effect=[[{"count": "0"}], [after]]),
        patch("post_audit_smoke.query", side_effect=[columns, ""]) as query,
    ):
        result = inject(run, scope, execution, audit)
    assert result["revision"] == execution.lease.catalog_revision
    assert result["event_us"] == int(after["seen_us"])
    sql = query.call_args.args[1]
    assert sql.startswith("INSERT INTO default.spans")
    assert "toUInt64(1)" in sql and "now(" not in sql
    assert scope["project_id"] in sql and "name='otlp_live'" in sql
    assert f"'{NAME}'" in sql and "LIMIT 1" in sql
    assert (run.directory / "post-audit-attempt.json").exists()


def test_uncertain_source_commit_never_retried(fixture):
    run, scope, execution, audit, _, columns = fixture
    writes = []

    def query(_run, sql):
        if sql.startswith("INSERT"):
            writes.append(sql)
            raise TimeoutError("uncertain commit")
        return columns

    with (
        patch("post_audit_smoke.rows", return_value=[{"count": "0"}]),
        patch("post_audit_smoke.query", side_effect=query),
    ):
        with pytest.raises(TimeoutError, match="uncertain commit"):
            inject(run, scope, execution, audit)
        with pytest.raises(FileExistsError):
            inject(run, scope, execution, audit)
    assert len(writes) == 1


def arm(run, scope):
    publish(run.directory / "source-fixture.json", scope)
    publish(
        run.directory / "post-audit-arm.json",
        {"run_id": run.manifest["run_id"], "fixture": scope},
    )


def test_actual_audit_then_insert_then_unchanged_notice(fixture):
    run, scope, execution, audit, after, _ = fixture
    arm(run, scope)
    self = SimpleNamespace(_require_execution=lambda: execution)
    order = []
    event_us = int(after["seen_us"])

    def audited(*_args, **_kwargs):
        order.append("audit")
        return audit["result"]

    def injected(*args):
        order.append("insert")
        assert args[3] == audit
        publish(run.directory / "post-audit-attempt.json", {"attempted": True})
        return {"event_us": event_us}

    def noticed(*_args):
        order.append("notice")
        folder = run.directory / "application-runtime/source-repairs"
        folder.mkdir(parents=True)
        publish(
            folder / f"repair-{scope['organization_id']}-{scope['workspace_id']}.json",
            {
                "first_seen_us": event_us,
                "last_seen_us": event_us,
                "observed_at_us": event_us + 1,
            },
        )
        return "original-result"

    with (
        patch.object(Runtime, "reconcile_non_postgres", side_effect=audited),
        patch.object(Runtime, "_notice_source_part_changes", side_effect=noticed),
        patch("post_audit_smoke.inject", side_effect=injected),
        patch("post_audit_smoke.observed_part_watch", return_value=nullcontext()),
        patch("post_audit_smoke.settled_part_baseline", return_value=True),
    ):
        install_fault(run)
        assert Runtime.reconcile_non_postgres(self) == audit["result"]
        assert Runtime._notice_source_part_changes(self, execution) == "original-result"
    assert order == ["audit", "insert", "notice"]
    assert (
        json.loads((run.directory / "post-audit-injected.json").read_text())[
            "exact_event_notice"
        ]
        is True
    )


@pytest.mark.parametrize("failure", [False, True])
def test_part_observations_preserve_original_calls_results_and_failures(
    fixture, failure
):
    run, scope, execution, *_ = fixture
    directory = run.directory / "application-runtime"
    directory.mkdir()
    parts = (("20260906_1_1_0", "abc"),)
    snapshot = Mock(return_value=parts)
    event = execution.prepared.cutoffs.span_window.since
    history = Mock(return_value=event)
    if failure:
        history.side_effect = TimeoutError("real probe failed")
    reader = SimpleNamespace(parts_snapshot=snapshot, history_in_parts=history)
    runtime = SimpleNamespace(
        span_reader=reader,
        _source_parts_at_start=parts,
        _source_repair_scope=lambda: {
            "state_directory": str(directory),
            "organization_id": scope["organization_id"],
            "workspace_id": scope["workspace_id"],
            "source_database": "default",
        },
    )
    report = {}
    params = {"since": event, "part_names": (parts[0][0],)}
    with observed_part_watch(run, runtime, report):
        assert reader.parts_snapshot() is parts
        if failure:
            with pytest.raises(TimeoutError, match="real probe failed"):
                reader.history_in_parts(**params)
        else:
            assert reader.history_in_parts(**params) is event
    assert reader.parts_snapshot is snapshot
    assert reader.history_in_parts is history
    snapshot.assert_called_once_with()
    history.assert_called_once_with(**params)
    assert report["part_watch"]["snapshots"] == [parts]
    probe = report["part_watch"]["history_probes"][0]
    assert probe["since"] == event.isoformat()
    assert probe.get("error") == ("real probe failed" if failure else None)
    assert probe.get("result") == (None if failure else event.isoformat())


@pytest.mark.parametrize(
    "inventory,settled,expected",
    [
        (None, True, False),
        ([], True, False),
        ([["20260906_1_1_0", "abc"]], False, False),
        ([["20260906_1_1_0", "abc"]], True, True),
    ],
)
def test_baseline_waits_for_normal_inventory_and_repairs_without_acknowledging(
    fixture, inventory, settled, expected
):
    from tracer.services.clickhouse.v2.property_catalog.coordinator import (
        FileSupersessionJournal,
    )

    run, scope, *_ = fixture
    directory = run.directory / "application-runtime"
    directory.mkdir()
    runtime = SimpleNamespace(
        _source_parts_at_start=(("20260906_1_1_0", "abc"),),
        _source_repair_scope=lambda: {
            "state_directory": str(directory),
            "source_database": "default",
            "organization_id": scope["organization_id"],
            "workspace_id": scope["workspace_id"],
        },
    )
    with (
        patch.object(
            FileSupersessionJournal,
            "load_record",
            return_value=({"parts": inventory} if inventory is not None else None),
        ),
        patch.object(FileSupersessionJournal, "save_record") as write,
        patch("post_audit_smoke.settled_repairs", return_value=(settled, {})),
    ):
        assert settled_part_baseline(run, runtime) is expected
        write.assert_not_called()


def test_unsettled_baseline_leaves_original_notice_running_without_injection(fixture):
    run, scope, execution, *_ = fixture
    arm(run, scope)
    runtime = SimpleNamespace(_require_execution=lambda: execution)
    with (
        patch.object(
            Runtime, "_notice_source_part_changes", return_value="real-notice"
        ) as notice,
        patch("post_audit_smoke.settled_part_baseline", return_value=False),
        patch("post_audit_smoke.inject") as injected,
    ):
        install_fault(run)
        assert Runtime._notice_source_part_changes(runtime, execution) == "real-notice"
        notice.assert_called_once_with(runtime, execution)
        injected.assert_not_called()
    assert not (run.directory / "post-audit-attempt.json").exists()


def test_audit_failure_prevents_injection(fixture):
    run, scope, execution, *_ = fixture
    arm(run, scope)
    with (
        patch.object(
            Runtime, "reconcile_non_postgres", side_effect=RuntimeError("audit failed")
        ),
        patch.object(Runtime, "_notice_source_part_changes"),
        patch("post_audit_smoke.inject") as injected,
    ):
        install_fault(run)
        with pytest.raises(RuntimeError, match="audit failed"):
            Runtime.reconcile_non_postgres(
                SimpleNamespace(_require_execution=lambda: execution)
            )
        injected.assert_not_called()
    assert not (run.directory / "post-audit-completed.json").exists()


@pytest.mark.parametrize(
    "exact_notice,later_revision,error",
    [
        (False, 10, "exact durable notice"),
        (True, 9, "later qualified catalog"),
        (True, 10, None),
    ],
)
def test_visible_value_alone_does_not_prove_post_audit_repair(
    fixture, exact_notice, later_revision, error
):
    run, scope, *_ = fixture
    publish(
        run.directory / "post-audit-injected.json",
        {"exact_event_notice": exact_notice, "revision": 9},
    )
    get = Mock(
        side_effect=[
            ({"metrics": []}, 0),
            ({"values": [{"value": "Pro"}]}, 0),
            ({"catalog_revision": later_revision, "metrics": [{"name": NAME}]}, 0),
            ({"catalog_revision": later_revision, "values": [{"value": "arrived"}]}, 0),
        ]
    )
    with (
        patch("post_audit_smoke.settled_repairs", return_value=(True, {})),
        patch("post_audit_smoke.isolated_merge_pause", return_value=nullcontext()),
    ):
        if error:
            with pytest.raises(RuntimeError, match=error):
                verify(run, scope, None, get)
        else:
            assert verify(run, scope, None, get)["status"] == "passed"
    report = json.loads((run.directory / "catalog-post-audit.json").read_text())
    assert report["status"] == ("failed" if error else "passed")


def test_exact_scope_accepts_only_original_project(fixture):
    _, scope, execution, *_ = fixture
    assert exact_scope(execution, scope)


@pytest.mark.parametrize("elapsed", [80, 181])
def test_correctness_wait_keeps_latency_target_and_finite_deadline(fixture, elapsed):
    run, scope, *_ = fixture
    publish(
        run.directory / "post-audit-injected.json",
        {"exact_event_notice": True, "revision": 9},
    )
    clock = [0]
    get = Mock(
        side_effect=[
            ({"metrics": []}, 0),
            ({"values": [{"value": "Pro"}]}, 0),
            ({"catalog_revision": 9, "metrics": []}, 0),
            ({"values": [{"value": "Pro"}]}, 0),
            ({"catalog_revision": 10, "metrics": [{"name": NAME}]}, 0),
            ({"catalog_revision": 10, "values": [{"value": "arrived"}]}, 0),
        ]
    )
    with (
        patch("post_audit_smoke.settled_repairs", return_value=(True, {})),
        patch("post_audit_smoke.isolated_merge_pause", return_value=nullcontext()),
        patch("post_audit_smoke.time.monotonic", side_effect=lambda: clock[0]),
        patch(
            "post_audit_smoke.time.sleep",
            side_effect=lambda _: clock.__setitem__(0, elapsed),
        ),
    ):
        if elapsed > 180:
            with pytest.raises(RuntimeError, match="180s"):
                verify(run, scope, None, get)
        else:
            result = verify(run, scope, None, get)
            assert result["status"] == "passed"
            assert result["timing"]["completion_seconds"] == elapsed
            assert result["timing"]["latency_target_met"] is False
            assert result["timing"]["latency_target_seconds"] == 60
    report = json.loads((run.directory / "catalog-post-audit.json").read_text())
    assert report["status"] == ("failed" if elapsed > 180 else "passed")


@pytest.mark.parametrize("failure", [None, "stop", "assertion", "restore"])
def test_merge_isolation_restores_only_owned_source_even_on_failure(fixture, failure):
    run, *_ = fixture
    calls = []

    def query(_run, sql):
        calls.append(sql)
        if failure == "stop" and "STOP" in sql:
            raise TimeoutError("uncertain STOP")
        if failure == "restore" and "START" in sql:
            raise RuntimeError("restore failed")

    def invoke():
        with isolated_merge_pause(run):
            if failure == "assertion":
                raise RuntimeError("primary assertion")

    with (
        patch("post_audit_smoke.query", side_effect=query),
        patch("post_audit_smoke.rows", return_value=[{"count": "0"}]),
    ):
        if failure:
            with pytest.raises((RuntimeError, TimeoutError)):
                invoke()
        else:
            invoke()
    assert calls == [
        "SYSTEM STOP MERGES default.spans",
        "SYSTEM START MERGES default.spans",
    ]
    report = json.loads((run.directory / "post-audit-merge-isolation.json").read_text())
    assert report["restored"] is (failure != "restore")


def test_merge_isolation_rejects_unowned_before_any_query(fixture):
    run, *_ = fixture
    run.owned.return_value = []
    with patch("post_audit_smoke.query") as query:
        with pytest.raises(RuntimeError, match="owned disposable"):
            with isolated_merge_pause(run):
                pytest.fail("unowned scope entered")
        query.assert_not_called()


def test_merge_restore_failure_does_not_hide_primary_assertion(fixture):
    run, *_ = fixture
    with (
        patch(
            "post_audit_smoke.query", side_effect=[None, RuntimeError("restore failed")]
        ),
        patch("post_audit_smoke.rows", return_value=[{"count": "0"}]),
    ):
        with pytest.raises(RuntimeError, match="primary assertion") as caught:
            with isolated_merge_pause(run):
                raise RuntimeError("primary assertion")
    assert "restore failed" in caught.value.__notes__[0]
