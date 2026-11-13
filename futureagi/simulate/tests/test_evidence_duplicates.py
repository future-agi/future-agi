"""One case, however many sources hold it: a source imported again, or the same
conversation imported from two sources, is proposed once, and the case it repeats
keeps every source as provenance. Over HTTP, for Observe sessions, transcripts and
dataset rows."""

from __future__ import annotations

import uuid

import pytest
from rest_framework import status

from simulate.models import EvidenceImportCase
from simulate.services.evidence_import import (
    TranscriptInput,
    create_transcript_import,
    discard_evidence_import,
)

from .test_evidence_datasets import CHAT, EXPECTED, _column, _mapping, _row
from .test_evidence_datasets import _create as _create_rows
from .test_evidence_datasets import _read as _read_rows
from .test_evidence_datasets import columns as columns
from .test_evidence_datasets import dataset as dataset
from .test_evidence_import_api import ENVIRONMENTS, SESSION, _evidence, _observe
from .test_evidence_import_api import _create as _create_sessions
from .test_evidence_import_api import client as client
from .test_evidence_import_api import environment as environment
from .test_evidence_import_api import project as project
from .test_evidence_import_list import _list
from .test_evidence_transcripts import _post as _post_transcripts
from .test_evidence_transcripts import _transcript

pytestmark = pytest.mark.django_db

OTHER = str(uuid.uuid4())


def _sessions(client, environment, project, found, ids):
    with _observe(found):
        response = _create_sessions(client, environment, project, ids=ids)
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.json()


def _review(client, environment, made, found=None):
    with _observe(found or {}):
        response = client.get(
            f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made['id']}/"
        )
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.json()


def _stored(made):
    return list(
        EvidenceImportCase.objects.filter(evidence_import_id=made["id"]).order_by(
            "position"
        )
    )


class TestObserve:
    def test_a_session_imported_again_is_no_second_case_and_counts_as_a_source(
        self, client, environment, project
    ):
        found = {SESSION: _evidence()}
        first = _sessions(client, environment, project, found, [SESSION])
        again = _sessions(client, environment, project, found, [SESSION])

        [case] = again["cases"]
        assert case["status"] == "duplicate"
        assert case["duplicate_of"]["import_id"] == first["id"]
        assert case["duplicate_of"]["case_id"] == first["cases"][0]["id"]
        # Nothing of it is left to draft: only the first import is listed.
        assert [one["id"] for one in _list(client, environment)] == [first["id"]]
        [original] = _review(client, environment, first, found)["cases"]
        assert (original["status"], original["also_from"]) == ("pending", 1)

    def test_two_sessions_holding_the_same_conversation_are_one_case(
        self, client, environment, project
    ):
        # Different ids, traces and times; the same turns and tool calls.
        found = {SESSION: _evidence(), OTHER: _evidence(OTHER)}
        made = _sessions(client, environment, project, found, [SESSION, OTHER])

        [case] = made["cases"]
        assert (case["source_id"], case["also_from"]) == (SESSION, 1)
        kept, repeated = _stored(made)
        assert (repeated.status, repeated.duplicate_of_id) == ("duplicate", kept.id)

    def test_a_different_conversation_is_its_own_case(
        self, client, environment, project
    ):
        found = {
            SESSION: _evidence(),
            OTHER: _evidence(OTHER, turns=_evidence().turns[:1]),
        }
        made = _sessions(client, environment, project, found, [SESSION, OTHER])

        assert [one["status"] for one in made["cases"]] == ["pending", "pending"]
        assert [one["also_from"] for one in made["cases"]] == [0, 0]

    def test_a_blocked_case_is_retried_by_importing_it_again(
        self, client, environment, project
    ):
        found = {SESSION: _evidence()}
        first = _sessions(client, environment, project, found, [SESSION])
        EvidenceImportCase.objects.filter(evidence_import_id=first["id"]).update(
            status=EvidenceImportCase.Status.BLOCKED
        )

        again = _sessions(client, environment, project, found, [SESSION])

        [case] = again["cases"]
        assert (case["status"], case["duplicate_of"]) == ("pending", None)

    def test_a_source_nothing_was_found_for_is_never_a_duplicate(
        self, client, environment, project
    ):
        missing = str(uuid.uuid4())
        _sessions(client, environment, project, {}, [missing])
        again = _sessions(client, environment, project, {}, [missing])

        assert [one["status"] for one in again["cases"]] == ["unavailable"]


class TestTranscripts:
    def test_the_same_transcript_twice_in_one_import_is_one_case(
        self, client, environment
    ):
        response = _post_transcripts(
            client,
            environment,
            _transcript(name="Monday"),
            _transcript(name="Monday again"),
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        [case] = response.json()["cases"]
        assert (case["name"], case["also_from"]) == ("Monday", 1)

    def test_a_transcript_added_again_later_is_already_imported(
        self, client, environment
    ):
        first = _post_transcripts(client, environment, _transcript()).json()
        again = _post_transcripts(
            client, environment, _transcript(name="Same chat, new name")
        ).json()

        [case] = again["cases"]
        assert case["status"] == "duplicate"
        assert case["duplicate_of"]["import_id"] == first["id"]


class TestDataset:
    def test_a_row_imported_again_is_already_imported(
        self, client, environment, dataset, columns
    ):
        row = _row(dataset, 0, {columns["chat"]: CHAT, columns["expected"]: EXPECTED})
        first = _create_rows(client, environment, dataset, [row], columns).json()
        again = _create_rows(client, environment, dataset, [row], columns).json()

        [case] = again["cases"]
        assert case["status"] == "duplicate"
        assert case["duplicate_of"]["case_id"] == first["cases"][0]["id"]
        [original] = _read_rows(client, environment, first)["cases"]
        assert original["also_from"] == 1

    def test_two_rows_holding_the_same_conversation_are_one_case(
        self, client, environment, dataset, columns
    ):
        same = {columns["chat"]: CHAT, columns["expected"]: EXPECTED}
        rows = [_row(dataset, 0, same), _row(dataset, 1, same)]

        made = _create_rows(client, environment, dataset, rows, columns).json()

        [case] = made["cases"]
        assert case["also_from"] == 1

    def test_the_same_conversation_with_another_expected_outcome_is_another_case(
        self, client, environment, dataset, columns
    ):
        rows = [
            _row(dataset, 0, {columns["chat"]: CHAT, columns["expected"]: EXPECTED}),
            _row(dataset, 1, {columns["chat"]: CHAT, columns["expected"]: "Refuse."}),
        ]

        made = _create_rows(
            client,
            environment,
            dataset,
            rows,
            columns,
            mapping=_mapping(columns, labels=[]),
        ).json()

        assert [one["status"] for one in made["cases"]] == ["pending", "pending"]


class TestDiscard:
    def test_discarding_the_case_it_repeats_makes_the_duplicate_the_case(
        self, environment, user
    ):
        chat = "User: Where is my parcel?\nAgent: It left the depot today."
        first = create_transcript_import(
            environment=environment,
            transcripts=[TranscriptInput("first", "text", chat)],
            production=True,
            user=user,
        )
        second = create_transcript_import(
            environment=environment,
            transcripts=[TranscriptInput("second", "text", chat)],
            production=True,
            user=user,
        )
        third = create_transcript_import(
            environment=environment,
            transcripts=[TranscriptInput("third", "text", chat)],
            production=True,
            user=user,
        )
        [promoted] = second.cases.all()
        [still] = third.cases.all()
        assert (promoted.status, still.status) == ("duplicate", "duplicate")

        assert discard_evidence_import(first) is True

        promoted.refresh_from_db()
        still.refresh_from_db()
        assert (promoted.status, promoted.duplicate_of_id) == ("pending", None)
        assert (still.status, still.duplicate_of_id) == ("duplicate", promoted.id)


def test_another_label_column_does_not_split_a_case(
    client, environment, dataset, columns
):
    """Labels are context for the builder, not the conversation: the same chat
    under two intents is one case."""
    intent = _column(dataset, "intent")
    rows = [
        _row(dataset, 0, {columns["chat"]: CHAT, intent: "refund"}),
        _row(dataset, 1, {columns["chat"]: CHAT, intent: "complaint"}),
    ]

    made = _create_rows(
        client,
        environment,
        dataset,
        rows,
        columns,
        mapping=_mapping(columns, labels=[str(intent.id)], expected=None),
    ).json()

    assert [one["also_from"] for one in made["cases"]] == [1]
