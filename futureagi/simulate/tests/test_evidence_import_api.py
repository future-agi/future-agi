"""Evidence imports over HTTP: freezing Observe sessions or calls for an
environment, the bundle the review reads (what each source holds and lacks,
cut to the import's size, and what redaction would replace), and who may make
one.

Observe itself is a stand-in here (``observe_evidence``); its reads are covered
in ``tracer/tests/test_observe_evidence.py``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
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
    BASIC_REDACTION,
    DOCUMENT_LINE_CHARS,
    MAX_IMPORT_CHARS,
    MAX_SOURCES,
    SOURCE_NOT_FOUND,
    SOURCE_UNAVAILABLE,
    evidence_digest,
    evidence_document,
    import_evidence,
)
from simulate.services.hosted_harness import create_hosted_job
from tracer.models.project import Project
from tracer.selectors.observe_evidence import (
    MAX_TOOL_CALLS,
    MAX_TURNS,
    TOOL_FIELD_CHARS,
    TURN_CHARS,
    Label,
    SourceEvidence,
    ToolCall,
    Turn,
)

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
        turns_omitted=0,
        transcript_unreadable=False,
        tool_calls=(),
        tool_calls_truncated=False,
        labels=(),
    )
    return replace(value, **overrides)


def _call(name, **fields) -> ToolCall:
    return ToolCall(
        span_id=str(uuid.uuid4()),
        trace_id="t",
        name=name,
        at=T0,
        failed=False,
        error=fields.get("error", ""),
        input=fields.get("input", '{"order": 42}'),
        output=fields.get("output", "refunded"),
        input_truncated=False,
        output_truncated=False,
    )


def _observe(found: dict):
    """Observe holds ``found`` for the import and every later read."""
    return patch(
        "simulate.selectors.evidence_import.observe_evidence", return_value=found
    )


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


def _start(client, environment, project, found, ids=(SESSION, MISSING)):
    with _observe(found):
        return _create(client, environment, project, ids=ids)


def _reread(client, environment, made, found):
    with _observe(found):
        return client.get(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
        )


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
        assert missing["changed_since_import"] is None

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
        assert (redaction["method"], redaction["reason"]) == ("presidio", "")
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

    def test_tool_errors_and_label_notes_are_checked_too(
        self, client, environment, project
    ):
        found = {
            SESSION: _evidence(
                turns=(),
                tool_calls=(_call("lookup", error="no customer bob@example.com"),),
                labels=(
                    Label(
                        "Resolved",
                        "text",
                        "call me on 415-555-0134",
                        "ask ann@example.com",
                        "trace",
                    ),
                ),
            )
        }

        redaction = _start(client, environment, project, found).json()["redaction"]

        places = {(f["part"], f["field"], f["value"]) for f in redaction["findings"]}
        assert ("tool_calls", "error", "bob@example.com") in places
        assert ("labels", "notes", "ann@example.com") in places
        assert any(part == "labels" and field == "value" for part, field, _ in places)

    def test_without_presidio_a_fixed_shape_match_stands_in(
        self, client, environment, project
    ):
        with patch("simulate.selectors.evidence_import.find_pii", return_value=None):
            response = _start(client, environment, project, {SESSION: _evidence()})

        assert response.status_code == status.HTTP_201_CREATED
        redaction = response.json()["redaction"]
        assert (redaction["method"], redaction["reason"]) == ("basic", BASIC_REDACTION)
        # The person reads this: what was and was not checked, never the library.
        assert redaction["reason"] == (
            "Names and addresses were not checked. Emails, phone numbers, card and "
            "ID numbers, IP addresses and keys were redacted."
        )
        assert [f["value"] for f in redaction["findings"]] == ["jane.doe@example.com"]

    def test_parts_that_could_not_be_read_say_so(self, client, environment, project):
        found = {
            SESSION: _evidence(turns=(), transcript_unreadable=True, tool_calls=None)
        }

        case = _start(client, environment, project, found).json()["cases"][0]

        assert case["transcript"]["status"] == "unreadable"
        assert case["tool_calls"] == {
            "status": "unreadable",
            "reason": "The tool calls could not be read.",
            "calls": [],
        }
        assert case["tool_inputs"]["status"] == "unreadable"
        assert case["tool_outputs"]["status"] == "unreadable"
        # Nobody can tell whether a part that could not be read has changed.
        assert case["changed_since_import"] is None
        assert EvidenceImportCase.objects.get(id=case["id"]).digest == ""

    def test_a_missing_transcript_is_not_an_unreadable_one(
        self, client, environment, project
    ):
        case = _start(
            client, environment, project, {SESSION: _evidence(turns=())}
        ).json()["cases"][0]

        assert case["transcript"] == {
            "status": "missing",
            "reason": "No conversation text was recorded.",
            "turns": [],
        }

    def test_tool_inputs_and_outputs_are_marked_separately(
        self, client, environment, project
    ):
        found = {
            SESSION: _evidence(
                tool_calls=(_call("lookup_order"), _call("refund", output=""))
            )
        }

        case = _start(client, environment, project, found).json()["cases"][0]

        assert case["tool_calls"]["status"] == "present"
        assert [c["name"] for c in case["tool_calls"]["calls"]] == [
            "lookup_order",
            "refund",
        ]
        assert case["tool_inputs"]["status"] == "present"
        assert case["tool_outputs"]["status"] == "partial"


class TestBudget:
    def test_an_over_budget_import_is_cut_and_says_so(
        self, client, environment, project
    ):
        trace = str(uuid.uuid4())
        long = tuple(
            Turn("user" if i % 2 else "agent", "word " * 400, False, trace, T0)
            for i in range(MAX_IMPORT_CHARS // 2000 + 10)
        )

        case = _start(
            client,
            environment,
            project,
            {SESSION: _evidence(turns=long)},
            ids=(SESSION,),
        ).json()["cases"][0]

        assert case["transcript"]["status"] == "truncated"
        kept = sum(len(t["text"]) for t in case["transcript"]["turns"])
        assert kept <= MAX_IMPORT_CHARS

    def test_a_late_failed_tool_call_survives_the_cut(self, project):
        """A failure often comes last; it is given room before the rest."""
        field = "z " * (TOOL_FIELD_CHARS // 2)
        calls = tuple(
            _call(f"step{i}", input=field, output=field) for i in range(40)
        ) + (_call("refund", input=field, output=field, error="already refunded"),)
        calls = calls[:-1] + (replace(calls[-1], failed=True),)

        with _observe({SESSION: _evidence(turns=(), tool_calls=calls)}):
            (kept,) = import_evidence(project, "session", [SESSION]).values()

        assert kept.tool_calls_truncated is True
        assert len(kept.tool_calls) < len(calls)
        assert kept.tool_calls[-1].name == "refund"
        assert kept.tool_calls[-1].failed is True
        assert kept.tool_calls[-1].error == "already refunded"

    def test_the_last_turn_survives_and_the_middle_is_counted(
        self, client, environment, project
    ):
        trace = str(uuid.uuid4())
        turns = tuple(
            Turn("user", f"turn {i} " + "word " * 300, False, trace, T0)
            for i in range(60)
        ) + (Turn("agent", "I issued the second refund.", False, trace, T0),)

        case = _start(
            client,
            environment,
            project,
            {SESSION: _evidence(turns=turns)},
            ids=(SESSION,),
        ).json()["cases"][0]

        shown = case["transcript"]["turns"]
        assert shown[0]["text"].startswith("turn 0 ")
        assert shown[-1]["text"] == "I issued the second refund."
        omitted = len(turns) - len(shown)
        assert omitted > 0
        assert case["transcript"]["status"] == "truncated"
        assert case["transcript"]["reason"].startswith(
            f"{omitted} middle turns left out"
        )

    def test_the_same_data_gives_the_same_digest(self, project):
        trace = str(uuid.uuid4())
        turns = tuple(Turn("user", "word " * 500, False, trace, T0) for _ in range(40))
        found = {SESSION: _evidence(turns=turns)}

        with _observe(found):
            first = import_evidence(project, "session", [SESSION])[SESSION]
        with _observe(found):
            second = import_evidence(project, "session", [SESSION])[SESSION]

        assert first.turns_truncated is True
        assert evidence_digest(first) == evidence_digest(second) != ""

    def test_the_worst_case_never_exceeds_the_budget(self, project):
        """Every source at every cap: what redaction reads stays in budget."""
        trace = str(uuid.uuid4())
        full = "x " * (TURN_CHARS // 2)
        field = "y " * (TOOL_FIELD_CHARS // 2)
        worst = _evidence(
            turns=tuple(Turn("user", full, False, trace, T0) for _ in range(MAX_TURNS)),
            tool_calls=tuple(
                _call("t", input=field, output=field, error="e" * 300)
                for _ in range(MAX_TOOL_CALLS)
            ),
            labels=tuple(
                Label("L", "text", "v" * 500, "n" * 500, "trace") for _ in range(50)
            ),
        )
        ids = [str(uuid.uuid4()) for _ in range(MAX_SOURCES)]

        with _observe(
            {source_id: replace(worst, source_id=source_id) for source_id in ids}
        ):
            evidence = import_evidence(project, "session", ids)

        read = sum(
            sum(len(t.text) for t in source.turns)
            + sum(
                len(c.input) + len(c.output) + len(c.error) for c in source.tool_calls
            )
            + sum(len(label.notes) + len(label.value) for label in source.labels)
            for source in evidence.values()
        )
        assert read <= MAX_IMPORT_CHARS
        assert all(
            source.turns_truncated
            and source.tool_calls_truncated
            and source.labels_truncated
            for source in evidence.values()
        )


def test_an_observe_sources_fingerprint_is_the_one_it_was_imported_with():
    """Dataset rows added an expected outcome to the evidence. An Observe source,
    which has none, keeps the fingerprint its import stored before that, so an
    unchanged session never reads as changed after the upgrade."""
    evidence = _evidence()
    before = {
        field: value
        for field, value in asdict(evidence).items()
        if field not in ("expected", "expected_truncated")
    }
    stored = hashlib.sha256(
        json.dumps(before, sort_keys=True, default=str).encode()
    ).hexdigest()

    assert evidence_digest(evidence) == stored
    # An expected outcome is part of what a row holds: changing it is a change.
    assert evidence_digest(replace(evidence, expected="Refuse.")) != stored


class TestReread:
    def test_a_source_deleted_since_reads_as_unavailable(
        self, client, environment, project
    ):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()

        response = _reread(client, environment, made, {})

        assert response.status_code == status.HTTP_200_OK, response.data
        case = response.json()["cases"][0]
        assert (case["status"], case["reason"]) == ("unavailable", SOURCE_UNAVAILABLE)
        assert case["transcript"]["status"] == "missing"
        assert case["changed_since_import"] is None

    def test_a_changed_source_says_so(self, client, environment, project):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()
        changed = _evidence(turns=(Turn("user", "Different now", False, "t", T0),))

        response = _reread(client, environment, made, {SESSION: changed})

        assert response.json()["cases"][0]["changed_since_import"] is True

    def test_a_source_the_import_found_nothing_for_stays_unavailable(
        self, client, environment, project
    ):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()

        response = _reread(
            client,
            environment,
            made,
            {SESSION: _evidence(), MISSING: _evidence(MISSING)},
        )

        missing = response.json()["cases"][1]
        assert (missing["status"], missing["reason"]) == (
            "unavailable",
            SOURCE_NOT_FOUND,
        )
        assert missing["transcript"]["turns"] == []

    def test_a_deleted_project_reads_every_source_unavailable(
        self, client, environment, project
    ):
        made = _start(client, environment, project, {SESSION: _evidence()}).json()
        Project.objects.filter(id=project.id).update(deleted=True)

        response = _reread(client, environment, made, {SESSION: _evidence()})

        assert [c["status"] for c in response.json()["cases"]] == ["unavailable"] * 2

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
    Replay is hidden from it) makes the import through the environment. Fails
    if any server path starts refusing such organizations."""
    cutoff = organization.created_at - timedelta(days=1)
    with override_settings(RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF=cutoff):
        assert restrict_simulate_to_environments(organization) is True
        response = _start(client, environment, project, {SESSION: _evidence()})

    assert response.status_code == status.HTTP_201_CREATED, response.data


class TestEvidenceDocument:
    """What a builder turn reads: redacted, one value per line, short lines."""

    def _import(self, client, environment, project, found, ids=(SESSION, MISSING)):
        made = _start(client, environment, project, found, ids=ids).json()
        return EvidenceImport.no_workspace_objects.get(id=made["id"]), made

    def test_every_value_is_redacted(self, client, environment, project):
        found = {
            SESSION: _evidence(
                tool_calls=(_call("lookup", error="no customer bob@example.com"),)
            )
        }
        evidence_import, made = self._import(client, environment, project, found)

        with _observe(found):
            text = evidence_document(evidence_import, [c["id"] for c in made["cases"]])

        assert "jane.doe@example.com" not in text
        assert "bob@example.com" not in text
        body = [line for line in text.splitlines() if not line.startswith("#")]
        assert sum(line.count("<EMAIL_ADDRESS>") for line in body) == 2
        assert "[call 1] lookup at " in text
        assert "starting state" in text.lower()

    def test_findings_past_the_listed_ones_are_redacted_too(
        self, client, environment, project, monkeypatch
    ):
        trace = str(uuid.uuid4())
        turns = tuple(
            Turn("user", f"mail me at person{i}@example.com", False, trace, T0)
            for i in range(3)
        )
        found = {SESSION: _evidence(turns=turns)}
        monkeypatch.setattr("simulate.selectors.evidence_import.MAX_FINDINGS", 1)
        evidence_import, made = self._import(client, environment, project, found)

        with _observe(found):
            text = evidence_document(evidence_import, [made["cases"][0]["id"]])

        assert "@example.com" not in text
        assert made["redaction"]["total"] == 3
        assert len(made["redaction"]["findings"]) == 1

    def test_long_values_wrap_and_lose_nothing(self, client, environment, project):
        output = "".join(f"row {i:04d} ok; " for i in range(400))
        found = {SESSION: _evidence(tool_calls=(_call("export", output=output),))}
        evidence_import, made = self._import(
            client, environment, project, found, ids=(SESSION,)
        )

        with _observe(found):
            text = evidence_document(evidence_import, [made["cases"][0]["id"]])

        lines = text.splitlines()
        assert max(len(line) for line in lines) <= DOCUMENT_LINE_CHARS
        pieces = [
            line.split(": ", 1)[1]
            for line in lines
            if line.startswith("[call 1] output")
        ]
        assert len(pieces) > 1
        assert "".join(pieces) == output

    def test_without_presidio_the_document_says_what_was_not_checked(
        self, client, environment, project
    ):
        found = {SESSION: _evidence()}
        evidence_import, made = self._import(client, environment, project, found)

        with (
            _observe(found),
            patch("simulate.selectors.evidence_import.find_pii", return_value=None),
        ):
            text = evidence_document(evidence_import, [made["cases"][0]["id"]])

        assert BASIC_REDACTION in text
        assert "jane.doe@example.com" not in text

    def test_only_the_asked_and_readable_cases_are_in_it(
        self, client, environment, project
    ):
        other = str(uuid.uuid4())
        # Another conversation: the same one would be proposed once, as one case.
        found = {
            SESSION: _evidence(),
            other: _evidence(other, turns=_evidence().turns[:1]),
        }
        evidence_import, made = self._import(
            client, environment, project, found, ids=(SESSION, other, MISSING)
        )
        asked, _, missing = made["cases"]

        with _observe(found):
            text = evidence_document(evidence_import, [asked["id"], missing["id"]])

        assert f"== case {asked['id']}" in text
        assert other not in text
        assert f"== case {missing['id']}" not in text
