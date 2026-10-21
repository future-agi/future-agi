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
                "end": (now - timedelta(seconds=1)).isoformat(),
            },
            "default": {"target_n": 1} if mode == "closed" else {"rate": 50},
        },
    )
    task.evals.add(config)
    return task


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
    stub_materialization(monkeypatch, task)
    rows = [
        [
            ("first-a", "session:kept", "original", 0.5, True),
            ("first-b", "session:skipped", "original", 0.5, False),
        ]
    ]
    monkeypatch.setattr(
        "tracer.services.eval_tasks.sampling.service.stream_query",
        lambda *_: iter(rows),
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


def test_preview_dependency_failure_is_sanitized(
    auth_client, project, custom_eval_config
):
    with patch(
        "tracer.views.eval_sampling.preview_policy",
        side_effect=RuntimeError("private SQL password"),
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
    assert response.status_code == 503
    assert "private SQL password" not in response.content.decode()
    assert "Narrow the window" in response.content.decode()


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
