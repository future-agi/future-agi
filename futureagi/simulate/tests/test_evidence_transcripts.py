"""Transcript imports over HTTP: a person pastes or uploads transcripts, which
become an import of one case each. Only their redacted text is stored, cut to
the import's size, and the review reads it back without Observe."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status

from accounts.tests.test_rbac_regressions import WORKSPACE_WRITE_DENIED, _viewer_client
from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.evidence_import import (
    BASIC_REDACTION,
    MAX_IMPORT_CHARS,
    TRANSCRIPT_NO_LABELS,
    TRANSCRIPT_NO_TOOLS,
    TRANSCRIPT_STATE_MISSING,
    evidence_document,
)
from tracer.selectors.observe_evidence import MAX_TURNS, TURN_CHARS

from .test_evidence_import_api import ENVIRONMENTS, SESSION
from .test_evidence_import_api import client as client
from .test_evidence_import_api import environment as environment
from .test_evidence_import_api import project as project

pytestmark = pytest.mark.django_db

EMAIL = "jane.doe@example.com"
PASTED = f"User: Refund my order, I'm {EMAIL}\nAgent: I've issued a second refund."


def _transcript(text=PASTED, form="text", name="Support chat"):
    return {"name": name, "format": form, "text": text}


def _post(client, environment, *transcripts, **body):
    return client.post(
        f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
        {
            "source_kind": "transcript",
            "transcripts": list(transcripts) or [_transcript()],
            **body,
        },
        format="json",
    )


@pytest.fixture
def no_observe():
    """A transcript import never reads Observe."""
    with patch(
        "simulate.selectors.evidence_import.observe_evidence",
        side_effect=AssertionError("Observe was read"),
    ):
        yield


class TestCreate:
    def test_a_pasted_transcript_becomes_a_case_with_its_text_redacted(
        self, client, environment, no_observe
    ):
        response = _post(
            client,
            environment,
            _transcript(name=f"chat with {EMAIL}"),
            production=True,
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        body = response.json()
        assert (body["source_kind"], body["project_id"]) == ("transcript", None)
        assert (body["simulated"], body["production"]) == (False, True)
        [case] = body["cases"]
        assert (case["source_id"], case["status"]) == ("transcript-1", "pending")
        assert case["name"] == "chat with <EMAIL_ADDRESS>"
        assert case["source_available"] is True
        assert case["changed_since_import"] is None
        assert (case["started_at"], case["trace_ids"]) == (None, [])
        turns = case["transcript"]["turns"]
        assert case["transcript"]["status"] == "present"
        assert [(t["role"], t["trace_id"]) for t in turns] == [
            ("user", ""),
            ("agent", ""),
        ]
        assert turns[0]["text"] == "Refund my order, I'm <EMAIL_ADDRESS>"
        assert case["tool_calls"] == {
            "status": "missing",
            "reason": TRANSCRIPT_NO_TOOLS,
            "calls": [],
        }
        assert case["labels"]["reason"] == TRANSCRIPT_NO_LABELS
        assert case["starting_state"]["reason"] == TRANSCRIPT_STATE_MISSING
        redaction = body["redaction"]
        assert (redaction["method"], redaction["findings"]) == ("presidio", [])
        assert redaction["total"] == 2
        assert EMAIL not in json.dumps(body)

    def test_nothing_unredacted_is_stored(self, client, environment, no_observe):
        _post(client, environment, _transcript(name=f"chat with {EMAIL}"))

        stored = EvidenceImport.no_workspace_objects.get()
        assert stored.project_id is None
        assert stored.declared_production is False
        assert stored.redaction == {"method": "presidio", "reason": "", "total": 2}
        rows = [stored.redaction] + [
            case.transcript for case in EvidenceImportCase.objects.all()
        ]
        assert EMAIL not in json.dumps(rows)
        assert "jane" not in json.dumps(rows).lower()

    def test_the_review_reads_the_stored_text_back(
        self, client, environment, no_observe
    ):
        made = _post(client, environment).json()

        again = client.get(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
        )

        assert again.status_code == status.HTTP_200_OK
        assert again.json()["cases"] == made["cases"]
        assert again.json()["redaction"] == made["redaction"]

    def test_each_transcript_is_one_case_in_order(
        self, client, environment, no_observe
    ):
        csv = "role,text\nuser,Where is my parcel?\nassistant,On its way.\n"
        messages = json.dumps([{"role": "user", "content": "Cancel it"}])

        body = _post(
            client,
            environment,
            _transcript(),
            _transcript(csv, "csv", "calls.csv"),
            _transcript(messages, "json", "chat.json"),
        ).json()

        assert [(c["source_id"], c["name"]) for c in body["cases"]] == [
            ("transcript-1", "Support chat"),
            ("transcript-2", "calls.csv"),
            ("transcript-3", "chat.json"),
        ]
        assert body["cases"][1]["transcript"]["turns"][1]["text"] == "On its way."
        assert body["production"] is False

    def test_system_and_tool_messages_left_out_are_named(
        self, client, environment, no_observe
    ):
        messages = json.dumps(
            [
                {"role": "system", "content": "You are a refund agent."},
                {"role": "user", "content": "Refund order 42"},
                {"role": "tool", "content": "ok"},
                {"role": "assistant", "content": "Done."},
            ]
        )

        [case] = _post(client, environment, _transcript(messages, "json")).json()[
            "cases"
        ]

        assert case["transcript"]["status"] == "present"
        assert case["transcript"]["reason"] == (
            "2 system or tool messages left out: only what the user and the agent "
            "said is kept."
        )

    def test_without_presidio_the_basic_match_is_stored_and_said(
        self, client, environment, no_observe
    ):
        with patch("simulate.selectors.evidence_import.find_pii", return_value=None):
            body = _post(client, environment).json()

        assert body["redaction"]["method"] == "basic"
        assert body["redaction"]["reason"] == BASIC_REDACTION
        assert body["cases"][0]["transcript"]["turns"][0]["text"] == (
            "Refund my order, I'm <EMAIL_ADDRESS>"
        )
        stored = EvidenceImport.no_workspace_objects.get().redaction
        assert (stored["method"], stored["reason"]) == ("basic", BASIC_REDACTION)


class TestCuts:
    def test_a_cut_turn_ends_at_whitespace_so_no_value_is_left_in_two(
        self, client, environment, no_observe
    ):
        # The email straddles TURN_CHARS: a plain cut would keep "jane.do".
        lead = "word " * ((TURN_CHARS - 6) // 5)
        text = f"User: {lead}{EMAIL} and more\nAgent: ok"

        [case] = _post(client, environment, _transcript(text)).json()["cases"]

        first = case["transcript"]["turns"][0]
        assert first["truncated"] is True
        assert first["text"] == lead.rstrip()
        assert case["transcript"]["status"] == "truncated"
        stored = json.dumps(EvidenceImportCase.objects.get().transcript)
        assert "jane" not in stored.lower()

    def test_a_value_with_spaces_across_the_cut_is_redacted_before_it_is_cut(
        self, client, environment, no_observe
    ):
        # The card straddles TURN_CHARS at one of its spaces: cut first, "4111 1111"
        # would be left, too short to read as a card.
        lead = "word " * ((TURN_CHARS - 10) // 5)
        text = f"User: {lead}4111 1111 1111 1111 and more\nAgent: ok"

        [case] = _post(client, environment, _transcript(text)).json()["cases"]

        assert case["transcript"]["turns"][0]["truncated"] is True
        stored = json.dumps(EvidenceImportCase.objects.get().transcript)
        assert "4111" not in stored

    def test_transcripts_share_the_import_budget_and_keep_head_and_tail(
        self, client, environment, no_observe
    ):
        lines = "\n".join(
            f"{'User' if n % 2 else 'Agent'}: turn {n} " + "x " * 700
            for n in range(MAX_TURNS + 20)
        )

        body = _post(client, environment, *[_transcript(lines)] * 3).json()

        for case in body["cases"]:
            turns = case["transcript"]["turns"]
            assert sum(len(t["text"]) for t in turns) <= MAX_IMPORT_CHARS // 3
            assert turns[0]["text"].startswith("turn 0 ")
            assert turns[-1]["text"].startswith(f"turn {MAX_TURNS + 19} ")
            assert case["transcript"]["status"] == "truncated"


class TestRefused:
    def test_an_unreadable_transcript_names_itself_and_its_line(
        self, client, environment
    ):
        bad = "role,text\nuser,hi\nboss,no\n"

        response = _post(
            client, environment, _transcript(), _transcript(bad, "csv", "calls.csv")
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["error"] == "transcript_unreadable"
        assert body["message"] == (
            "Transcript 2 (calls.csv): Line 3: Unknown speaker 'boss': use User or "
            "Agent."
        )
        assert not EvidenceImport.no_workspace_objects.exists()

    @pytest.mark.parametrize(
        "body, field",
        [
            ({"project_id": "00000000-0000-0000-0000-000000000001"}, "project_id"),
            ({"ids": [SESSION]}, "ids"),
        ],
    )
    def test_a_transcript_import_takes_no_observe_fields(
        self, client, environment, body, field
    ):
        response = _post(client, environment, **body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in str(response.data)
        assert not EvidenceImport.no_workspace_objects.exists()

    def test_a_nul_is_refused_raw_and_dropped_once_parsed(
        self, client, environment, no_observe
    ):
        raw = _post(client, environment, _transcript("User: hi\x00\nAgent: ok"))
        escaped = _post(
            client,
            environment,
            _transcript(
                json.dumps([{"role": "user", "content": "hi\x00 there"}]), "json"
            ),
        )

        assert raw.status_code == status.HTTP_400_BAD_REQUEST
        assert escaped.status_code == status.HTTP_201_CREATED, escaped.data
        [case] = escaped.json()["cases"]
        assert case["transcript"]["turns"][0]["text"] == "hi there"

    def test_a_transcript_import_needs_transcripts(self, client, environment):
        response = client.post(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
            {"source_kind": "transcript"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "transcripts" in str(response.data)

    @pytest.mark.parametrize(
        "extra", [{"transcripts": [_transcript()]}, {"production": True}]
    )
    def test_a_session_import_takes_no_transcript_fields(
        self, client, environment, project, extra
    ):
        response = client.post(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
            {
                "project_id": str(project.id),
                "source_kind": "session",
                "ids": [SESSION],
                **extra,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert next(iter(extra)) in str(response.data)

    def test_a_session_import_still_needs_its_project_and_ids(
        self, client, environment
    ):
        response = client.post(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
            {"source_kind": "session"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "project_id" in str(response.data) and "ids" in str(response.data)

    def test_a_viewer_cannot_add_one(self, organization, workspace, environment):
        viewer = _viewer_client(organization, workspace)

        response = _post(viewer, environment)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert WORKSPACE_WRITE_DENIED in str(response.data)
        assert not EvidenceImport.no_workspace_objects.exists()


class TestStored:
    def test_the_project_follows_the_kind(self, environment, project):
        for kind, with_project in (("session", None), ("transcript", project)):
            with pytest.raises(IntegrityError), transaction.atomic():
                EvidenceImport.objects.create(
                    organization_id=environment.organization_id,
                    workspace_id=environment.workspace_id,
                    environment=environment,
                    project=with_project,
                    source_kind=kind,
                )

    def test_deleting_the_import_deletes_its_text(
        self, client, environment, no_observe
    ):
        _post(client, environment)
        stored = EvidenceImport.no_workspace_objects.get()

        stored.delete()

        stored = EvidenceImport.all_objects.get(id=stored.id)
        assert stored.deleted is True
        assert stored.redaction is None
        assert [c.transcript for c in EvidenceImportCase._base_manager.all()] == [None]

    def test_deleting_the_environment_deletes_its_transcripts(
        self, client, environment, no_observe
    ):
        _post(client, environment)

        with patch("simulate.temporal.client.cancel_hosted_harness_gateway_workflow"):
            deleted = client.delete(f"{ENVIRONMENTS}/{environment.id}/")

        assert deleted.status_code == status.HTTP_204_NO_CONTENT
        stored = EvidenceImport.all_objects.get()
        assert (stored.deleted, stored.redaction) == (True, None)
        assert [c.transcript for c in EvidenceImportCase._base_manager.all()] == [None]

    @pytest.mark.parametrize(
        "production, says",
        [
            (
                True,
                "# The person says they come from production: each case is "
                "regression evidence.",
            ),
            (
                False,
                "# The person did not say they come from production. Never write a "
                "case as something a customer did.",
            ),
        ],
    )
    def test_the_builders_document_says_whose_transcript_it_is(
        self, client, environment, no_observe, production, says
    ):
        made = _post(client, environment, production=production).json()
        stored = EvidenceImport.no_workspace_objects.get(id=made["id"])

        text = evidence_document(stored, [made["cases"][0]["id"]])

        assert "transcripts a person added" in text
        assert "never cite a trace or a call" in text
        assert says in text
        assert "Observe project" not in text
        assert "== case " in text and "(transcript transcript-1)" in text
        assert "[turn 1] user: Refund my order, I'm <EMAIL_ADDRESS>" in text
        assert "-- tool calls: missing" in text
        assert EMAIL not in text
