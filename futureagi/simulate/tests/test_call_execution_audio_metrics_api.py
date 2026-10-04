"""R08/R10/R12, AC09/AC12: authorized detail, snapshots and rerun lifecycle."""

import copy
import json
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.voice.services.audio_analysis.envelope import empty_envelope
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, CallExecutionSnapshot
from simulate.models.test_execution import TestExecution as Execution
from simulate.serializers.test_execution import (
    CallExecutionDetailSerializer,
    CallExecutionSnapshotSerializer,
)
from simulate.views.run_results_v3 import (
    CallExecutionV3DetailView,
    build_call_execution_detail,
)
from simulate.views.run_test import (
    CallExecutionDetailView,
    CallExecutionRerunView,
    _clear_call_execution_data,
    _save_eval_snapshot,
)
from tracer.views.shared_link import _resolve_shared_call_execution


@pytest.fixture
def audio_call(db, organization, workspace, settings):
    settings.VOICE_AUDIO_METRICS_ENABLED = True
    settings.VOICE_AUDIO_METRICS_ORG_ALLOWLIST = []
    agent = AgentDefinition.objects.create(
        agent_name="Audio target",
        agent_type="voice",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Audio scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(
        name="Audio run",
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    execution = Execution.objects.create(run_test=run, agent_definition=agent)
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        audio_metrics=phase_a(),
        audio_provenance={"private": "call-recordings/secret"},
    )


def phase_a():
    env = empty_envelope(0, "not_validated")
    env.update(state="partial", analysis_id="ce96a315-1f28-4640-92f4-d434904c612a")
    env["metrics"]["average_pitch_hz"].update(
        state="available", value=220.0, reason=None
    )
    env["metrics"]["estimated_snr_db"].update(state="available", value=0.0, reason=None)
    return env


def request_detail(view, call, user, workspace, *, org=None, authenticated=True):
    request = APIRequestFactory().get("/")
    request.organization = org or user.organization
    request.workspace = workspace
    if authenticated:
        force_authenticate(request, user=user)
    return view.as_view()(request, call_execution_id=call.id)


@pytest.mark.parametrize("view", [CallExecutionV3DetailView, CallExecutionDetailView])
def test_detail_envelope_and_export(view, audio_call, user, workspace):
    response = request_detail(view, audio_call, user, workspace)
    assert response.status_code == 200
    assert response.data["audio_metrics"] == phase_a()
    exported = json.loads(json.dumps(response.data, default=str))
    assert exported["audio_metrics"] == response.data["audio_metrics"]
    assert "audio_provenance" not in exported
    assert "call-recordings/secret" not in json.dumps(exported)


def test_legacy_detail_identical_to_v3(audio_call, user, workspace):
    assert (
        request_detail(CallExecutionDetailView, audio_call, user, workspace).data[
            "audio_metrics"
        ]
        == request_detail(CallExecutionV3DetailView, audio_call, user, workspace).data[
            "audio_metrics"
        ]
    )


@pytest.mark.parametrize(
    "kind,reason", [("voice", "not_analyzed"), ("text", "not_applicable")]
)
def test_null_column_and_chat(audio_call, kind, reason):
    audio_call.audio_metrics = None
    audio_call.simulation_call_type = kind
    result = CallExecutionDetailSerializer(
        audio_call, context={"include_audio_metrics": True}
    ).data["audio_metrics"]
    assert result == empty_envelope(0, reason)


def test_pending_with_available_sibling_and_timeout(audio_call):
    audio_call.audio_metrics["state"] = "pending"
    audio_call.audio_metrics["deadline_at"] = "2000-01-01T00:00:00Z"
    audio_call.audio_metrics["metrics"]["voice_quality_index"].update(
        state="pending", reason=None
    )
    safe = build_call_execution_detail(audio_call, include_audio_metrics=True)[
        "audio_metrics"
    ]
    assert safe["state"] == "partial"
    assert safe["metrics"]["voice_quality_index"]["reason"] == "timeout"


def test_shared_link_resolve_has_no_audio_metrics_key(
    audio_call, workspace, organization
):
    CallExecutionSnapshot.objects.create(
        call_execution=audio_call, rerun_type="call_and_eval", audio_metrics=phase_a()
    )
    link = SimpleNamespace(
        resource_id=audio_call.id, organization=organization, workspace=workspace
    )
    result = _resolve_shared_call_execution(link)
    assert result is not None
    assert "audio_metrics" not in json.dumps(result, default=str)
    assert "audio_provenance" not in json.dumps(result, default=str)


def test_flag_off_key_absent_no_existing_field_changed(audio_call, settings):
    context = {"include_audio_metrics": True}
    enabled = dict(CallExecutionDetailSerializer(audio_call, context=context).data)
    settings.VOICE_AUDIO_METRICS_ENABLED = False
    disabled = dict(CallExecutionDetailSerializer(audio_call, context=context).data)
    enabled.pop("audio_metrics")
    assert enabled == disabled
    audio_call.refresh_from_db()
    assert audio_call.audio_metrics == phase_a()


@pytest.mark.parametrize("include", [None, False, 1, "true"])
def test_explicit_true_required(audio_call, include):
    assert (
        "audio_metrics"
        not in CallExecutionDetailSerializer(
            audio_call, context={"include_audio_metrics": include}
        ).data
    )


def test_allowlist_denial_not_enabled(audio_call, settings):
    settings.VOICE_AUDIO_METRICS_ORG_ALLOWLIST = ["other-org"]
    assert build_call_execution_detail(audio_call, include_audio_metrics=True)[
        "audio_metrics"
    ] == empty_envelope(0, "not_enabled")


def test_unknown_schema_version_unsupported(audio_call):
    audio_call.audio_metrics = {"schema_version": 99, "private": "secret"}
    assert build_call_execution_detail(audio_call, include_audio_metrics=True)[
        "audio_metrics"
    ] == {"schema_version": 99, "state": "unsupported"}


@pytest.mark.parametrize("view", [CallExecutionV3DetailView, CallExecutionDetailView])
def test_unauthenticated_unchanged(view, audio_call, user, workspace):
    response = request_detail(view, audio_call, user, workspace, authenticated=False)
    assert response.status_code in {401, 403}
    assert "audio_metrics" not in json.dumps(response.data)


@pytest.mark.parametrize("view", [CallExecutionV3DetailView, CallExecutionDetailView])
@pytest.mark.parametrize("scope", ["organization", "workspace"])
def test_out_of_scope_404(view, scope, audio_call, user, workspace):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    org = user.organization
    if scope == "organization":
        org = Organization.objects.create(name="Other")
    else:
        workspace = Workspace.objects.create(name="Other", organization=org, created_by=user)
    response = request_detail(view, audio_call, user, workspace, org=org)
    assert response.status_code == 404
    assert "audio_metrics" not in json.dumps(response.data)


@pytest.mark.parametrize(
    "clear",
    [_clear_call_execution_data, CallExecutionRerunView()._clear_call_execution_data],
)
def test_snapshot_copies_envelope_before_both_reset_paths(audio_call, clear):
    before = copy.deepcopy(audio_call.audio_metrics)
    clear(audio_call)
    audio_call.refresh_from_db()
    assert audio_call.snapshots.get().audio_metrics == before
    assert audio_call.audio_analysis_generation == 1
    assert audio_call.audio_metrics is None
    assert audio_call.audio_provenance is None


@pytest.mark.parametrize(
    "save_snapshot", [_save_eval_snapshot, CallExecutionRerunView()._save_eval_snapshot]
)
def test_eval_only_rerun_keeps_envelope(audio_call, save_snapshot):
    before = copy.deepcopy(audio_call.audio_metrics)
    save_snapshot(audio_call)
    audio_call.refresh_from_db()
    assert audio_call.snapshots.get().audio_metrics == before
    assert audio_call.audio_metrics == before
    assert audio_call.audio_analysis_generation == 0


def test_bulk_reset_clears_and_bumps(audio_call):
    audio_call.reset_to_default(save=False)
    CallExecution.objects.bulk_update([audio_call], CallExecution.RESET_FIELDS)
    audio_call.refresh_from_db()
    assert audio_call.audio_analysis_generation == 1
    assert audio_call.audio_metrics is None
    assert audio_call.audio_provenance is None


def test_snapshot_sanitized_old_and_hidden(audio_call, settings):
    snapshot = CallExecutionSnapshot.objects.create(
        call_execution=audio_call, rerun_type="call_and_eval"
    )
    context = {"include_audio_metrics": True}
    assert CallExecutionSnapshotSerializer(snapshot, context=context).data[
        "audio_metrics"
    ] == empty_envelope(0)
    snapshot.audio_metrics = {**phase_a(), "private": "secret"}
    assert "secret" not in json.dumps(
        CallExecutionSnapshotSerializer(snapshot, context=context).data, default=str
    )
    settings.VOICE_AUDIO_METRICS_ENABLED = False
    assert (
        "audio_metrics"
        not in CallExecutionSnapshotSerializer(snapshot, context=context).data
    )


# These use real model/serializer objects with no database. They independently
# pin the projection even when the local integration database is unavailable.
@pytest.fixture
def unsaved_audio_call(settings):
    from accounts.models.organization import Organization

    settings.VOICE_AUDIO_METRICS_ENABLED = True
    settings.VOICE_AUDIO_METRICS_ORG_ALLOWLIST = []
    org = Organization(name="Offline")
    run = RunTest(name="Offline", organization=org)
    execution = Execution(run_test=run)
    return CallExecution(test_execution=execution, audio_metrics=phase_a())


def test_offline_detail_projection_and_visibility(unsaved_audio_call, settings):
    call = unsaved_audio_call
    serializer = CallExecutionDetailSerializer(context={"include_audio_metrics": True})
    assert serializer.get_audio_metrics(call) == phase_a()
    settings.VOICE_AUDIO_METRICS_ORG_ALLOWLIST = ["denied"]
    assert serializer.get_audio_metrics(call) == empty_envelope(0, "not_enabled")
    settings.VOICE_AUDIO_METRICS_ENABLED = False
    assert serializer.get_audio_metrics(call) is None
    assert CallExecutionDetailSerializer().get_audio_metrics(call) is None


def test_offline_snapshot_projection_and_reset(unsaved_audio_call):
    call = unsaved_audio_call
    snap = CallExecutionSnapshot(
        call_execution=call, audio_metrics=copy.deepcopy(call.audio_metrics)
    )
    result = CallExecutionSnapshotSerializer(
        snap, context={"include_audio_metrics": True}
    ).data
    assert result["audio_metrics"] == phase_a()
    call.reset_to_default(save=False)
    assert call.audio_analysis_generation == 1
    assert call.audio_metrics is None
    assert snap.audio_metrics == phase_a()


def test_offline_detail_key_removed_and_context_forwarded(
    unsaved_audio_call, monkeypatch
):
    from rest_framework.serializers import ModelSerializer

    monkeypatch.setattr(
        ModelSerializer,
        "to_representation",
        lambda self, obj: {"id": "call", "audio_metrics": phase_a()},
    )
    assert CallExecutionDetailSerializer(unsaved_audio_call).data == {"id": "call"}
    assert (
        "audio_metrics"
        in CallExecutionDetailSerializer(
            unsaved_audio_call, context={"include_audio_metrics": True}
        ).data
    )


def test_offline_shared_link_default_is_closed(unsaved_audio_call, monkeypatch):
    from simulate.views import run_results_v3
    from tracer.views import shared_link

    monkeypatch.setattr(
        shared_link, "_get_shared_call_execution", lambda *args: unsaved_audio_call
    )
    captured = {}

    def build(call, request=None, workspace=None, include_audio_metrics=False):
        captured["include"] = include_audio_metrics
        return {"id": str(call.id)}

    monkeypatch.setattr(run_results_v3, "build_call_execution_detail", build)
    _resolve_shared_call_execution(
        SimpleNamespace(
            resource_id=unsaved_audio_call.id, organization=None, workspace=None
        )
    )
    assert captured["include"] is False
