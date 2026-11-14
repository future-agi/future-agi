"""Database contracts. ClickHouse membership/row streams are controlled here;
real SQL/identity/filter coverage lives in test_eval_sampling_clickhouse.py.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.models.trace import Trace
from tracer.services.eval_tasks.reconciler import reconcile

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def controlled_sampling_clock(monkeypatch):
    # These engine/ORM tests control CH streams; real CH integration tests use
    # the actual server clock and have no such replacement.
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.sampling_clock",
        lambda: timezone.now(),
    )


@pytest.fixture(autouse=True)
def immediate_closed_freeze(settings):
    # These DB-contract tests seed and freeze at one controlled instant. The
    # ingestion/replication settle margin has dedicated tests below.
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0


@pytest.fixture(autouse=True)
def controlled_pending_source(monkeypatch):
    # Most engine tests model an unchanged source independently of delta pages.
    # The pending revalidation control flow/locks are real; CH integration tests
    # exercise this reader without any replacement.
    from tracer.services.eval_tasks.sampling.pending import read_current_rows

    def read(task, policy, version, ids, now):
        from tracer.services.eval_tasks.sampling.pending import IDENTITY_FIELDS

        field = IDENTITY_FIELDS[task.row_type]
        return {
            str(getattr(entry, field)): entry.output_metadata["_task_selection"][
                "sampling"
            ]["sampling_unit_id"]
            for entry in EvalLogger.objects.filter(
                eval_task_id=str(task.id),
                status=EvalEntryStatus.PENDING,
                **{f"{field}__in": ids},
            )
        }

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.pending.read_current_rows", read
    )
    # Lock-scope tests restore the real reader and stub only its CH stream.
    return read_current_rows


def make_task(project, config, *, mode="closed"):
    now = timezone.now()
    task = EvalTask.objects.create(
        project=project,
        name="cohort",
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        sampling_rate=1,
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": mode,
            "window": {
                "start": (now - timedelta(days=1)).isoformat(),
                "end": (now - timedelta(seconds=1)).isoformat()
                if mode == "closed"
                else None,
            },
            "default": {"target_n": 1} if mode == "closed" else {"rate": 50},
        },
    )
    task.evals.add(config)
    return task


def live_stream(rows, params):
    if "sp_new_units" in params:
        current = {
            row[1]: (row[1], row[2], row[3], row[4]) for block in rows for row in block
        }
        return iter([[current[unit] for unit in params["sp_new_units"]]])
    return iter(rows)


def stub_materialization(monkeypatch, task):
    trace = Trace.objects.create(project=task.project, name="sampled")
    cfg = task.evals.first()

    def materialize(_task, identities, *, sampling_metadata):
        EvalLogger.objects.bulk_create(
            [
                EvalLogger(
                    observation_span_id=identity,
                    trace=trace,
                    custom_eval_config=cfg,
                    eval_task_id=str(task.id),
                    target_type="span",
                    status=EvalEntryStatus.PENDING,
                    output_metadata={
                        "_task_selection": {"sampling": sampling_metadata[identity]}
                    },
                )
                for identity in identities
            ],
            ignore_conflicts=True,
        )

    monkeypatch.setattr(
        "tracer.services.eval_tasks.entries.materialize_pending", materialize
    )


def test_closed_membership_frozen_and_all_late_contact_rows_selected(
    project, custom_eval_config, monkeypatch
):
    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)
    calls = []
    rows = [
        [
            ("trace-a-span", "session:chosen", "", 0, False),
            ("trace-b-span", "session:chosen", "", 0, False),
            ("other-span", "session:other", "", 0, False),
        ]
    ]

    def stream(sql, params):
        calls.append(sql)
        if "WHERE selected" in sql:
            yield [("session:chosen", '["control",[]]', 0.5, 2)]
        else:
            yield from rows

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    first = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {e.observation_span_id for e in first} == {"trace-a-span", "trace-b-span"}
    assert all(
        e.output_metadata["_task_selection"]["sampling"]["inclusion_probability"] == 0.5
        for e in first
    )
    rows[:] = [
        [
            ("late-span", "session:chosen", "", 0, False),
            ("late-new-contact", "session:new", "", 0, False),
        ]
    ]
    reconcile(task)
    assert set(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    ) == {"trace-a-span", "trace-b-span", "late-span"}
    assert sum("WHERE selected" in sql for sql in calls) == 1
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 1
    assert EvalSamplingCohort.objects.get(task=task).complete


def test_partial_freeze_failure_rolls_back_cohort_entries_and_cursor(
    project, custom_eval_config, monkeypatch
):
    task = make_task(project, custom_eval_config)

    def partial_stream(sql, params):
        yield [("session:a", "rare", 1, 1)]
        raise TimeoutError("incomplete proof")

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", partial_stream
    )
    with pytest.raises(TimeoutError, match="incomplete"):
        reconcile(task)
    assert not EvalSamplingCohort.objects.filter(task=task).exists()
    assert not EvalSamplingMember.objects.exists()
    task.refresh_from_db()
    assert task.continuous_cursor is None
    assert not EvalLogger.objects.filter(eval_task_id=str(task.id)).exists()


def test_live_decision_never_flips_on_late_stratum_change(
    project, custom_eval_config, monkeypatch
):
    task = make_task(project, custom_eval_config, mode="live")
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.live_scan_windows",
        lambda *_: iter(
            [
                {
                    "start": task.sampling_policy["window"]["start"],
                    "end": timezone.now().isoformat(),
                }
            ]
        ),
    )
    stub_materialization(monkeypatch, task)
    rows = [
        [
            ("first-a", "session:kept", "original", 0.5, True),
            ("first-b", "session:skipped", "original", 0.5, False),
        ]
    ]
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query",
        lambda _sql, params: live_stream(rows, params),
    )
    reconcile(task)
    rows[:] = [
        [
            ("late-a", "session:kept", "changed", 0.1, False),
            ("late-b", "session:skipped", "changed", 1, True),
        ]
    ]
    reconcile(task)
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {e.observation_span_id for e in entries} == {"first-a", "late-a"}
    assert {
        e.output_metadata["_task_selection"]["sampling"]["stratum"] for e in entries
    } == {"original"}
    assert {
        e.output_metadata["_task_selection"]["sampling"]["inclusion_probability"]
        for e in entries
    } == {0.5}
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 2


def test_sampling_metadata_survives_real_result_write(project, custom_eval_config):
    from tracer.services.eval_tasks.entries import (
        persist_eval_result,
        writing_onto_entry,
    )

    task = make_task(project, custom_eval_config)
    trace = Trace.objects.create(project=project, name="result")
    metadata = {
        "_task_selection": {
            "sampling": {
                "policy_version": "v1",
                "inclusion_probability": 0.25,
                "sampling_unit_id": "session:x",
                "stratum": "rare",
            }
        }
    }
    entry = EvalLogger.objects.create(
        trace=trace,
        observation_span_id="result-span",
        custom_eval_config=custom_eval_config,
        eval_task_id=str(task.id),
        target_type="span",
        status=EvalEntryStatus.RUNNING,
        output_metadata=metadata,
    )
    with writing_onto_entry(str(entry.id), output_metadata=entry.output_metadata):
        persist_eval_result({"output_metadata": {"usage": {"total_tokens": 14}}})
    entry.refresh_from_db()
    assert entry.output_metadata["_task_selection"] == metadata["_task_selection"]
    assert entry.output_metadata["usage"]["total_tokens"] == 14


def test_preview_rejects_other_workspace_before_clickhouse(
    auth_client, project, user, custom_eval_config, monkeypatch
):
    from tracer.tests.test_eval_task import make_other_workspace_eval_task

    foreign = make_other_workspace_eval_task(project, user, custom_eval_config)
    policy = make_task(project, custom_eval_config).sampling_policy
    with patch("tracer.views.eval_sampling.preview_policy") as preview:
        response = auth_client.post(
            "/tracer/eval-task/sampling_preview/",
            {"project": str(foreign.project_id), "sampling_policy": policy},
            format="json",
        )
    assert response.status_code == 400
    preview.assert_not_called()


def test_preview_rejects_other_organization_before_clickhouse(
    auth_client, project, custom_eval_config
):
    from accounts.models import Organization
    from tracer.models.project import Project

    other = Organization.objects.create(name="other")
    foreign = Project.objects.create(
        organization=other,
        name="other-project",
        trace_type="observe",
        model_type=project.model_type,
    )
    with patch("tracer.views.eval_sampling.preview_policy") as preview:
        response = auth_client.post(
            "/tracer/eval-task/sampling_preview/",
            {
                "project": str(foreign.id),
                "sampling_policy": make_task(
                    project, custom_eval_config
                ).sampling_policy,
            },
            format="json",
        )
    assert response.status_code == 400
    preview.assert_not_called()


def test_create_edit_round_trips_policy_and_project_default(
    auth_client, project, custom_eval_config
):
    project.sampling_contact_attribute = "contact_id"
    project.save(update_fields=["sampling_contact_attribute"])
    value = make_task(project, custom_eval_config).sampling_policy
    value["sampling_unit"] = "attribute"
    response = auth_client.post(
        "/tracer/eval-task/",
        {
            "project": str(project.id),
            "name": "policy task",
            "run_type": "historical",
            "sampling_rate": 100,
            "sampling_policy": value,
            "evals": [str(custom_eval_config.id)],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    task = EvalTask.objects.get(pk=response.data["result"]["id"])
    assert task.sampling_policy["unit_attribute"]["key"] == "contact_id"
    updated = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.id),
            "sampling_policy": None,
            "edit_type": "fresh_run",
            "spans_limit": 100,
        },
        format="json",
    )
    assert updated.status_code == 200, updated.data
    task.refresh_from_db()
    assert task.sampling_policy is None


def test_anonymous_preview_denied(api_client, project, custom_eval_config):
    response = api_client.post(
        "/tracer/eval-task/sampling_preview/",
        {
            "project": str(project.id),
            "sampling_policy": make_task(project, custom_eval_config).sampling_policy,
        },
        format="json",
    )
    assert response.status_code in (401, 403)


def test_five_thousand_rows_materialize_in_bounded_batches_without_n_plus_one(
    project, custom_eval_config, monkeypatch
):
    from types import SimpleNamespace

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from tracer.services.eval_tasks import entries

    task = make_task(project, custom_eval_config)
    trace = Trace.objects.create(project=project, name="batched")
    monkeypatch.setattr(
        entries, "get_reader", lambda: SimpleNamespace(close=lambda: None)
    )
    batches = []

    def fks(reader, row_type, identities, *, project_id):
        assert len(identities) <= 1000
        batches.append(len(identities))
        return {
            identity: {"observation_span_id": identity, "trace_id": trace.id}
            for identity in identities
        }

    monkeypatch.setattr(entries, "_resolve_entry_fks", fks)

    def stream(sql, params):
        if "WHERE selected" in sql:
            yield [("session:one", "control", 1, 1)]
            return
        for index in range(5):
            if index:
                # A completely-buffered implementation would fail this: the
                # previous block must be materialized before the next is read.
                assert (
                    EvalLogger.objects.filter(eval_task_id=str(task.id)).count()
                    == index * 1000
                )
            yield [
                (f"batch-{index}-{row}", "session:one", "", 0, False)
                for row in range(1000)
            ]

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    with CaptureQueriesContext(connection) as queries:
        result = reconcile(task)
    assert result.created == 5000
    assert batches == [1000] * 5
    # SQLite chunks inserts more tightly than PostgreSQL; either must be far
    # below one query per span. No membership/result metadata FK N+1 is allowed.
    assert len(queries) < 500


def test_cohort_snapshot_retains_original_scope_after_task_edit(
    project, custom_eval_config, monkeypatch
):
    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)

    def stream(sql, params):
        yield (
            [("session:one", "original", 0.5, 2)]
            if "WHERE selected" in sql
            else [("one-span", "session:one", "", 0, False)]
        )

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    original = EvalSamplingCohort.objects.get(task=task)
    task.filters = {"observation_type": ["llm"]}
    task.continuous_cursor = None
    task.save(update_fields=["filters", "continuous_cursor"])
    reconcile(task)
    original.refresh_from_db()
    assert original.selection_context["filters"] == {}
    assert original.selection_context["project_id"] == str(project.id)
    assert original.selection_context["organization_id"] == str(project.organization_id)
    assert EvalSamplingCohort.objects.filter(task=task).count() == 2


@pytest.mark.parametrize(
    "failure,status,text",
    [
        # A refused/reset connection is a genuine outage: retryable 503.
        (ConnectionResetError, 503, "Narrow the window"),
        # A generic runtime failure is a defect, not an outage: sanitized 500.
        (RuntimeError, 500, "failed unexpectedly"),
    ],
)
def test_preview_dependency_failure_is_sanitized(
    auth_client, project, custom_eval_config, failure, status, text
):
    with patch(
        "tracer.views.eval_sampling.preview_policy",
        side_effect=failure("private SQL password"),
    ):
        response = auth_client.post(
            "/tracer/eval-task/sampling_preview/",
            {
                "project": str(project.id),
                "sampling_policy": make_task(
                    project, custom_eval_config
                ).sampling_policy,
            },
            format="json",
        )
    assert response.status_code == status
    assert "private SQL password" not in response.content.decode()
    assert text in response.content.decode()


def test_generic_patch_policy_scope_resets_arrival_cursor(
    auth_client, project, custom_eval_config
):
    task = make_task(project, custom_eval_config)
    task.continuous_cursor = timezone.now()
    task.save(update_fields=["continuous_cursor"])
    changed = {**task.sampling_policy, "default": {"target_n": 2}}
    response = auth_client.patch(
        f"/tracer/eval-task/{task.id}/", {"sampling_policy": changed}, format="json"
    )
    assert response.status_code == 200, response.data
    task.refresh_from_db()
    assert task.continuous_cursor is None
    assert task.sampling_policy["default"]["target_n"] == 2


def test_preview_rejects_evaluator_from_other_project(
    auth_client, project, custom_eval_config
):
    from tracer.models.project import Project
    from tracer.tests.test_eval_task import make_custom_eval_config_for_project

    other = Project.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        name="different-project",
        trace_type="observe",
        model_type=project.model_type,
    )
    foreign = make_custom_eval_config_for_project(
        other, custom_eval_config, "different evaluator"
    )
    with patch("tracer.views.eval_sampling.preview_policy") as preview:
        response = auth_client.post(
            "/tracer/eval-task/sampling_preview/",
            {
                "project": str(project.id),
                "evals": [str(foreign.id)],
                "sampling_policy": make_task(
                    project, custom_eval_config
                ).sampling_policy,
            },
            format="json",
        )
    assert response.status_code == 400
    preview.assert_not_called()


def test_policy_task_switches_to_historical_without_legacy_row_limit(
    auth_client, project, custom_eval_config
):
    task = make_task(project, custom_eval_config)
    task.spans_limit = None
    task.save(update_fields=["spans_limit"])
    response = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.id),
            "run_type": "historical",
            "edit_type": "edit_rerun",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    task.refresh_from_db()
    assert task.run_type == "historical"
    assert task.spans_limit is None
    disabled = auth_client.patch(
        f"/tracer/eval-task/{task.id}/", {"sampling_policy": None}, format="json"
    )
    assert disabled.status_code == 400


def test_generic_row_type_change_revalidates_existing_policy(
    auth_client, project, custom_eval_config
):
    task = make_task(project, custom_eval_config)
    task.sampling_policy["sampling_unit"] = "span"
    task.save(update_fields=["sampling_policy"])
    response = auth_client.patch(
        f"/tracer/eval-task/{task.id}/", {"row_type": "sessions"}, format="json"
    )
    assert response.status_code == 400
    task.refresh_from_db()
    assert task.row_type == "spans"


def test_closed_quota_edit_requires_fresh_run_and_keeps_deleted_provenance(
    auth_client, project, custom_eval_config, monkeypatch
):
    from tracer.models.eval_task import EvalTaskStatus

    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)

    def stream(sql, params):
        if "WHERE selected" in sql:
            current = EvalTask.objects.get(pk=task.pk).sampling_policy["default"][
                "target_n"
            ]
            yield (
                [("session:a", "control", 0.5, 2)]
                if current == 1
                else [("session:a", "control", 1, 2), ("session:b", "control", 1, 2)]
            )
        else:
            yield [
                ("a-span", "session:a", "", 0, False),
                ("b-span", "session:b", "", 0, False),
            ]

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    EvalLogger.objects.filter(eval_task_id=str(task.id)).update(
        status=EvalEntryStatus.COMPLETED
    )
    old = EvalLogger.objects.get(eval_task_id=str(task.id))
    old_metadata = old.output_metadata
    task.status = EvalTaskStatus.PAUSED
    task.save(update_fields=["status"])
    changed = {**task.sampling_policy, "default": {"target_n": 2}}
    rejected = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.id),
            "sampling_policy": changed,
            "edit_type": "edit_rerun",
        },
        format="json",
    )
    assert rejected.status_code == 400
    assert "Delete & rerun" in rejected.content.decode()
    direct = auth_client.patch(
        f"/tracer/eval-task/{task.id}/", {"sampling_policy": changed}, format="json"
    )
    assert direct.status_code == 400
    fresh = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.id),
            "sampling_policy": changed,
            "edit_type": "fresh_run",
        },
        format="json",
    )
    assert fresh.status_code == 200, fresh.data
    reconcile(task)
    visible = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in visible} == {"a-span", "b-span"}
    samples = [
        entry.output_metadata["_task_selection"]["sampling"] for entry in visible
    ]
    assert {sample["inclusion_probability"] for sample in samples} == {1}
    assert len({sample["cohort_id"] for sample in samples}) == 1
    assert (
        samples[0]["cohort_id"]
        != old_metadata["_task_selection"]["sampling"]["cohort_id"]
    )
    archived = EvalLogger.all_objects.get(pk=old.pk)
    assert archived.deleted
    assert archived.output_metadata == old_metadata


def test_live_policy_admits_new_contacts_in_future_windows(
    project, custom_eval_config, monkeypatch
):
    task = make_task(project, custom_eval_config, mode="live")
    stub_materialization(monkeypatch, task)
    first = timezone.now()
    future = first + timedelta(days=40)
    scans = []

    def windows(_task, _policy, ceiling, floor):
        scans.append(ceiling)
        yield {
            "start": (ceiling - timedelta(days=1)).isoformat(),
            "end": ceiling.isoformat(),
        }

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.live_scan_windows", windows
    )
    rows = [[("first", "session:first", "control", 0.5, True)]]
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query",
        lambda _sql, params: live_stream(rows, params),
    )
    with patch(
        "tracer.services.eval_tasks.sampling.service.sampling_clock", return_value=first
    ):
        reconcile(task)
    original = EvalSamplingCohort.objects.get(task=task)
    rows[:] = [
        [
            ("future", "session:future", "control", 0.5, True),
            ("late-first", "session:first", "changed", 0.1, False),
        ]
    ]
    with patch(
        "tracer.services.eval_tasks.sampling.service.sampling_clock",
        return_value=future,
    ):
        reconcile(task)
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in entries} == {
        "first",
        "future",
        "late-first",
    }
    assert {
        entry.output_metadata["_task_selection"]["sampling"]["cohort_id"]
        for entry in entries
    } == {str(original.id)}
    assert all(
        entry.output_metadata["_task_selection"]["sampling"]["window"]["end"] is None
        for entry in entries
    )
    assert scans == [first, future]


def test_policy_cursor_uses_ch_clock_without_pg_creation_floor(
    project, custom_eval_config, monkeypatch
):
    from tracer.services.eval_tasks.cursor_policy import CONTINUOUS_CURSOR_OVERLAP

    task = make_task(project, custom_eval_config, mode="live")
    server_now = task.created_at - timedelta(seconds=10)
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.sampling_clock", lambda: server_now
    )
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.live_scan_windows",
        lambda *_: iter([]),
    )
    reconcile(task)
    task.refresh_from_db()
    assert task.continuous_cursor == server_now - CONTINUOUS_CURSOR_OVERLAP
    assert task.continuous_cursor < task.created_at


def test_generic_policy_task_edit_rejects_cross_project_evaluator(
    auth_client, project, custom_eval_config
):
    from tracer.models.project import Project
    from tracer.tests.test_eval_task import make_custom_eval_config_for_project

    task = make_task(project, custom_eval_config)
    other = Project.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        name="foreign-policy-project",
        trace_type="observe",
        model_type=project.model_type,
    )
    foreign = make_custom_eval_config_for_project(
        other, custom_eval_config, "foreign-policy-eval"
    )
    response = auth_client.patch(
        f"/tracer/eval-task/{task.id}/", {"evals": [str(foreign.id)]}, format="json"
    )
    assert response.status_code == 400
    assert list(task.evals.values_list("id", flat=True)) == [custom_eval_config.id]


@pytest.mark.parametrize("state", [EvalEntryStatus.ERRORED, EvalEntryStatus.SKIPPED])
def test_full_eval_revision_pass_preserves_converged_failure_and_delta_retry(
    project, custom_eval_config, monkeypatch, state
):
    from types import SimpleNamespace

    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.services.eval_tasks import entries

    task = make_task(project, custom_eval_config)
    trace = Trace.objects.create(project=project, name="convergence")
    monkeypatch.setattr(
        entries, "get_reader", lambda: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(
        entries,
        "_resolve_entry_fks",
        lambda reader, kind, ids, **_: {
            identity: {"observation_span_id": identity, "trace_id": trace.id}
            for identity in ids
        },
    )

    def stream(sql, params):
        yield (
            [("session:one", "cell", 1, 1)]
            if "WHERE selected" in sql
            else [("failed-span", "session:one", "", 0, False)]
        )

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    failed = EvalLogger.objects.get(
        eval_task_id=str(task.id), custom_eval_config=custom_eval_config
    )
    EvalLogger.objects.filter(pk=failed.pk).update(
        status=state, updated_at=timezone.now() - timedelta(days=2)
    )
    EvalTask.objects.filter(pk=task.pk).update(
        continuous_cursor=timezone.now() - timedelta(hours=1)
    )
    added = CustomEvalConfig.objects.create(
        project=project,
        name="new evaluator",
        eval_template=custom_eval_config.eval_template,
        config=custom_eval_config.config,
        mapping=custom_eval_config.mapping,
        filters={},
    )
    task.evals.add(added)
    result = reconcile(task)
    failed.refresh_from_db()
    assert failed.status == state
    assert result.requeued == 0
    assert (
        EvalLogger.objects.filter(
            eval_task_id=str(task.id),
            custom_eval_config=added,
            status=EvalEntryStatus.PENDING,
        ).count()
        == 1
    )
    # With no full revision now, this controlled stream proves a changed row
    # inside the arrival window. Genuine retry still occurs exactly once.
    changed = reconcile(task)
    failed.refresh_from_db()
    assert changed.requeued == 1
    assert failed.status == EvalEntryStatus.PENDING
    EvalLogger.objects.filter(pk=failed.pk).update(
        status=state, updated_at=timezone.now()
    )
    unchanged = reconcile(task)
    failed.refresh_from_db()
    assert unchanged.requeued == 0
    assert failed.status == state


@pytest.mark.django_db(transaction=True)
def test_pending_proof_lock_prevents_claiming_a_proven_negative(
    project, custom_eval_config, monkeypatch
):
    from threading import Event, Thread

    from django.db import close_old_connections, connection, transaction

    from tracer.services.eval_tasks.entries import claim_pending_batch
    from tracer.services.eval_tasks.sampling.pending import revalidate_pending
    from tracer.services.eval_tasks.sampling.service import task_policy

    assert connection.vendor == "postgresql", (
        "This locking regression requires real PostgreSQL."
    )
    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)

    def stream(sql, params):
        yield (
            [("session:one", "cell", 1, 1)]
            if "WHERE selected" in sql
            else [("locked-span", "session:one", "", 0, False)]
        )

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    entered, release = Event(), Event()
    errors = []

    def blocked_current(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release the pending proof")
        return {}

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.pending.read_current_rows", blocked_current
    )

    def proof():
        close_old_connections()
        try:
            with transaction.atomic():
                locked = (
                    EvalTask.objects.select_for_update(of=("self",))
                    .select_related("project")
                    .get(pk=task.pk)
                )
                policy, version = task_policy(locked, now=timezone.now())
                cohort = EvalSamplingCohort.objects.get(task=locked)
                assert (
                    revalidate_pending(locked, policy, version, cohort, timezone.now())
                    == 1
                )
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()

    worker = Thread(target=proof, daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        assert claim_pending_batch(task, 1) == []
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert claim_pending_batch(task, 1) == []
    assert EvalLogger.all_objects.get(eval_task_id=str(task.id)).deleted


@pytest.mark.parametrize("reverse", [False, True])
def test_live_first_admission_classifies_all_changed_days_before_freezing_priority(
    project, custom_eval_config, monkeypatch, reverse
):
    task = make_task(project, custom_eval_config, mode="live")
    task.sampling_policy = {
        **task.sampling_policy,
        "strata": [{"name": "rare-positive", "filters": [], "rate": 100}],
        "default": {"rate": 0},
    }
    task.save(update_fields=["sampling_policy"])
    stub_materialization(monkeypatch, task)
    windows = [
        {"start": "2026-10-19T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
        {"start": "2026-10-20T00:00:00Z", "end": "2026-10-21T00:00:00Z"},
    ]
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.live_scan_windows",
        lambda *_: iter(windows[::-1] if reverse else windows),
    )
    proofs = []

    def stream(sql, params):
        if "sp_new_units" in params:
            proofs.append(params["sp_new_units"])
            assert "sp_arrival_floor_ns" not in params
            assert "sp_new_session_ids" in params
            yield [("session:new", '["rare-positive",[]]', 1.0, True)]
        elif params["sp_start"].day == 19:
            yield [("ordinary-span", "session:new", '["default",[]]', 0.0, False)]
        else:
            yield [("rare-span", "session:new", '["rare-positive",[]]', 1.0, True)]

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    member = EvalSamplingMember.objects.get(cohort__task=task)
    assert member.stratum == '["rare-positive",[]]'
    assert member.selected is True
    assert proofs == [("session:new",)]
    assert {
        entry.observation_span_id
        for entry in EvalLogger.objects.filter(eval_task_id=str(task.id))
    } == {"ordinary-span", "rare-span"}


def closed_stream(*, members=None, rows=None, hook=None):
    members = members or [("session:one", "cell", 1, 1)]
    rows = rows or [("span-1", "session:one", "", 0, False)]

    def stream(sql, params):
        if hook is not None:
            hook("members" if "WHERE selected" in sql else "rows", "start")
        yield members if "WHERE selected" in sql else rows
        if hook is not None:
            hook("members" if "WHERE selected" in sql else "rows", "end")

    return stream


def _other_connection(work):
    """Run ``work`` on a separate PostgreSQL connection and return its error."""
    from threading import Thread

    from django.db import close_old_connections

    errors = []

    def run():
        close_old_connections()
        try:
            work()
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()

    worker = Thread(target=run, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "probe connection hung on a lock"
    return errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["closed", "live"])
def test_discovery_and_member_streams_hold_no_transaction_or_task_lock(
    project, custom_eval_config, monkeypatch, mode
):
    from django.db import connection, transaction

    from tracer.models.custom_eval_config import CustomEvalConfig

    assert connection.vendor == "postgresql", "Lock scope needs real PostgreSQL."
    task = make_task(project, custom_eval_config, mode=mode)
    stub_materialization(monkeypatch, task)
    observed = []

    def probe(kind, phase):
        def edit_while_streaming():
            with transaction.atomic():
                EvalTask.objects.select_for_update(nowait=True).get(pk=task.pk)
                CustomEvalConfig.objects.select_for_update(nowait=True).get(
                    pk=custom_eval_config.pk
                )
                EvalTask.objects.filter(pk=task.pk).update(status="paused")

        observed.append(
            (
                kind,
                phase,
                connection.in_atomic_block,
                _other_connection(edit_while_streaming),
            )
        )

    if mode == "closed":
        stream = closed_stream(hook=probe)
    else:
        monkeypatch.setattr(
            "tracer.services.eval_tasks.sampling.service.live_scan_windows",
            lambda *_: iter(
                [
                    {
                        "start": task.sampling_policy["window"]["start"],
                        "end": timezone.now().isoformat(),
                    }
                ]
            ),
        )
        rows = [[("span-1", "session:one", "cell", 0.5, True)]]

        def stream(sql, params):
            probe("proof" if "sp_new_units" in params else "rows", "start")
            yield from live_stream(rows, params)
            probe("proof" if "sp_new_units" in params else "rows", "end")

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    assert {kind for kind, *_ in observed} >= (
        {"members", "rows"} if mode == "closed" else {"proof", "rows"}
    )
    # Discovery/member streams hold no transaction or task/evaluator lock: edits
    # commit immediately. Pending proofs are a separate, locked path (tested below).
    assert all(not in_atomic and not errors for *_, in_atomic, errors in observed), (
        observed
    )
    assert EvalTask.objects.get(pk=task.pk).status == "paused"
    assert list(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    ) == ["span-1"]
    assert EvalSamplingCohort.objects.get(task=task).lease_token is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["closed", "live"])
@pytest.mark.parametrize("still_eligible", [True, False])
def test_pending_proof_holds_task_config_and_entry_locks_until_page_commit(
    project,
    custom_eval_config,
    monkeypatch,
    controlled_pending_source,
    mode,
    still_eligible,
):
    from django.db import OperationalError, connection, transaction

    from tracer.services.eval_tasks.sampling import pending

    assert connection.vendor == "postgresql", "Lock scope needs real PostgreSQL."
    task = make_task(project, custom_eval_config, mode=mode)
    stub_materialization(monkeypatch, task)
    if mode == "closed":
        stream = closed_stream()
    else:
        monkeypatch.setattr(
            "tracer.services.eval_tasks.sampling.service.live_scan_windows",
            lambda *_: iter(
                [task.sampling_policy["window"] | {"end": timezone.now().isoformat()}]
            ),
        )

        def stream(sql, params):
            return live_stream([[("span-1", "session:one", "cell", 0.5, True)]], params)

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    # Undo the autouse PostgreSQL-derived reader: exercise read_current_rows and
    # its real bounded compiler, replacing only the network stream for this test.
    monkeypatch.setattr(pending, "read_current_rows", controlled_pending_source)
    observations = []
    entry_ids = []

    def proof_stream(sql, params):
        assert params["sp_current_ids"] == ("span-1",)
        assert connection.in_atomic_block
        entry = EvalLogger.objects.get(eval_task_id=str(task.id))
        entry_ids.append(entry.pk)
        for obj in (task, custom_eval_config, entry):

            def claim_or_edit(obj=obj):
                with transaction.atomic():
                    type(obj).objects.select_for_update(nowait=True).get(pk=obj.pk)

            errors = _other_connection(claim_or_edit)
            assert len(errors) == 1 and isinstance(errors[0], OperationalError), errors
            assert errors[0].__cause__.sqlstate == "55P03", errors
            observations.append(type(obj).__name__)
        yield [("span-1", "session:one")] if still_eligible else []
        assert connection.in_atomic_block

    monkeypatch.setattr(pending, "stream_query", proof_stream)
    reconcile(task)
    assert observations == ["EvalTask", "CustomEvalConfig", "EvalLogger"]
    assert len(entry_ids) == 1
    assert EvalLogger.objects.filter(pk=entry_ids[0]).exists() == still_eligible
    assert not connection.in_atomic_block

    def edit_after_commit():
        with transaction.atomic():
            EvalTask.objects.select_for_update(nowait=True).get(pk=task.pk)
            type(custom_eval_config).objects.select_for_update(nowait=True).get(
                pk=custom_eval_config.pk
            )
            if still_eligible:
                EvalLogger.objects.select_for_update(nowait=True).get(pk=entry_ids[0])

    assert _other_connection(edit_after_commit) == []


@pytest.mark.django_db(transaction=True)
def test_lease_excludes_second_writer_and_takeover_discards_uncertified_freeze(
    project, custom_eval_config, monkeypatch
):
    import uuid

    from tracer.services.eval_tasks.sampling.errors import SamplingReconcileBusy
    from tracer.services.eval_tasks.sampling.service import task_policy, unit_key

    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", closed_stream()
    )
    policy, version = task_policy(task, now=timezone.now())
    holder = uuid.uuid4()
    cohort = EvalSamplingCohort.objects.create(
        task=task,
        policy_version=version,
        policy=policy,
        frozen_at=timezone.now() - timedelta(hours=1),
        lease_token=holder,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    EvalSamplingMember.objects.create(
        cohort=cohort,
        unit_key=unit_key("session:half-written"),
        unit_id="session:half-written",
        stratum="cell",
        selected=True,
        inclusion_probability=1,
        population_size=1,
    )
    with pytest.raises(SamplingReconcileBusy):
        reconcile(task)
    cohort.refresh_from_db()
    assert cohort.lease_token == holder and not cohort.complete
    assert not EvalLogger.objects.filter(eval_task_id=str(task.id)).exists()
    assert EvalTask.objects.get(pk=task.pk).continuous_cursor is None
    # The holder died: its lease expires and its uncertified members are never
    # read. The next pass discards them and freezes the whole cohort anew.
    EvalSamplingCohort.objects.filter(pk=cohort.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    reconcile(task)
    cohort.refresh_from_db()
    assert cohort.complete and cohort.lease_token is None
    assert cohort.frozen_at > timezone.now() - timedelta(minutes=5)
    assert list(cohort.members.values_list("unit_id", flat=True)) == ["session:one"]
    assert list(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    ) == ["span-1"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("interruption", ["lease", "edit"])
def test_fenced_pass_stops_writing_after_lease_takeover_or_task_edit(
    project, custom_eval_config, monkeypatch, interruption
):
    import uuid

    from tracer.services.eval_tasks.reconciler import EvalTaskRevisionChanged
    from tracer.services.eval_tasks.sampling.errors import SamplingLeaseLost

    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)
    other = uuid.uuid4()

    def interrupt(kind, phase):
        if kind != "rows" or phase != "start":
            return
        if interruption == "lease":
            EvalSamplingCohort.objects.filter(task=task).update(lease_token=other)
        else:
            EvalTask.objects.filter(pk=task.pk).update(
                filters={"observation_type": ["llm"]}
            )

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query",
        closed_stream(hook=interrupt),
    )
    expected = SamplingLeaseLost if interruption == "lease" else EvalTaskRevisionChanged
    with pytest.raises(expected):
        reconcile(task)
    cohort = EvalSamplingCohort.objects.get(task=task)
    # Membership was certified before the interruption; nothing was written
    # after it, and a lease owned by another worker is never released by us.
    assert cohort.complete
    assert not EvalLogger.objects.filter(eval_task_id=str(task.id)).exists()
    assert EvalTask.objects.get(pk=task.pk).continuous_cursor is None
    assert cohort.lease_token == (other if interruption == "lease" else None)


def test_closed_freeze_waits_for_settle_margin_and_excludes_unsettled_arrivals(
    project, custom_eval_config, monkeypatch, settings
):
    from tracer.services.eval_tasks.sampling.policy import parse_time

    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 60
    task = make_task(project, custom_eval_config)
    stub_materialization(monkeypatch, task)
    clock = [timezone.now()]
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.sampling_clock", lambda: clock[0]
    )
    monkeypatch.setattr("tracer.services.eval_tasks.sampling.service._sleep", advance)
    snapshots = []

    def stream(sql, params):
        if "WHERE selected" in sql:
            snapshots.append(params["sp_snapshot"])
            yield [("session:one", "cell", 1, 1)]
        else:
            yield [("span-1", "session:one", "", 0, False)]

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", stream
    )
    reconcile(task)
    end = parse_time(task.sampling_policy["window"]["end"])
    cohort = EvalSamplingCohort.objects.get(task=task)
    assert sleeps and 0 < sum(sleeps) <= 120
    assert clock[0] >= end + timedelta(seconds=120)
    # Rows that arrived within the margin before the read are not counted, and
    # in-window rows had at least the margin after the window end to arrive.
    assert cohort.frozen_at == clock[0] - timedelta(seconds=60)
    assert cohort.frozen_at >= end + timedelta(seconds=60)
    assert snapshots == [cohort.frozen_at]
    entry = EvalLogger.objects.get(eval_task_id=str(task.id))
    assert (
        entry.output_metadata["_task_selection"]["sampling"]["cohort_frozen_at"]
        == cohort.frozen_at.isoformat()
    )
    # A certified cohort never waits again: later polls only expand members.
    sleeps.clear()
    reconcile(task)
    assert sleeps == []


@pytest.mark.parametrize("value", [-1, 3601, "15"])
def test_settle_margin_setting_is_validated(settings, value):
    from tracer.services.eval_tasks.sampling.service import closed_settle_margin

    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = value
    with pytest.raises(ValueError, match="EVAL_SAMPLING_CLOSED_SETTLE_SECONDS"):
        closed_settle_margin()


def _preview(auth_client, project, policy, **extra):
    return auth_client.post(
        "/tracer/eval-task/sampling_preview/",
        {"project": str(project.id), "sampling_policy": policy, **extra},
        format="json",
    )


@pytest.mark.parametrize(
    "error,status,text",
    [
        ("filter", 400, "cannot define a sampling population"),
        ("oversized", 400, "'contact_id'"),
        ("ceiling", 400, "row/byte ceiling"),
        ("pressure", 503, "Narrow the window"),
        ("unavailable", 503, "Narrow the window"),
        ("network", 503, "Narrow the window"),
        ("dropped", 503, "Narrow the window"),
        ("server_busy", 503, "Narrow the window"),
        ("server_defect", 500, "failed unexpectedly"),
        ("bug", 500, "failed unexpectedly"),
    ],
)
def test_preview_maps_failures_to_precise_statuses(
    auth_client, project, custom_eval_config, monkeypatch, error, status, text
):
    from clickhouse_driver import errors as driver_errors
    from django.core.cache import cache

    from tracer.services.eval_tasks.sampling import errors

    cache.clear()
    policy = make_task(project, custom_eval_config).sampling_policy
    policy.update(
        sampling_unit="attribute",
        unit_attribute={"key": "contact_id"},
        group_by=[{"key": "locale"}],
    )

    def fail(sql, params):
        if error == "oversized":
            raise errors.classify_read_error(
                RuntimeError("Code: 395. EVAL_SAMPLING_OVERSIZED_UNIT"), policy=policy
            )
        if error == "ceiling":
            exc = RuntimeError("Code: 158. Limit for rows exceeded")
            exc.code = 158
            raise errors.classify_read_error(exc, policy=policy)
        if error == "pressure":
            raise errors.classify_read_error(TimeoutError("deadline"), policy=policy)
        if error == "unavailable":
            raise ConnectionRefusedError("private host detail")
        if error == "network":
            raise driver_errors.NetworkError("private host detail")
        if error == "dropped":
            # The native driver's error when the server closes mid-read.
            raise EOFError("private host detail")
        if error == "server_busy":
            raise driver_errors.ServerException("private host detail", code=202)
        if error == "server_defect":
            # A server-side defect (here: an unknown identifier) is not an
            # outage and must not be reported as one.
            raise driver_errors.ServerException("private SQL detail", code=47)
        raise TypeError("private SQL detail")
        yield  # pragma: no cover - generator shape of stream_query

    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query", fail
    )
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.sampling_clock", timezone.now
    )
    if error == "filter":
        monkeypatch.setattr(
            "tracer.selectors.eval_tasks.row_resolver._build_sample_query",
            lambda **_: (_ for _ in ()).throw(
                ValueError("date_range must contain exactly two values")
            ),
        )
    response = _preview(auth_client, project, policy)
    body = response.content.decode()
    assert response.status_code == status, body
    assert text in body
    assert "private" not in body


def test_preview_rejects_a_statement_over_its_compiler_budget_as_a_client_error(
    auth_client, project, custom_eval_config, monkeypatch
):
    from django.core.cache import cache

    from tracer.services.eval_tasks.sampling import query

    cache.clear()
    # The real compiler and stream_query run; only the byte budget is lowered
    # so an ordinary policy exceeds it. No statement may reach ClickHouse.
    monkeypatch.setitem(query.READ_SETTINGS, "max_query_size", 64)
    with patch(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client"
    ) as client:
        response = _preview(
            auth_client, project, make_task(project, custom_eval_config).sampling_policy
        )
    assert response.status_code == 400, response.content
    assert "compiler budget" in response.content.decode()
    client.assert_not_called()


def test_preview_principal_and_time_filter_errors_are_client_errors(
    auth_client, project, custom_eval_config
):
    from django.core.cache import cache

    from tracer.services.filter_principal_context import FilterPrincipalContextError

    cache.clear()
    policy = make_task(project, custom_eval_config).sampling_policy
    with patch("tracer.views.eval_sampling.preview_policy") as preview:
        timed = _preview(
            auth_client,
            project,
            policy,
            filters={
                "filters": [
                    {
                        "column_id": "start_time",
                        "filter_config": {
                            "filter_type": "datetime",
                            "filter_op": "between",
                            "filter_value": [
                                "2026-10-01T00:00:00Z",
                                "2026-10-02T00:00:00Z",
                            ],
                        },
                    }
                ]
            },
        )
        with patch(
            "tracer.views.eval_sampling.bind_request_my_annotations_principal",
            side_effect=FilterPrincipalContextError("principal mismatch"),
        ):
            principal = _preview(auth_client, project, policy)
    assert timed.status_code == 400 and "policy window" in timed.content.decode()
    assert principal.status_code == 400
    preview.assert_not_called()


def test_preview_strips_hidden_task_window_before_reading(
    auth_client, project, custom_eval_config
):
    from django.core.cache import cache

    cache.clear()
    policy = make_task(project, custom_eval_config).sampling_policy
    seen = []

    def preview(task, **_):
        seen.append(task.filters)
        return {"strata": []}

    with patch("tracer.views.eval_sampling.preview_policy", side_effect=preview):
        response = _preview(
            auth_client,
            project,
            policy,
            filters={
                "date_range": ["2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z"],
                "date_preset": "custom",
                "observation_type": ["llm"],
            },
        )
    assert response.status_code == 200, response.content
    assert seen == [{"observation_type": ["llm"], "project_id": str(project.id)}]


def test_preview_guard_limits_concurrency_and_rate_per_actor(
    auth_client, user, project, custom_eval_config, settings, monkeypatch
):
    from django.core.cache import cache

    from tracer.services.eval_tasks.sampling.preview_guard import (
        PreviewThrottled,
        preview_slot,
    )

    cache.clear()
    settings.EVAL_SAMPLING_PREVIEW_PER_MINUTE = 3
    # One fixed minute bucket: the assertions never straddle a boundary.
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.preview_guard._clock",
        lambda: 1_800_000_000.0,
    )
    policy = make_task(project, custom_eval_config).sampling_policy
    actor = f"{project.organization_id}:{user.pk}"
    with patch(
        "tracer.views.eval_sampling.preview_policy", return_value={"strata": []}
    ) as preview:
        with preview_slot(actor):  # this actor already has a read in flight
            busy = _preview(auth_client, project, policy)
            with preview_slot(f"{project.organization_id}:someone-else"):
                pass  # other actors are not blocked
        first = _preview(auth_client, project, policy)
        second = _preview(auth_client, project, policy)
        third = _preview(auth_client, project, policy)
    assert busy.status_code == 429 and "already running" in busy.content.decode()
    assert int(busy["Retry-After"]) >= 1
    assert first.status_code == 200 and second.status_code == 200
    assert third.status_code == 429 and "per minute" in third.content.decode()
    assert int(third["Retry-After"]) >= 1
    assert preview.call_count == 2
    with pytest.raises(PreviewThrottled, match="per minute"):
        with preview_slot(actor):
            pass


def test_preview_guard_fails_closed_when_its_cache_is_unavailable(
    auth_client, project, custom_eval_config
):
    policy = make_task(project, custom_eval_config).sampling_policy
    with (
        patch(
            "tracer.services.eval_tasks.sampling.preview_guard.cache.add",
            side_effect=ConnectionError("cache down"),
        ),
        patch("tracer.views.eval_sampling.preview_policy") as preview,
    ):
        response = _preview(auth_client, project, policy)
    assert response.status_code == 503
    preview.assert_not_called()


def test_policy_task_saves_without_hidden_task_window(
    auth_client, project, custom_eval_config
):
    policy = make_task(project, custom_eval_config).sampling_policy
    created = auth_client.post(
        "/tracer/eval-task/",
        {
            "project": str(project.id),
            "name": "historical policy",
            "run_type": "historical",
            "sampling_rate": 100,
            "sampling_policy": policy,
            "evals": [str(custom_eval_config.id)],
            "filters": {
                "project_id": str(project.id),
                "date_range": ["2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z"],
                "date_preset": "custom",
            },
        },
        format="json",
    )
    assert created.status_code == 200, created.data
    task = EvalTask.objects.get(pk=created.data["result"]["id"])
    assert task.filters == {"project_id": str(project.id)}
    # Older edit drawers always send a date range; it is neither stored nor
    # treated as a population change for a policy task.
    edited = auth_client.patch(
        "/tracer/eval-task/update_eval_task/",
        {
            "eval_task_id": str(task.id),
            "edit_type": "edit_rerun",
            "name": "renamed",
            "filters": {
                "project_id": str(project.id),
                "date_range": ["2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z"],
            },
        },
        format="json",
    )
    assert edited.status_code == 200, edited.data
    task.refresh_from_db()
    assert task.name == "renamed"
    assert task.filters == {"project_id": str(project.id)}
    timed = auth_client.patch(
        f"/tracer/eval-task/{task.id}/",
        {
            "filters": {
                "filters": [
                    {
                        "column_id": "created_at",
                        "filter_config": {
                            "filter_type": "datetime",
                            "filter_op": "greater_than",
                            "filter_value": "2026-10-01T00:00:00Z",
                        },
                    }
                ]
            }
        },
        format="json",
    )
    assert timed.status_code == 400
    assert "policy window" in timed.content.decode()


def test_stored_policy_task_with_hidden_window_fails_closed(
    project, custom_eval_config
):
    from tracer.services.eval_tasks.sampling.policy import InvalidSamplingPolicy
    from tracer.services.eval_tasks.sampling.service import task_policy

    task = make_task(project, custom_eval_config)
    EvalTask.objects.filter(pk=task.pk).update(
        filters={"date_range": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]}
    )
    task.refresh_from_db()
    with pytest.raises(InvalidSamplingPolicy, match="task date range"):
        task_policy(task, now=timezone.now())
