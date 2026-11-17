"""Cooperative STOPPED is a shared read model, never a canonical terminal write.

Real native fixtures, state persistence, ownership selection and authenticated
GET. No workflow/provider is needed: this begins at the drained worker's real
persist_run_state call. The existing persistence suite proves workflow Resume.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services import experiment_run_control as control_service
from model_hub.services.experiment_run_state import persist_run_state
from model_hub.services.native_run_recording import (
    finish_native_run,
    mark_native_run,
    record_attempt,
    start_attempt,
)
from model_hub.services.results_catalog import experiment_runs_detail, list_results_runs
from model_hub.tests.test_experiment_run_control_run_ids import (
    bound,
    native_experiment,
    stamp,
)
from model_hub.tests.test_native_run_binding import bind
from model_hub.views.experiments import ExperimentsTableV2DetailView

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def world(organization, workspace, user, prompt_version):
    return native_experiment(organization, workspace, user, prompt_version)


def persist(w, execution, phase):
    persist_run_state(
        {
            "experiment_id": str(w["experiment"].id),
            "workflow_run_id": str(execution.workflow_run_id),
            "config_id": str(execution.config_id),
            "phase": phase,
        }
    )


def request_context(w):
    return SimpleNamespace(
        user=w["created_by"],
        organization=w["dataset"].organization,
        workspace=w["dataset"].workspace,
        method="GET",
    )


def get_detail(w, *, organization=None, workspace=None, authenticated=True):
    request = APIRequestFactory().get(
        f"/model-hub/experiments/v2/{w['experiment'].id}/"
    )
    request.organization = organization or w["dataset"].organization
    request.workspace = workspace or w["dataset"].workspace
    if authenticated:
        force_authenticate(request, user=w["created_by"])
    # The detail GET may read durable control bindings, never Temporal.
    with patch.object(
        control_service, "get_client", side_effect=AssertionError("GET used Temporal")
    ):
        return ExperimentsTableV2DetailView.as_view()(
            request,
            experiment_id=w["experiment"].id,
        )


def detail(w):
    response = get_detail(w)
    assert response.status_code == 200, response.data
    return response.data["result"]


def rows(payload):
    return {row["run_id"]: row for row in payload["runs"]}


def evidence(w):
    ids = ExperimentRun.no_workspace_objects.filter(
        experiment=w["experiment"]
    ).values_list("id", flat=True)
    return {
        "runs": list(
            ExperimentRun.no_workspace_objects.filter(id__in=ids)
            .order_by("id")
            .values()
        ),
        "items": list(
            ExperimentRunItem.no_workspace_objects.filter(run_id__in=ids)
            .order_by("id")
            .values()
        ),
    }


def record(w, target, *, complete=False):
    mark_native_run(dataset=w["dataset"], run_id=target.run.id, status="queued")
    snapshot_rows = list(w["row_map"].values())
    for index, row in enumerate(snapshot_rows if complete else snapshot_rows[:1]):
        args = {
            "dataset": w["dataset"],
            "run_id": target.run.id,
            "snapshot_row_id": row.id,
            "attempt_key": f"detail-{index}",
        }
        start_attempt(**args)
        record_attempt(
            **args,
            status="completed",
            output={"text": f"result-{index}"},
            token_usage={
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
            cost=0.001,
        )
    if complete:
        finish_native_run(dataset=w["dataset"], run_id=target.run.id)


@pytest.mark.parametrize("sibling_complete", [True, False], ids=["completed", "active"])
def test_persisted_stop_projects_only_owned_run_and_resume_preserves_evidence(
    world,
    sibling_complete,
):
    w, (selected, sibling) = world
    record(w, selected)
    record(w, sibling, complete=sibling_complete)
    own = bound(selected, "detail-selected")
    other = bound(sibling, "detail-sibling")
    persist(w, own, "RUNNING")
    persist(w, other, "COMPLETED" if sibling_complete else "RUNNING")
    before = detail(w)
    saved = evidence(w)
    assert len(saved["items"]) == (3 if sibling_complete else 2)
    persist(w, own, "STOPPED")

    # A wrapping spy observes batching without substituting selection/results.
    with patch.object(
        control_service, "_named_runs", wraps=control_service._named_runs
    ) as resolve:
        stopped = detail(w)
    before_rows, stopped_rows = rows(before), rows(stopped)
    assert stopped_rows[str(selected.run.id)] == {
        **before_rows[str(selected.run.id)],
        "status": "cancelled",
        "progress": {
            **before_rows[str(selected.run.id)]["progress"],
            "status": "Cancelled",
        },
    }
    assert resolve.call_count == 1
    assert resolve.call_args.args[1] == [str(selected.run.id)]
    assert stopped_rows[str(sibling.run.id)] == before_rows[str(sibling.run.id)]
    assert stopped_rows[str(sibling.run.id)]["status"] == (
        "completed" if sibling_complete else "running"
    )
    # Frontend experimentStatusOf prioritizes active siblings over Cancelled;
    # Completed + Cancelled instead reads Stopped (existing runStatus contract).
    assert stopped["status"] == ("Cancelled" if sibling_complete else "Running")
    assert stopped["runs_total"] == before["runs_total"] == 2
    assert stopped["runs_has_more"] is False and stopped["runs_next"] is None
    assert (
        evidence(w) == saved
    )  # includes canonical status, timestamps, usage, receipts
    selected.run.refresh_from_db()
    assert selected.run.status == "running" and selected.run.completed_at is None

    # Catalog labels and filters agree; canonical history stays unchanged.
    context = request_context(w)
    canonical = list_results_runs(
        context,
        w["dataset"].id,
        {
            "experiment_id": w["experiment"].id,
            "status": ["running"],
            "limit": 1,
        },
    )
    assert canonical["count"] == (0 if sibling_complete else 1)
    assert [row["run_id"] for row in canonical["results"]] == (
        [] if sibling_complete else [str(sibling.run.id)]
    )
    assert [row["status"] for row in canonical["results"]] == (
        [] if sibling_complete else ["running"]
    )
    assert canonical["has_more"] is False
    assert canonical["next"] is None
    cancelled = list_results_runs(
        context,
        w["dataset"].id,
        {
            "experiment_id": w["experiment"].id,
            "status": ["cancelled"],
            "include": ["config", "lineage"],
        },
    )
    assert cancelled["count"] == 1
    assert cancelled["results"] == [stopped_rows[str(selected.run.id)]]
    assert cancelled["has_more"] is False
    assert cancelled["next"] is None
    assert evidence(w) == saved

    persist(w, own, "RUNNING")
    resumed = detail(w)
    assert rows(resumed) == before_rows
    assert resumed["status"] == "Running"
    assert evidence(w) == saved


@pytest.mark.parametrize(
    "case",
    [
        "older_control",
        "wrong_config",
        "no_control",
        "superseded",
        "superseded_deleted",
        "latest_running",
    ],
)
def test_unowned_or_stale_stopped_control_cannot_override_detail(world, case):
    w, (selected, sibling) = world
    record(w, selected)
    record(w, sibling)
    execution = None
    if case != "no_control":
        execution = bound(selected, "detail-stale")
        persist(w, execution, "STOPPED")
    if case == "older_control":
        stamp(
            ExperimentRunControl,
            execution,
            selected.run.created_at - timedelta(seconds=1),
        )
    elif case == "wrong_config":
        ExperimentRunControl.objects.filter(pk=execution.pk).update(config_id=uuid4())
    elif case == "no_control":
        w["experiment"].status = "Cancelled"
        w["experiment"].save(update_fields=["status"])
        selected.variant.status = "Cancelled"
        selected.variant.save(update_fields=["status"])
    elif case in {"superseded", "superseded_deleted"}:
        newer = bind(w, execution_id=uuid4())
        mark_native_run(dataset=w["dataset"], run_id=newer.id, status="queued")
        newer_execution = bound(selected, "detail-newer")
        persist(w, newer_execution, "STOPPED")
        if case == "superseded_deleted":
            newer.delete()
    elif case == "latest_running":
        newer_execution = bound(selected, "detail-resumed")
        persist(w, newer_execution, "RUNNING")
        # Stale worker delivery must neither change the latest phase nor GET.
        persist(w, execution, "STOPPED")
    saved = evidence(w)
    payload = detail(w)
    assert rows(payload)[str(selected.run.id)]["status"] == "running"
    assert rows(payload)[str(sibling.run.id)]["status"] == "running"
    catalog = list_results_runs(
        request_context(w),
        w["dataset"].id,
        {"experiment_id": w["experiment"].id, "status": ["cancelled"]},
    )
    assert [row["run_id"] for row in catalog["results"]] == (
        [str(newer.id)] if case == "superseded" else []
    )
    assert catalog["count"] == (1 if case == "superseded" else 0)
    if case == "superseded":
        assert rows(payload)[str(newer.id)]["status"] == "cancelled"
        # The owner may be outside the detail page: never borrow it for history.
        older_page = experiment_runs_detail(
            request_context(w), w["experiment"], limit=1, offset=2
        )
        assert older_page["runs"][0]["run_id"] == str(selected.run.id)
        assert older_page["runs"][0]["status"] == "running"
    assert evidence(w) == saved


def test_parent_cancelled_and_unbound_external_run_are_not_stop_authority(world):
    w, (selected, sibling) = world
    record(w, selected)
    record(w, sibling, complete=True)
    persist(w, bound(sibling, "detail-completed"), "COMPLETED")
    execution = bound(selected, "detail-native")
    persist(w, execution, "STOPPED")
    external = ExperimentRun.objects.create(
        experiment=w["experiment"],
        dataset_version=selected.run.dataset_version,
        task_kind=ExperimentRun.TaskKind.EXTERNAL,
        status=ExperimentRun.Status.RUNNING,
    )
    saved = evidence(w)
    projected = rows(detail(w))
    assert projected[str(selected.run.id)]["status"] == "cancelled"
    assert projected[str(external.id)]["status"] == "running"
    assert evidence(w) == saved


@pytest.mark.parametrize("terminal", ["completed", "partial", "failed", "cancelled"])
def test_stopped_phase_cannot_relabel_a_canonical_terminal_run(world, terminal):
    w, (selected, _) = world
    ended = timezone.now()
    ExperimentRun.no_workspace_objects.filter(pk=selected.run.pk).update(
        status=terminal,
        completed_at=ended,
    )
    execution = bound(selected, "detail-terminal")
    persist(w, execution, "STOPPED")
    saved = evidence(w)
    assert rows(detail(w))[str(selected.run.id)]["status"] == terminal
    assert evidence(w) == saved


@pytest.mark.parametrize("foreign", ["organization", "workspace", "anonymous"])
def test_stopped_detail_keeps_authentication_and_tenant_scope(world, foreign):
    w, (selected, _) = world
    record(w, selected)
    execution = bound(selected, "detail-private")
    persist(w, execution, "STOPPED")
    kwargs = {}
    if foreign == "organization":
        kwargs["organization"] = Organization.objects.create(name="Other detail scope")
    elif foreign == "workspace":
        kwargs["workspace"] = Workspace.objects.create(
            name="Other detail scope",
            organization=w["dataset"].organization,
            created_by=w["created_by"],
            is_default=False,
        )
    else:
        kwargs["authenticated"] = False
    response = get_detail(w, **kwargs)
    assert response.status_code in ({401, 403} if foreign == "anonymous" else {404})
    assert str(selected.run.id) not in str(response.data)


@pytest.mark.parametrize("stored_status", ["queued", "running"])
def test_stopped_catalog_paginates_effective_status_without_rewriting_runs(
    world, stored_status
):
    w, targets = world
    for target in targets:
        ExperimentRun.no_workspace_objects.filter(pk=target.run.pk).update(
            status=stored_status
        )
        persist(w, bound(target, f"catalog-{target.run.id}"), "STOPPED")
    saved = evidence(w)
    context = request_context(w)
    query = {"status": ["cancelled"], "limit": 1}
    first = list_results_runs(context, w["dataset"].id, query)
    second = list_results_runs(context, w["dataset"].id, {**query, "offset": 1})
    assert first["count"] == second["count"] == 2
    assert first["has_more"] is True
    assert first["next"] == 1
    assert second["has_more"] is False
    assert second["next"] is None
    assert [row["run_id"] for row in first["results"] + second["results"]] == [
        str(target.run.id) for target in reversed(targets)
    ]
    assert [row["status"] for row in first["results"] + second["results"]] == [
        "cancelled",
        "cancelled",
    ]
    assert [
        row["progress"]["status"] for row in first["results"] + second["results"]
    ] == ["Cancelled", "Cancelled"]
    active = list_results_runs(
        context, w["dataset"].id, {"status": ["queued", "running"]}
    )
    assert active["count"] == 0
    assert active["results"] == []
    assert evidence(w) == saved
