"""Evidence imports over HTTP: freezing Observe sessions or calls for an
environment, the bundle the review reads (what each source holds and lacks,
and what redaction would replace), and who may make one.

Observe itself is a stand-in here (``observe_evidence``); its reads are covered
in ``tracer/tests/test_observe_evidence.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, Workspace
from accounts.services.simulate_access import restrict_simulate_to_environments
from accounts.tests.test_rbac_regressions import (
    WORKSPACE_WRITE_DENIED,
    _member_client,
    _viewer_client,
)
from model_hub.models.ai_model import AIModel
from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.evidence_import import (
    MAX_SOURCES,
    REDACTION_UNAVAILABLE,
    SOURCE_NOT_FOUND,
    SOURCE_UNAVAILABLE,
)
from simulate.services.hosted_harness import create_hosted_job
from tracer.models.project import Project
from tracer.selectors.observe_evidence import SourceEvidence, ToolCall, Turn

from .test_hosted_harness_channels import _payload

ENVIRONMENTS = "/simulate/api/harness-environments"
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SESSION = str(uuid.uuid4())
MISSING = str(uuid.uuid4())


def _evidence(source_id=SESSION, **overrides) -> SourceEvidence:
    trace = str(uuid.uuid4())
    value = SourceEvidence(
        source_id=source_id,
        trace_ids=(trace,),
        started_at=T0,
        ended_at=T0 + timedelta(minutes=2),
        turns=(
            Turn(
                "user", "Refund my order, I'm at jane.doe@example.com", False, trace, T0
            ),
            Turn("agent", "I've issued a second refund.", False, trace, T0),
        ),
        turns_truncated=False,
        tool_calls=(),
        tool_calls_truncated=False,
        labels=(),
    )
    return replace(value, **overrides)


def _observe(found: dict):
    """Observe holds ``found`` for both the import and every later read."""
    return [
        patch("simulate.services.evidence_import.observe_evidence", return_value=found),
        patch(
            "simulate.selectors.evidence_import.observe_evidence", return_value=found
        ),
    ]


@pytest.fixture
def environment(db, user, workspace):
    job, _ = create_hosted_job(
        user.organization, _payload(), idempotency_key="evidence", workspace=workspace
    )
    return job


@pytest.fixture
def project(db, organization, workspace):
    return Project.objects.create(
        name="Support agent",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def client(user, workspace):
    api = APIClient()
    api.force_authenticate(user=user)
    api.credentials(HTTP_X_WORKSPACE_ID=str(workspace.id))
    return api


def _create(client, environment, project, ids=(SESSION, MISSING), **body):
    return client.post(
        f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
        {
            "project_id": str(project.id),
            "source_kind": "session",
            "ids": list(ids),
            **body,
        },
        format="json",
    )


def _start(client, environment, project, found):
    first, second = _observe(found)
    with first, second:
        return _create(client, environment, project)


class TestCreate:
    def test_freezes_the_selection_and_returns_the_bundle(
        self, client, environment, project
    ):
        response = _start(client, environment, project, {SESSION: _evidence()})

        assert response.status_code == status.HTTP_201_CREATED, response.data
        body = response.json()
        assert body["environment_id"] == str(environment.id)
        assert body["project_id"] == str(project.id)
        assert body["source_kind"] == "session"
        found, missing = body["cases"]
        assert (found["source_id"], found["status"]) == (SESSION, "pending")
        assert found["changed_since_import"] is False
        assert found["transcript"]["status"] == "present"
        assert [t["role"] for t in found["transcript"]["turns"]] == ["user", "agent"]
        assert found["tool_calls"]["status"] == "missing"
        assert "not traced" in found["tool_calls"]["reason"]
        assert found["starting_state"]["status"] == "missing"
        assert found["labels"]["status"] == "missing"
        assert (missing["source_id"], missing["status"]) == (MISSING, "unavailable")
        assert missing["reason"] == SOURCE_NOT_FOUND

        stored = EvidenceImport.no_workspace_objects.get(id=body["id"])
        assert stored.workspace_id == environment.workspace_id
        assert stored.created_by_id is not None
        assert list(stored.cases.values_list("source_id", "status")) == [
            (SESSION, EvidenceImportCase.Status.PENDING),
            (MISSING, EvidenceImportCase.Status.UNAVAILABLE),
        ]
        assert len(stored.cases.get(source_id=SESSION).digest) == 64

    def test_redaction_previews_what_would_be_replaced(
        self, client, environment, project
    ):
        response = _start(client, environment, project, {SESSION: _evidence()})

        redaction = response.json()["redaction"]
        assert redaction["status"] == "ready", redaction
        email = next(f for f in redaction["findings"] if f["entity"] == "EMAIL_ADDRESS")
        assert email["value"] == "jane.doe@example.com"
        assert email["replacement"] == "<EMAIL_ADDRESS>"
        assert (email["part"], email["index"], email["field"]) == (
            "transcript",
            0,
            "text",
        )
        text = response.json()["cases"][0]["transcript"]["turns"][0]["text"]
        assert text[email["start"] : email["end"]] == email["value"]

    def test_redaction_fails_closed_when_nothing_can_look(
        self, client, environment, project
    ):
        with patch("simulate.selectors.evidence_import.find_pii", return_value=None):
            response = _start(client, environment, project, {SESSION: _evidence()})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["redaction"] == {
            "status": "unavailable",
            "reason": REDACTION_UNAVAILABLE,
            "findings": [],
            "total": 0,
        }

    def test_a_missing_transcript_and_unreadable_tool_calls_say_so(
        self, client, environment, project
    ):
        found = {SESSION: _evidence(turns=(), tool_calls=None)}

        case = _start(client, environment, project, found).json()["cases"][0]

        assert case["transcript"] == {
            "status": "missing",
            "reason": "No conversation text was recorded.",
            "turns": [],
        }
        assert case["tool_calls"]["status"] == "missing"
        assert case["tool_calls"]["reason"] == "The tool calls could not be read."
        assert case["tool_io"]["status"] == "missing"

    def test_tool_calls_and_their_inputs_and_outputs(
        self, client, environment, project
    ):
        def call(name, **fields):
            return ToolCall(
                span_id=str(uuid.uuid4()),
                trace_id="t",
                name=name,
                at=T0,
                failed=False,
                error="",
                input=fields.get("input", '{"order": 42}'),
                output=fields.get("output", ""),
                input_truncated=False,
                output_truncated=False,
            )

        found = {
            SESSION: _evidence(
                tool_calls=(call("lookup_order", output="refunded"), call("refund"))
            )
        }

        case = _start(client, environment, project, found).json()["cases"][0]

        assert case["tool_calls"]["status"] == "present"
        assert [c["name"] for c in case["tool_calls"]["calls"]] == [
            "lookup_order",
            "refund",
        ]
        assert case["tool_io"]["status"] == "present"
        assert case["tool_calls"]["calls"][1]["output"] == ""


class TestReread:
    def test_a_source_deleted_since_reads_as_unavailable(
        self, client, environment, project
    ):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()

        first, second = _observe({})
        with first, second:
            response = client.get(
                f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        case = response.json()["cases"][0]
        assert (case["status"], case["reason"]) == ("unavailable", SOURCE_UNAVAILABLE)
        assert case["transcript"]["status"] == "missing"

    def test_a_changed_source_says_so(self, client, environment, project):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()
        changed = _evidence(turns=(Turn("user", "Different now", False, "t", T0),))

        first, second = _observe({SESSION: changed})
        with first, second:
            response = client.get(
                f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
            )

        assert response.json()["cases"][0]["changed_since_import"] is True

    def test_another_environments_import_is_not_found(
        self, client, environment, project, user, workspace
    ):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()
        other, _ = create_hosted_job(
            user.organization, _payload(), idempotency_key="other", workspace=workspace
        )

        response = client.get(
            f"{ENVIRONMENTS}/{other.id}/evidence-imports/{made['id']}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestRefusals:
    def test_an_environment_of_another_organization_is_not_found(
        self, client, environment, project
    ):
        environment.organization = Organization.objects.create(name="Other")
        environment.save(update_fields=["organization"])

        response = _start(client, environment, project, {})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not EvidenceImport.no_workspace_objects.exists()

    def test_a_project_of_another_organization_is_not_found(
        self, client, environment, project
    ):
        Project.objects.filter(id=project.id).update(
            organization=Organization.objects.create(name="Other")
        )

        response = _start(client, environment, project, {})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Project not found" in str(response.data)

    def test_a_project_in_another_workspace_is_refused(
        self, client, environment, project, organization, user
    ):
        """The caller can read both, but the environment's workspace cannot,
        so production data from there never lands here."""
        elsewhere = Workspace.objects.create(
            name="Elsewhere", organization=organization, created_by=user
        )
        Project.objects.filter(id=project.id).update(workspace=elsewhere)

        response = _start(client, environment, project, {})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not EvidenceImport.no_workspace_objects.exists()

    @pytest.mark.parametrize(
        "ids",
        [
            [SESSION, SESSION],
            [str(uuid.uuid4()) for _ in range(MAX_SOURCES + 1)],
            [],
            ["not-a-uuid"],
        ],
        ids=["duplicate", "too-many", "none", "not-an-id"],
    )
    def test_a_bad_selection_is_refused(self, client, environment, project, ids):
        response = _create(client, environment, project, ids=ids)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unknown_source_kind_is_refused(self, client, environment, project):
        response = _create(client, environment, project, source_kind="trace")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_viewer_cannot_make_one(
        self, organization, workspace, environment, project
    ):
        viewer = _viewer_client(organization, workspace)

        response = _start(viewer, environment, project, {SESSION: _evidence()})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert WORKSPACE_WRITE_DENIED in str(response.data)
        assert not EvidenceImport.no_workspace_objects.exists()

    def test_a_member_can(self, organization, workspace, environment, project):
        member = _member_client(organization, workspace)

        response = _start(member, environment, project, {SESSION: _evidence()})

        assert response.status_code == status.HTTP_201_CREATED, response.data


def test_an_environment_only_organization_imports_through_its_environment(
    client, environment, project, organization
):
    """EV-2: an organization restricted to Environment resources (the legacy
    Replay is hidden from it) makes the import through the environment."""
    cutoff = organization.created_at - timedelta(days=1)
    with override_settings(RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF=cutoff):
        assert restrict_simulate_to_environments(organization) is True
        response = _start(client, environment, project, {SESSION: _evidence()})

    assert response.status_code == status.HTTP_201_CREATED, response.data
