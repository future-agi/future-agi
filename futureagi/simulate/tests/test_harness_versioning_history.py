import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from simulate.models import HarnessSuiteMember, HostedHarnessJob, HostedHarnessScenario
from simulate.models import TestExecution as Execution
from simulate.models.test_execution import CallExecution
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.run_results_v3 import _harness_scenarios
from simulate.services.run_results_v3_queries import (
    GROUP_FIELDS,
    UNGROUPED,
    apply_run_call_query,
    run_call_facets,
    run_calls_queryset,
)
from simulate.services.run_results_v3_scan import scan_call_values
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import _legacy, _RealisticStorage
from simulate.tests.test_harness_versioning_runs import (
    _run,
    _scenario,
    _suite_archive,
    _versioned,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


KEY = _key(NAMES[0])
DROPPED = _key(NAMES[1])


def _then(**fields):
    """Scenario fields as suite version "then" held them."""
    return {
        "use_case": "billing",
        "sub_goals": ["greeted"],
        "persona": {"accent": "scottish", "age_group": "adult"},
        "coverage": {"overlay": "none", "task": "pay"},
        **fields,
    }


def _now(**fields):
    """The same scenario as a later default holds it."""
    return {
        "use_case": "refunds",
        "sub_goals": ["refunded"],
        "persona": {"accent": "irish", "age_group": "senior"},
        "coverage": {"overlay": "angry", "task": "refund"},
        **fields,
    }


def _pinned_then_moved(user, workspace, storage, name, *, keys=None, drop=False):
    """A run pinned to a suite version, after a later version became the default."""
    environment = _versioned(user, workspace, name, storage)
    record_authoring(
        environment,
        _suite_archive(
            [_scenario(NAMES[0], **_then()), _scenario(NAMES[1], **_then())]
        ),
        origin="chat",
    )
    environment.refresh_from_db()
    run = _run(environment, keys or [KEY])
    later = [_scenario(NAMES[0], **_now())]
    if not drop:
        later.append(_scenario(NAMES[1], **_now()))
    record_authoring(environment, _suite_archive(later), origin="authoring")
    environment.refresh_from_db()
    assert environment.suite_version_id != run.suite_version_id
    return environment, run, Execution.objects.get(id=run.test_execution_id)


def _read_model(execution):
    return list(
        run_calls_queryset(execution).values(
            "result_goal",
            GROUP_FIELDS["accent"],
            GROUP_FIELDS["age"],
            GROUP_FIELDS["attack"],
            GROUP_FIELDS["task"],
            GROUP_FIELDS["sub_goal"],
        )
    )


def _facets(execution):
    return run_call_facets(
        lambda: [], lambda: CallExecution.objects.filter(test_execution=execution)
    )


class TestPinnedRunHistory:
    def test_the_sql_read_model_shows_the_pinned_suites_content(
        self, user, workspace, storage
    ):
        environment, _, execution = _pinned_then_moved(
            user, workspace, storage, "history-sql"
        )
        assert (
            HostedHarnessScenario.no_workspace_objects.get(
                job=environment, scenario_key=KEY
            ).use_case
            == "refunds"
        )

        rows = _read_model(execution)

        assert rows and all(
            row
            == {
                "result_goal": "billing",
                GROUP_FIELDS["accent"]: "scottish",
                GROUP_FIELDS["age"]: "adult",
                GROUP_FIELDS["attack"]: "none",
                GROUP_FIELDS["task"]: "pay",
                GROUP_FIELDS["sub_goal"]: ["greeted"],
            }
            for row in rows
        )

    def test_the_sub_goal_facet_and_filters_read_the_pinned_suite(
        self, user, workspace, storage
    ):
        _, _, execution = _pinned_then_moved(user, workspace, storage, "history-sg")
        calls = CallExecution.objects.filter(test_execution=execution).count()

        assert _facets(execution)["sub_goal"] == [{"value": "greeted", "count": calls}]
        base = run_calls_queryset(execution)
        pick = {"filters": {"sub_goal": ["greeted"]}}
        assert apply_run_call_query(base, pick).count() == calls
        other = {"filters": {"sub_goal": ["refunded"]}}
        assert apply_run_call_query(run_calls_queryset(execution), other).count() == 0
        not_then = {"filters": {"sub_goal_not": ["greeted"]}}
        not_now = {"filters": {"sub_goal_not": ["refunded"]}}
        assert (
            apply_run_call_query(run_calls_queryset(execution), not_then).count() == 0
        )
        assert (
            apply_run_call_query(run_calls_queryset(execution), not_now).count()
            == calls
        )

    def test_grouping_by_a_scenario_axis_reads_the_pinned_suite(
        self, user, workspace, storage
    ):
        _, _, execution = _pinned_then_moved(user, workspace, storage, "history-grp")
        query = {"group_by": "accent", "group_key": "scottish"}

        grouped = apply_run_call_query(run_calls_queryset(execution), query)

        assert (
            grouped.count()
            == CallExecution.objects.filter(test_execution=execution).count()
        )

    def test_the_scan_shows_the_pinned_suites_content(self, user, workspace, storage):
        _, _, execution = _pinned_then_moved(user, workspace, storage, "history-scan")

        rows = scan_call_values(execution, [])

        assert rows and all(
            (
                row["result_goal"],
                row["result_sub_goal"],
                row["result_accent"],
                row["result_age"],
                row["result_attack"],
                row["result_task"],
            )
            == ("billing", ["greeted"], "scottish", "adult", "none", "pay")
            for row in rows
        )

    def test_the_scan_and_the_sql_read_model_agree(self, user, workspace, storage):
        _, _, execution = _pinned_then_moved(user, workspace, storage, "history-twin")

        scanned = {
            (row["result_goal"], row["result_accent"], tuple(row["result_sub_goal"]))
            for row in scan_call_values(execution, [])
        }
        sql = {
            (
                row["result_goal"],
                row[GROUP_FIELDS["accent"]],
                tuple(row[GROUP_FIELDS["sub_goal"]]),
            )
            for row in _read_model(execution)
        }

        assert scanned == sql

    def test_a_scenario_dropped_later_still_shows_its_pinned_content(
        self, user, workspace, storage
    ):
        environment, _, execution = _pinned_then_moved(
            user, workspace, storage, "history-drop", keys=[DROPPED], drop=True
        )
        assert HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=DROPPED
        ).deleted

        rows = scan_call_values(execution, [])

        assert rows and all(row["result_goal"] == "billing" for row in rows)
        assert {row["result_goal"] for row in _read_model(execution)} == {"billing"}
        assert _facets(execution)["sub_goal"][0]["value"] == "greeted"

    def test_a_value_the_member_lacks_reads_empty_not_the_newer_rows(
        self, user, workspace, storage
    ):
        _, run, execution = _pinned_then_moved(user, workspace, storage, "history-gap")
        member = HarnessSuiteMember.no_workspace_objects.get(
            suite_version_id=run.suite_version_id, scenario_key=KEY
        )
        content = {
            key: value
            for key, value in member.content.items()
            if key not in {"sub_goals", "coverage"}
        }
        member.content = {**content, "use_case": None, "persona": {"accent": None}}
        member.save(update_fields=["content", "updated_at"])

        sql = _read_model(execution)
        scanned = scan_call_values(execution, [])

        assert sql and all(
            (
                row[GROUP_FIELDS["accent"]],
                row[GROUP_FIELDS["task"]],
                row[GROUP_FIELDS["sub_goal"]],
            )
            == (UNGROUPED, UNGROUPED, [])
            for row in sql
        )
        assert {row["result_goal"] for row in sql} & {"billing", "refunds"} == set()
        assert {
            (row["result_goal"], row["result_accent"], tuple(row["result_sub_goal"]))
            for row in scanned
        } == {(row["result_goal"], row[GROUP_FIELDS["accent"]], ()) for row in sql}
        assert _facets(execution)["sub_goal"] == []

    def test_every_reader_drops_the_pin_of_a_deleted_run_job(
        self, user, workspace, storage, auth_client
    ):
        environment, run, execution = _pinned_then_moved(
            user, workspace, storage, "history-deleted-job"
        )
        HostedHarnessJob.all_objects.filter(id=run.id).update(deleted=True)
        calls = list(CallExecution.objects.filter(test_execution=execution))

        rows = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        ).json()["results"]

        assert {row["result_goal"] for row in _read_model(execution)} == {"refunds"}
        assert {row["result_goal"] for row in scan_call_values(execution, [])} == {
            "refunds"
        }
        assert _facets(execution)["sub_goal"][0]["value"] == "refunded"
        assert {one.use_case for one in _harness_scenarios(calls).values()} == {
            "refunds"
        }
        row = next(one for one in rows if one["id"] == str(execution.id))
        assert row["suite_version"] is None

    def test_the_call_rows_show_the_pinned_scenario(self, user, workspace, storage):
        _, _, execution = _pinned_then_moved(user, workspace, storage, "history-rows")
        calls = list(CallExecution.objects.filter(test_execution=execution))

        shown = _harness_scenarios(calls)

        assert {str(call.id) for call in calls} == set(shown)
        assert {(one.use_case, tuple(one.sub_goals)) for one in shown.values()} == {
            ("billing", ("greeted",))
        }


class TestUnpinnedRuns:
    def test_a_legacy_run_reads_the_identity_row(self, user, workspace, storage):
        environment = _legacy(user, workspace, "history-legacy", storage)
        run = _run(environment)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=KEY
        ).update(use_case="edited in place", sub_goals=["edited"])
        execution = Execution.objects.get(id=run.test_execution_id)

        assert {row["result_goal"] for row in _read_model(execution)} == {
            "edited in place"
        }
        assert {row["result_goal"] for row in scan_call_values(execution, [])} == {
            "edited in place"
        }
        assert _facets(execution)["sub_goal"][0]["value"] == "edited"

    def test_an_unpinned_runs_read_model_leaves_members_out(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "history-cost", storage)
        execution = Execution.objects.get(id=_run(environment).test_execution_id)

        # The group, goal and sub-goal reads only touch members when a run is pinned.
        assert "harness_suite_member" not in str(
            run_calls_queryset(execution).values("result_goal").query
        )
        filtered = apply_run_call_query(
            run_calls_queryset(execution),
            {"filters": {"sub_goal": ["greeted"], "sub_goal_not": ["refunded"]}},
        )
        assert "harness_suite_member" not in str(filtered.query)
        with CaptureQueriesContext(connection) as facets:
            _facets(execution)
        assert facets.captured_queries
        assert not [
            one
            for one in facets.captured_queries
            if "harness_suite_member" in one["sql"]
        ]

    def test_the_scan_reads_members_only_for_a_pinned_run(
        self, user, workspace, storage
    ):
        legacy = _legacy(user, workspace, "history-q-legacy", storage)
        unpinned = Execution.objects.get(id=_run(legacy).test_execution_id)
        _, _, pinned = _pinned_then_moved(user, workspace, storage, "history-q")

        with CaptureQueriesContext(connection) as before:
            scan_call_values(unpinned, [])
        with CaptureQueriesContext(connection) as after:
            scan_call_values(pinned, [])

        def member_reads(context):
            return [
                one
                for one in context.captured_queries
                if "harness_suite_member" in one["sql"]
            ]

        assert member_reads(before) == []
        assert len(member_reads(after)) == 1


class TestRunLabels:
    def test_the_executions_list_names_a_pinned_runs_versions(
        self, user, workspace, storage, auth_client
    ):
        environment, run, _ = _pinned_then_moved(
            user, workspace, storage, "history-labels"
        )
        legacy = _legacy(user, workspace, "history-labels-legacy", storage)
        old = _run(legacy, key="legacy")

        pinned_rows = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        ).json()["results"]
        legacy_rows = auth_client.get(
            f"/simulate/run-tests/{legacy.run_test_id}/executions/"
        ).json()["results"]

        row = next(
            one for one in pinned_rows if one["id"] == str(run.test_execution_id)
        )
        assert row["environment_version"] == {
            "id": str(run.environment_version_id),
            "label": f"v{run.environment_version.version_number}",
        }
        assert row["suite_version"] == {
            "id": str(run.suite_version_id),
            "label": f"v{run.suite_version.version_number}",
        }
        old_row = next(
            one for one in legacy_rows if one["id"] == str(old.test_execution_id)
        )
        assert old_row["environment_version"] is None
        assert old_row["suite_version"] is None

    def test_the_executions_list_reads_labels_in_one_query(
        self, user, workspace, storage, auth_client
    ):
        environment, _, _ = _pinned_then_moved(
            user, workspace, storage, "history-labels-count"
        )
        url = f"/simulate/run-tests/{environment.run_test_id}/executions/"
        with CaptureQueriesContext(connection) as one_run:
            first = auth_client.get(url)
        assert first.status_code == 200
        _run(environment, [DROPPED], key="second")
        _run(environment, [KEY], key="third")

        with CaptureQueriesContext(connection) as three_runs:
            rows = auth_client.get(url).json()["results"]

        assert len(rows) == 3
        assert len(three_runs) == len(one_run)

    def test_a_runs_label_never_follows_the_new_default(
        self, user, workspace, storage, auth_client
    ):
        environment, run, _ = _pinned_then_moved(
            user, workspace, storage, "history-labels-default"
        )

        rows = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        ).json()["results"]

        row = next(one for one in rows if one["id"] == str(run.test_execution_id))
        assert row["suite_version"]["id"] == str(run.suite_version_id)
        assert row["suite_version"]["id"] != str(environment.suite_version_id)

    def test_the_run_results_header_names_the_versions(
        self, user, workspace, storage, auth_client
    ):
        _, run, execution = _pinned_then_moved(
            user, workspace, storage, "history-header"
        )
        legacy = _legacy(user, workspace, "history-header-legacy", storage)
        old = Execution.objects.get(id=_run(legacy, key="legacy").test_execution_id)

        pinned = auth_client.get(
            f"/simulate/v3/test-executions/{execution.id}/calls/"
        ).json()["execution"]
        unpinned = auth_client.get(
            f"/simulate/v3/test-executions/{old.id}/calls/"
        ).json()["execution"]

        assert pinned["environment_version"]["id"] == str(run.environment_version_id)
        assert pinned["suite_version"] == {
            "id": str(run.suite_version_id),
            "label": f"v{run.suite_version.version_number}",
        }
        assert unpinned["environment_version"] is None
        assert unpinned["suite_version"] is None
