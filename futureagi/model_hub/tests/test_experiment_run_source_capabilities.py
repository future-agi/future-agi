"""Run-source capability contracts; no database, dispatch or destination probes.

GraphVersion targets remain executable under either Console-agent setting at
head-73 (test_experiment_target_kind.py). Do not turn that unrelated switch into
a new gate on Agent Playground graphs.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.serializers import experiments as experiment_serializers
from model_hub.services import http_experiment_source
from model_hub.services.experiment_source_capabilities import get_experiment_run_sources
from tfc.capabilities import views as capability_views
from tfc.capabilities.contracts import ExperimentRunSourcesSerializer
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseState
from tfc.temporal import experiments as experiment_worker

ROUTE = "/api/capabilities/"
KINDS = ("prompt", "agent", "http", "a2a", "mcp_tool")


@pytest.fixture
def scope():
    organization = SimpleNamespace(id=uuid4())
    return SimpleNamespace(
        organization=organization,
        workspace=SimpleNamespace(
            id=uuid4(), organization_id=organization.id, is_active=True
        ),
    )


@pytest.mark.parametrize("console_enabled", [False, True], ids=["off", "on"])
@pytest.mark.parametrize(
    ("kind", "available", "reason"),
    [
        ("prompt", True, None),
        ("agent", True, None),
        ("http", True, None),
        ("a2a", True, None),
        ("mcp_tool", False, "MCP tool experiments are not built in this release."),
    ],
    ids=KINDS,
)
def test_each_kind_matches_create_and_worker_support(
    scope, settings, console_enabled, kind, available, reason
):
    settings.DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED = console_enabled

    report = get_experiment_run_sources(scope)

    assert report["workspace_id"] == str(scope.workspace.id)
    assert set(report["sources"]) == set(KINDS)
    assert report["sources"][kind] == {"available": available, "reason": reason}


@pytest.mark.parametrize(
    "missing", ["organization", "workspace", "foreign_workspace", "inactive_workspace"]
)
def test_no_authorized_scope_offers_no_sources(scope, missing):
    if missing == "foreign_workspace":
        scope.workspace.organization_id = uuid4()
    elif missing == "inactive_workspace":
        scope.workspace.is_active = False
    else:
        setattr(scope, missing, None)

    report = get_experiment_run_sources(scope)

    assert report["workspace_id"] is None
    assert report["sources"] == {
        kind: {
            "available": False,
            "reason": "Select a workspace to create an experiment.",
        }
        for kind in KINDS
    }


def test_only_authenticated_request_scope_is_used(scope):
    foreign_id = str(uuid4())
    scope.query_params = {"workspace_id": foreign_id}
    scope.headers = {"X-Workspace-Id": foreign_id}
    first = get_experiment_run_sources(scope)
    scope.workspace.id = uuid4()
    second = get_experiment_run_sources(scope)

    assert first["workspace_id"] != foreign_id
    assert second["workspace_id"] == str(scope.workspace.id)
    assert first["workspace_id"] != second["workspace_id"]
    scope.workspace = None
    assert get_experiment_run_sources(scope)["workspace_id"] is None


def test_http_requires_creation_serializer_support(scope, monkeypatch):
    class WithoutHttp(experiment_serializers.PromptConfigEntrySerializer):
        target_type = serializers.ChoiceField(choices=["prompt", "agent"])

    monkeypatch.setattr(
        experiment_serializers, "PromptConfigEntrySerializer", WithoutHttp
    )

    assert get_experiment_run_sources(scope)["sources"]["http"] == {
        "available": False,
        "reason": "HTTP endpoint experiments are not built in this release.",
    }


@pytest.mark.parametrize(
    ("kind", "workflow"),
    [
        ("prompt", "ProcessPromptV2Workflow"),
        ("agent", "ProcessAgentWorkflow"),
        ("http", "ProcessHttpSourceWorkflow"),
        ("a2a", "ProcessHttpSourceWorkflow"),
    ],
)
def test_requires_registered_workflow(scope, monkeypatch, kind, workflow):
    remaining = [
        runner
        for runner in experiment_worker.get_workflows()
        if runner.__name__ != workflow
    ]
    monkeypatch.setattr(experiment_worker, "get_workflows", lambda: remaining)

    capability = get_experiment_run_sources(scope)["sources"][kind]

    assert capability["available"] is False
    assert capability["reason"].endswith("Contact your administrator.")


@pytest.mark.parametrize(
    ("kind", "activity"),
    [
        ("prompt", "process_row_activity"),
        ("agent", "execute_native_graph_activity"),
        ("http", "process_http_row_activity"),
        ("a2a", "process_http_row_activity"),
    ],
)
def test_requires_registered_executor(scope, monkeypatch, kind, activity):
    remaining = [
        runner
        for runner in experiment_worker.get_activities()
        if runner.__name__ != activity
    ]
    monkeypatch.setattr(experiment_worker, "get_activities", lambda: remaining)

    capability = get_experiment_run_sources(scope)["sources"][kind]

    assert capability["available"] is False
    assert capability["reason"].endswith("Contact your administrator.")


@pytest.mark.parametrize(
    "symbol", ["persist_http_source", "run_http_snapshot_row", "execute_http_row"]
)
def test_http_requires_real_creation_and_execution_hooks(scope, monkeypatch, symbol):
    monkeypatch.setattr(http_experiment_source, symbol, None)

    report = get_experiment_run_sources(scope)

    assert report["sources"]["http"] == {
        "available": False,
        "reason": (
            "HTTP endpoint experiments are unavailable on this server. "
            "Contact your administrator."
        ),
    }
    assert report["sources"]["prompt"]["available"] is True
    # An A2A agent runs on the HTTP source path, so it goes with it.
    assert report["sources"]["a2a"]["available"] is False
    assert report["sources"]["a2a"]["reason"].endswith("Contact your administrator.")


def test_a2a_requires_its_reply_reader(scope, monkeypatch):
    monkeypatch.setattr(http_experiment_source, "a2a_reply_text", None)

    report = get_experiment_run_sources(scope)

    assert report["sources"]["a2a"] == {
        "available": False,
        "reason": "A2A experiments are unavailable on this server. Contact your administrator.",
    }
    assert report["sources"]["http"]["available"] is True


def test_a2a_requires_creation_serializer_support(scope, monkeypatch):
    class WithoutA2a(experiment_serializers.PromptConfigEntrySerializer):
        target_type = serializers.ChoiceField(choices=["prompt", "agent", "http"])

    monkeypatch.setattr(
        experiment_serializers, "PromptConfigEntrySerializer", WithoutA2a
    )

    assert get_experiment_run_sources(scope)["sources"]["a2a"] == {
        "available": False,
        "reason": "A2A experiments are not built in this release.",
    }


def test_http_requires_outbound_guard(scope, monkeypatch):
    monkeypatch.setattr(http_experiment_source.outbound_destination, "request", None)

    capability = get_experiment_run_sources(scope)["sources"]["http"]

    assert capability["available"] is False
    assert "Contact your administrator." in capability["reason"]


def test_serializer_choices_alone_do_not_enable_unbuilt_executors(scope, monkeypatch):
    class WithUnbuiltTargets(experiment_serializers.PromptConfigEntrySerializer):
        target_type = serializers.ChoiceField(choices=KINDS)

    monkeypatch.setattr(
        experiment_serializers, "PromptConfigEntrySerializer", WithUnbuiltTargets
    )

    report = get_experiment_run_sources(scope)

    for kind in ("mcp_tool",):
        assert report["sources"][kind]["available"] is False
        assert "not built in this release" in report["sources"][kind]["reason"]


def test_read_does_not_create_execute_or_probe(scope, monkeypatch):
    probes = []
    for module, name in (
        (http_experiment_source, "persist_http_source"),
        (http_experiment_source, "run_http_snapshot_row"),
        (http_experiment_source, "execute_http_row"),
        (http_experiment_source, "a2a_reply_text"),
        (http_experiment_source.outbound_destination, "request"),
    ):
        probe = Mock(side_effect=AssertionError("Capability reads must not execute"))
        monkeypatch.setattr(module, name, probe)
        probes.append(probe)

    report = get_experiment_run_sources(scope)

    assert report["sources"]["http"]["available"] is True
    assert report["sources"]["a2a"]["available"] is True
    for probe in probes:
        probe.assert_not_called()


@pytest.fixture
def capability_api(monkeypatch, scope):
    # Isolate the existing license/rollout reads, not the run-source report.
    monkeypatch.setattr(capability_views, "FEATURE_REGISTRY", {})
    monkeypatch.setattr(
        capability_views.service,
        "get_deployment_flavor",
        lambda: DeploymentFlavor.OSS,
    )
    monkeypatch.setattr(
        capability_views.service,
        "get_deployment_location",
        lambda: DeploymentLocation.SELF_HOSTED,
    )
    monkeypatch.setattr(
        capability_views.CapabilitiesView,
        "_get_license_state",
        lambda self: LicenseState.MISSING,
    )
    monkeypatch.setattr(
        capability_views.CapabilitiesView,
        "_rollout_admissions",
        lambda self, org_id, workspace_id: {},
    )
    monkeypatch.setattr(
        capability_views.CapabilitiesView, "_is_admin", lambda self, request: False
    )

    def call(method="get", *, authenticated=True):
        request = getattr(APIRequestFactory(), method)(ROUTE)
        request.organization = scope.organization
        request.workspace = scope.workspace
        if authenticated:
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        return capability_views.CapabilitiesView.as_view()(request)

    return call


def test_existing_capabilities_get_adds_typed_workspace_report(capability_api, scope):
    response = capability_api()

    assert response.status_code == 200
    assert "deployment_flavor" in response.data
    assert "display_mode" in response.data
    assert "license_state" in response.data
    # Run sources live in their own field; features keeps only registry entries.
    assert set(response.data["features"]) <= {"dataset_stars"}
    assert "license" not in response.data
    report = response.data["experiment_run_sources"]
    assert report == get_experiment_run_sources(scope)
    serializer = ExperimentRunSourcesSerializer(data=report)
    assert serializer.is_valid(), serializer.errors
    assert dict(serializer.data) == report
    assert response["Cache-Control"] == "private, no-store"
    assert "DSX_" not in str(report)


def test_missing_workspace_preserves_capabilities_get(capability_api, scope):
    scope.workspace = None

    response = capability_api()

    assert response.status_code == 200
    assert response.data["experiment_run_sources"]["workspace_id"] is None
    assert all(
        not value["available"]
        for value in response.data["experiment_run_sources"]["sources"].values()
    )


def test_capabilities_get_still_requires_authentication(capability_api):
    response = capability_api(authenticated=False)

    assert response.status_code in (401, 403)
    assert "experiment_run_sources" not in response.data


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_report_does_not_add_write_methods(capability_api, method):
    assert capability_api(method).status_code == 405


def test_unavailable_capability_requires_a_reason(scope):
    report = get_experiment_run_sources(scope)
    report["sources"]["http"] = {"available": False, "reason": None}
    serializer = ExperimentRunSourcesSerializer(data=report)

    assert not serializer.is_valid()
    assert "sources" in serializer.errors
