"""Dataset imports over HTTP: a person picks dataset rows and says which column
is the conversation, which are labels and which is the expected outcome. The
rows are read live on every view, as Observe's sources are: nothing of them is
stored, an edited row reads as changed and a deleted one as unavailable."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status

from accounts.models import Organization, Workspace
from accounts.tests.test_rbac_regressions import WORKSPACE_WRITE_DENIED, _viewer_client
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.evidence_import import (
    CUT_TO_FIT,
    MAX_IMPORT_CHARS,
    ROW_EXPECTED_EMPTY,
    ROW_NO_EXPECTED,
    ROW_NO_LABELS,
    ROW_NO_TOOLS,
    ROW_NOT_FOUND,
    ROW_STATE_MISSING,
    ROW_UNAVAILABLE,
    evidence_document,
)
from simulate.utils.transcript_parse import MAX_TRANSCRIPT_CHARS
from tracer.selectors.observe_evidence import TURN_CHARS

from .test_evidence_import_api import ENVIRONMENTS
from .test_evidence_import_api import client as client
from .test_evidence_import_api import environment as environment
from .test_evidence_import_api import project as project

pytestmark = pytest.mark.django_db

EMAIL = "jane.doe@example.com"
CHAT = f"User: Refund my order, I'm {EMAIL}\nAgent: I've issued a second refund."
MESSAGES = json.dumps(
    [
        {"role": "system", "content": "You are a support agent."},
        {"role": "user", "content": "Where is my parcel?"},
        {"role": "assistant", "content": "It left the depot today."},
    ]
)
EXPECTED = "One refund only; the second is refused."


def _column(dataset, name, data_type=DataTypeChoices.TEXT.value):
    return Column.objects.create(
        name=name,
        data_type=data_type,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )


def _row(dataset, order, cells):
    row = Row.objects.create(dataset=dataset, order=order)
    for column, value in cells.items():
        Cell.objects.create(dataset=dataset, column=column, row=row, value=value)
    return row


@pytest.fixture
def dataset(db, organization, workspace, user):
    return Dataset.objects.create(
        name="Support evals", organization=organization, workspace=workspace, user=user
    )


@pytest.fixture
def columns(dataset):
    return {
        "chat": _column(dataset, "chat"),
        "intent": _column(dataset, "intent"),
        "resolved": _column(dataset, "resolved", DataTypeChoices.BOOLEAN.value),
        "expected": _column(dataset, "expected"),
        "audio": _column(dataset, "recording", DataTypeChoices.AUDIO.value),
    }


@pytest.fixture
def rows(dataset, columns):
    return [
        _row(
            dataset,
            0,
            {
                columns["chat"]: CHAT,
                columns["intent"]: "refund",
                columns["resolved"]: "false",
                columns["expected"]: EXPECTED,
            },
        ),
        _row(
            dataset,
            1,
            {columns["chat"]: MESSAGES, columns["intent"]: "", columns["expected"]: ""},
        ),
    ]


def _mapping(columns, **overrides):
    return {
        "conversation": str(columns["chat"].id),
        "labels": [str(columns["intent"].id), str(columns["resolved"].id)],
        "expected": str(columns["expected"].id),
        **overrides,
    }


def _post(client, environment, dataset, ids, columns, mapping=None, **body):
    return client.post(
        f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
        {
            "source_kind": "dataset",
            "dataset_id": str(dataset.id),
            "ids": [str(one) for one in ids],
            "columns": mapping or _mapping(columns),
            **body,
        },
        format="json",
    )


def _create(client, environment, dataset, rows, columns, mapping=None, **body):
    return _post(
        client, environment, dataset, [row.id for row in rows], columns, mapping, **body
    )


def _read(client, environment, made):
    return client.get(
        f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
    ).json()


class TestCreate:
    def test_picked_rows_become_cases_read_through_the_columns(
        self, client, environment, dataset, rows, columns
    ):
        response = _create(client, environment, dataset, rows, columns)

        assert response.status_code == status.HTTP_201_CREATED, response.data
        body = response.json()
        assert (body["source_kind"], body["project_id"]) == ("dataset", None)
        assert body["dataset_id"] == str(dataset.id)
        assert (body["simulated"], body["production"]) == (False, False)
        first, second = body["cases"]
        assert [first["source_id"], second["source_id"]] == [
            str(rows[0].id),
            str(rows[1].id),
        ]
        # A row is named by its dataset, not its id.
        assert [first["name"], second["name"]] == [
            "Row from Support evals",
            "Row from Support evals",
        ]
        assert (first["status"], first["source_available"]) == ("pending", True)
        assert first["changed_since_import"] is False
        assert first["transcript"]["status"] == "present"
        assert [(t["role"], t["text"]) for t in first["transcript"]["turns"]] == [
            ("user", f"Refund my order, I'm {EMAIL}"),
            ("agent", "I've issued a second refund."),
        ]
        assert [
            (label["name"], label["kind"], label["value"], label["target"])
            for label in first["labels"]["labels"]
        ] == [
            ("intent", "text", "refund", "row"),
            ("resolved", "boolean", "false", "row"),
        ]
        assert first["expected_outcome"] == {
            "status": "present",
            "reason": "",
            "text": EXPECTED,
        }
        assert first["tool_calls"] == {
            "status": "missing",
            "reason": ROW_NO_TOOLS,
            "calls": [],
        }
        assert first["starting_state"]["reason"] == ROW_STATE_MISSING
        # Redaction reads the live rows, as it reads Observe: it lists what it
        # would replace before any of it becomes scenario text.
        [finding] = body["redaction"]["findings"]
        assert (finding["case_id"], finding["part"]) == (first["id"], "transcript")
        assert finding["replacement"] == "<EMAIL_ADDRESS>"

    def test_a_json_conversation_is_read_as_messages(
        self, client, environment, dataset, rows, columns
    ):
        body = _create(client, environment, dataset, rows[1:], columns).json()

        [case] = body["cases"]
        assert [(t["role"], t["text"]) for t in case["transcript"]["turns"]] == [
            ("user", "Where is my parcel?"),
            ("agent", "It left the depot today."),
        ]
        assert case["labels"] == {
            "status": "missing",
            "reason": ROW_NO_LABELS,
            "labels": [],
        }
        assert case["expected_outcome"]["reason"] == ROW_EXPECTED_EMPTY

    def test_nothing_of_the_rows_is_stored(
        self, client, environment, dataset, rows, columns
    ):
        _create(client, environment, dataset, rows, columns)

        stored = EvidenceImport.no_workspace_objects.get()
        assert (stored.dataset_id, stored.project_id) == (dataset.id, None)
        assert stored.dataset_columns == _mapping(columns)
        assert stored.redaction is None
        cases = list(stored.cases.all())
        assert [case.transcript for case in cases] == [None, None]
        assert all(len(case.digest) == 64 for case in cases)

    def test_production_is_what_the_person_says(
        self, client, environment, dataset, rows, columns
    ):
        body = _create(
            client, environment, dataset, rows, columns, production=True
        ).json()

        assert (body["simulated"], body["production"]) == (False, True)
        assert EvidenceImport.no_workspace_objects.get().declared_production is True

    def test_without_an_expected_column_none_is_read(
        self, client, environment, dataset, rows, columns
    ):
        body = _create(
            client,
            environment,
            dataset,
            rows[:1],
            columns,
            mapping=_mapping(columns, expected=None, labels=[]),
        ).json()

        [case] = body["cases"]
        assert case["expected_outcome"] == {
            "status": "missing",
            "reason": ROW_NO_EXPECTED,
            "text": "",
        }
        stored = EvidenceImport.no_workspace_objects.get()
        assert stored.dataset_columns["labels"] == []
        assert stored.dataset_columns["expected"] is None

    def test_the_expected_outcome_is_redacted_like_the_conversation(
        self, client, environment, dataset, rows, columns
    ):
        Cell.objects.filter(row=rows[0], column=columns["expected"]).update(
            value=f"Mail {EMAIL} that the second refund is refused."
        )

        made = _create(client, environment, dataset, rows[:1], columns).json()

        parts = {
            (one["part"], one["replacement"]) for one in made["redaction"]["findings"]
        }
        assert parts == {
            ("transcript", "<EMAIL_ADDRESS>"),
            ("expected_outcome", "<EMAIL_ADDRESS>"),
        }
        stored = EvidenceImport.no_workspace_objects.get(id=made["id"])
        text = evidence_document(stored, [made["cases"][0]["id"]])
        assert "[expected outcome]: Mail <EMAIL_ADDRESS> that the second" in text
        assert EMAIL not in text


class TestLive:
    def test_an_edited_row_reads_as_changed(
        self, client, environment, dataset, rows, columns
    ):
        made = _create(client, environment, dataset, rows, columns).json()
        Cell.objects.filter(row=rows[0], column=columns["expected"]).update(
            value="Refuse the second refund."
        )

        first, second = _read(client, environment, made)["cases"]

        assert first["changed_since_import"] is True
        assert first["expected_outcome"]["text"] == "Refuse the second refund."
        assert second["changed_since_import"] is False

    def test_a_long_row_and_its_repeat_read_unchanged_on_reload(
        self, client, environment, dataset, columns
    ):
        # Made, the two rows share the budget; reloaded, only the case proposed is
        # read. Its share must stay the one it was fingerprinted under.
        chat = "\n".join(
            f"{'User' if n % 2 == 0 else 'Agent'}: turn {n} " + "word " * 200
            for n in range(40)
        )
        same = {columns["chat"]: chat}
        rows = [_row(dataset, 0, same), _row(dataset, 1, same)]
        mapping = _mapping(columns, labels=[], expected=None)

        made = _create(client, environment, dataset, rows, columns, mapping).json()

        [case] = _read(client, environment, made)["cases"]
        assert case["also_from"] == 1
        assert case["changed_since_import"] is False

    def test_a_deleted_row_reads_unavailable(
        self, client, environment, dataset, rows, columns
    ):
        made = _create(client, environment, dataset, rows, columns).json()
        rows[0].delete()

        first, second = _read(client, environment, made)["cases"]

        assert (first["status"], first["reason"]) == ("unavailable", ROW_UNAVAILABLE)
        assert first["source_available"] is False
        # Still says where it came from.
        assert first["name"] == "Row from Support evals"
        assert first["expected_outcome"]["status"] == "missing"
        assert second["status"] == "pending"

    def test_a_deleted_dataset_reads_unavailable(
        self, client, environment, dataset, rows, columns
    ):
        made = _create(client, environment, dataset, rows, columns).json()
        Dataset.objects.filter(id=dataset.id).update(deleted=True)

        cases = _read(client, environment, made)["cases"]

        assert [case["status"] for case in cases] == ["unavailable", "unavailable"]
        assert {case["reason"] for case in cases} == {ROW_UNAVAILABLE}

    def test_a_row_not_in_the_dataset_is_frozen_unavailable(
        self, client, environment, dataset, rows, columns, organization, workspace
    ):
        elsewhere = Dataset.objects.create(
            name="Other", organization=organization, workspace=workspace
        )
        foreign = _row(elsewhere, 0, {_column(elsewhere, "chat"): CHAT})

        body = _post(
            client, environment, dataset, [rows[0].id, foreign.id], columns
        ).json()

        first, second = body["cases"]
        assert first["status"] == "pending"
        assert (second["status"], second["reason"]) == ("unavailable", ROW_NOT_FOUND)
        assert "Refund my order" not in json.dumps(second)

    def test_an_unreadable_conversation_waits_and_recovers_once_fixed(
        self, client, environment, dataset, rows, columns
    ):
        Cell.objects.filter(row=rows[0], column=columns["chat"]).update(
            value='[{"role": "user", "content": '
        )
        made = _create(client, environment, dataset, rows[:1], columns).json()

        [case] = made["cases"]
        assert (case["status"], case["transcript"]["status"]) == (
            "pending",
            "unreadable",
        )
        assert case["changed_since_import"] is None
        assert EvidenceImportCase.objects.get().digest == ""

        Cell.objects.filter(row=rows[0], column=columns["chat"]).update(value=CHAT)
        [case] = _read(client, environment, made)["cases"]

        assert case["transcript"]["status"] == "present"

    def test_a_deleted_label_column_drops_its_labels(
        self, client, environment, dataset, rows, columns
    ):
        made = _create(client, environment, dataset, rows[:1], columns).json()
        columns["intent"].delete()

        [case] = _read(client, environment, made)["cases"]

        assert [label["name"] for label in case["labels"]["labels"]] == ["resolved"]


class TestCuts:
    def test_rows_share_the_import_budget(self, client, environment, dataset, columns):
        long = "x " * 5000
        picked = [
            _row(
                dataset,
                n,
                {columns["chat"]: f"User: {long}", columns["expected"]: long},
            )
            for n in range(20)
        ]

        body = _create(client, environment, dataset, picked, columns).json()

        share = MAX_IMPORT_CHARS // 20
        for case in body["cases"]:
            size = sum(len(t["text"]) for t in case["transcript"]["turns"])
            size += len(case["expected_outcome"]["text"])
            assert size <= share
            assert case["expected_outcome"]["status"] == "truncated"
            assert case["expected_outcome"]["reason"] == CUT_TO_FIT


class TestBounded:
    """A view reads at most what it can use of each cell."""

    def test_a_conversation_longer_than_a_transcript_is_unreadable_unparsed(
        self, client, environment, dataset, rows, columns
    ):
        Cell.objects.filter(row=rows[0], column=columns["chat"]).update(
            value="User: " + "x" * MAX_TRANSCRIPT_CHARS
        )

        with patch(
            "simulate.selectors.dataset_evidence.parse_transcript",
            side_effect=AssertionError("an oversized cell was parsed"),
        ):
            made = _create(client, environment, dataset, rows[:1], columns).json()

        [case] = made["cases"]
        assert (case["status"], case["transcript"]["status"]) == (
            "pending",
            "unreadable",
        )

    def test_long_label_and_expected_cells_are_cut_to_a_turn(
        self, client, environment, dataset, rows, columns
    ):
        long = "y" * (TURN_CHARS * 3)
        for name in ("intent", "expected"):
            Cell.objects.filter(row=rows[0], column=columns[name]).update(value=long)

        made = _create(
            client,
            environment,
            dataset,
            rows[:1],
            columns,
            mapping=_mapping(columns, labels=[str(columns["intent"].id)]),
        ).json()

        [case] = made["cases"]
        [label] = case["labels"]["labels"]
        assert label["value"] == "y" * TURN_CHARS
        assert case["labels"]["status"] == "truncated"
        assert case["expected_outcome"]["text"] == "y" * TURN_CHARS
        assert case["expected_outcome"]["status"] == "truncated"


class TestRefused:
    def test_a_dataset_of_another_organization_is_not_found(
        self, client, environment, dataset, rows, columns
    ):
        Dataset.objects.filter(id=dataset.id).update(
            organization=Organization.objects.create(name="Other")
        )

        response = _create(client, environment, dataset, rows, columns)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Dataset not found" in str(response.data)

    def test_a_dataset_in_another_workspace_is_refused(
        self, client, environment, dataset, rows, columns, organization, user
    ):
        elsewhere = Workspace.objects.create(
            name="Elsewhere", organization=organization, created_by=user
        )
        Dataset.objects.filter(id=dataset.id).update(workspace=elsewhere)

        response = _create(client, environment, dataset, rows, columns)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not EvidenceImport.no_workspace_objects.exists()

    def test_a_column_of_another_dataset_is_refused(
        self, client, environment, dataset, rows, columns, organization, workspace
    ):
        elsewhere = Dataset.objects.create(
            name="Other", organization=organization, workspace=workspace
        )

        response = _create(
            client,
            environment,
            dataset,
            rows,
            columns,
            mapping=_mapping(columns, expected=str(_column(elsewhere, "x").id)),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "dataset_column_not_found"
        # No column id: the person picks the columns again.
        assert response.json()["message"] == (
            "A picked column is no longer in this dataset; pick the columns again"
        )
        assert not EvidenceImport.no_workspace_objects.exists()

    def test_an_audio_conversation_is_refused_as_out_of_scope(
        self, client, environment, dataset, rows, columns
    ):
        response = _create(
            client,
            environment,
            dataset,
            rows,
            columns,
            mapping=_mapping(columns, conversation=str(columns["audio"].id)),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["error"] == "dataset_column_media"
        assert "only audio" in body["message"]

    def test_a_column_picked_twice_is_refused(
        self, client, environment, dataset, rows, columns
    ):
        response = _create(
            client,
            environment,
            dataset,
            rows,
            columns,
            mapping=_mapping(columns, expected=str(columns["chat"].id)),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Each column may be picked once." in str(response.data)

    @pytest.mark.parametrize("missing", ["dataset_id", "ids", "columns"])
    def test_a_dataset_import_needs_its_fields(
        self, client, environment, dataset, rows, columns, missing
    ):
        body = {
            "source_kind": "dataset",
            "dataset_id": str(dataset.id),
            "ids": [str(rows[0].id)],
            "columns": _mapping(columns),
        }
        del body[missing]

        response = client.post(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/", body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "A dataset import needs it." in str(response.data)

    def test_a_dataset_import_takes_no_project(
        self, client, environment, dataset, rows, columns, project
    ):
        response = _create(
            client, environment, dataset, rows, columns, project_id=str(project.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "A dataset import does not take it." in str(response.data)

    def test_a_session_import_takes_no_dataset_fields(
        self, client, environment, project, dataset, columns
    ):
        response = client.post(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/",
            {
                "source_kind": "session",
                "project_id": str(project.id),
                "ids": [str(uuid.uuid4())],
                "dataset_id": str(dataset.id),
                "columns": _mapping(columns),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "dataset_id" in str(response.data) and "columns" in str(response.data)

    def test_a_viewer_cannot_make_one(
        self, organization, workspace, environment, dataset, rows, columns
    ):
        viewer = _viewer_client(organization, workspace)

        response = _create(viewer, environment, dataset, rows, columns)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert WORKSPACE_WRITE_DENIED in str(response.data)


class TestStored:
    @pytest.mark.parametrize(
        "kind, with_project, with_dataset",
        [
            ("dataset", False, False),
            ("dataset", True, True),
            ("transcript", False, True),
            ("session", True, True),
        ],
    )
    def test_the_source_follows_the_kind(
        self, environment, project, dataset, kind, with_project, with_dataset
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            EvidenceImport.objects.create(
                organization_id=environment.organization_id,
                workspace_id=environment.workspace_id,
                environment=environment,
                project=project if with_project else None,
                dataset=dataset if with_dataset else None,
                source_kind=kind,
            )

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
    def test_the_builders_document_reads_the_rows_redacted(
        self, client, environment, dataset, rows, columns, production, says
    ):
        made = _create(
            client, environment, dataset, rows, columns, production=production
        ).json()
        stored = EvidenceImport.no_workspace_objects.get(id=made["id"])

        text = evidence_document(stored, [made["cases"][0]["id"]])

        assert f"rows a person picked from dataset {dataset.id}" in text
        assert says in text
        assert "Observe project" not in text
        assert f"(dataset row {rows[0].id})" in text
        assert "[turn 1] user: Refund my order, I'm <EMAIL_ADDRESS>" in text
        assert "[label 1] intent on the row: refund" in text
        assert "-- expected outcome: present" in text
        assert f"[expected outcome]: {EXPECTED}" in text
        assert EMAIL not in text
