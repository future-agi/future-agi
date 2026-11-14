"""One case, however many sources hold it: a source imported again, or the same
conversation imported from two sources, is proposed once, and the case it repeats
keeps every source as provenance. Over HTTP, for Observe sessions, transcripts and
dataset rows."""

from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from rest_framework import status

from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from simulate.selectors.evidence_import import RETRIED_LATER
from simulate.services import evidence_import as evidence_import_service
from simulate.services.evidence_import import (
    TranscriptInput,
    create_transcript_import,
    discard_evidence_import,
)
from tracer.selectors.observe_evidence import Turn

from .test_evidence_datasets import CHAT, EXPECTED, _column, _mapping, _row
from .test_evidence_datasets import _create as _create_rows
from .test_evidence_datasets import _read as _read_rows
from .test_evidence_datasets import columns as columns
from .test_evidence_datasets import dataset as dataset
from .test_evidence_import_api import ENVIRONMENTS, SESSION, T0, _evidence, _observe
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


BLOCKED_BECAUSE = "The draft changed the environment beyond new checks."


def _block(made):
    EvidenceImportCase.objects.filter(evidence_import_id=made["id"]).update(
        status=EvidenceImportCase.Status.BLOCKED, reason=BLOCKED_BECAUSE
    )


class TestRetry:
    """A blocked case is retried by importing its source again; the new case
    supersedes it, so one source shows one row."""

    def test_a_retry_supersedes_the_blocked_case(self, client, environment, project):
        found = {SESSION: _evidence()}
        first = _sessions(client, environment, project, found, [SESSION])
        _block(first)

        again = _sessions(client, environment, project, found, [SESSION])

        [case] = again["cases"]
        assert case["status"] == "pending"
        [old] = _stored(first)
        assert (old.status, old.reason) == ("discarded", RETRIED_LATER)
        # Only the retry is left to finish.
        assert [one["id"] for one in _list(client, environment)] == [again["id"]]
        # The old import's review says why, not that a person discarded it.
        [shown] = _review(client, environment, first, found)["cases"]
        assert (shown["status"], shown["reason"]) == ("discarded", RETRIED_LATER)

    def test_the_same_conversation_retries_a_blocked_transcript(
        self, client, environment
    ):
        first = _post_transcripts(client, environment, _transcript()).json()
        _block(first)

        again = _post_transcripts(
            client, environment, _transcript(name="Same chat, new name")
        ).json()

        assert [one["status"] for one in again["cases"]] == ["pending"]
        [old] = _stored(first)
        assert (old.status, old.reason) == ("discarded", RETRIED_LATER)

    def test_a_retry_that_fails_to_save_leaves_the_blocked_case(
        self, environment, user
    ):
        chat = "User: Where is my parcel?\nAgent: It left the depot today."
        first = create_transcript_import(
            environment=environment,
            transcripts=[TranscriptInput("first", "text", chat)],
            production=True,
            user=user,
        )
        first.cases.update(
            status=EvidenceImportCase.Status.BLOCKED, reason=BLOCKED_BECAUSE
        )

        with (
            patch.object(
                EvidenceImportCase.objects,
                "bulk_create",
                side_effect=IntegrityError("refused"),
            ),
            pytest.raises(IntegrityError),
        ):
            create_transcript_import(
                environment=environment,
                transcripts=[TranscriptInput("retry", "text", chat)],
                production=True,
                user=user,
            )

        [old] = first.cases.all()
        assert (old.status, old.reason) == ("blocked", BLOCKED_BECAUSE)
        assert (
            EvidenceImport.no_workspace_objects.filter(environment=environment).count()
            == 1
        )

    def test_a_retried_call_supersedes_its_blocked_case(
        self, client, environment, project
    ):
        found = {SESSION: _evidence()}

        def calls():
            with _observe(found):
                response = _create_sessions(
                    client, environment, project, ids=[SESSION], source_kind="call"
                )
            assert response.status_code == status.HTTP_201_CREATED, response.data
            return response.json()

        first = calls()
        _block(first)
        again = calls()

        assert [one["status"] for one in again["cases"]] == ["pending"]
        [old] = _stored(first)
        assert (old.status, old.reason) == ("discarded", RETRIED_LATER)

    def test_a_retried_dataset_row_supersedes_its_blocked_case(
        self, client, environment, dataset, columns
    ):
        row = _row(dataset, 0, {columns["chat"]: CHAT, columns["expected"]: EXPECTED})
        first = _create_rows(client, environment, dataset, [row], columns).json()
        _block(first)

        again = _create_rows(client, environment, dataset, [row], columns).json()

        assert [one["status"] for one in again["cases"]] == ["pending"]
        [old] = _stored(first)
        assert (old.status, old.reason) == ("discarded", RETRIED_LATER)

    def test_the_cases_that_repeated_a_blocked_case_repeat_its_retry(
        self, client, environment, project
    ):
        found = {SESSION: _evidence()}
        first = _sessions(client, environment, project, found, [SESSION])
        # Imported again while the first was still waiting: a duplicate of it.
        repeat = _sessions(client, environment, project, found, [SESSION])
        _block(first)

        retry = _sessions(client, environment, project, found, [SESSION])

        [kept] = _stored(retry)
        [repeated] = _stored(repeat)
        assert (repeated.status, repeated.duplicate_of_id) == ("duplicate", kept.id)
        [case] = _review(client, environment, retry, found)["cases"]
        assert (case["status"], case["also_from"]) == ("pending", 1)

    def test_a_transcript_of_the_same_conversation_retries_a_blocked_session(
        self, client, environment, project
    ):
        # One key for repeats and retries: the source where one exists, else the
        # conversation, whatever kind of source holds it.
        turns = (
            Turn("user", "Where is my parcel?", False, "t", T0),
            Turn("agent", "It left the depot today.", False, "t", T0),
        )
        found = {SESSION: _evidence(turns=turns)}
        first = _sessions(client, environment, project, found, [SESSION])
        _block(first)

        again = _post_transcripts(
            client,
            environment,
            _transcript(
                text="User: Where is my parcel?\nAgent: It left the depot today."
            ),
        ).json()

        assert [one["status"] for one in again["cases"]] == ["pending"]
        [old] = _stored(first)
        assert (old.status, old.reason) == ("discarded", RETRIED_LATER)

    def test_a_blocked_case_of_another_source_stays_blocked(
        self, client, environment, project
    ):
        found = {
            SESSION: _evidence(),
            OTHER: _evidence(OTHER, turns=_evidence().turns[:1]),
        }
        first = _sessions(client, environment, project, found, [SESSION])
        _block(first)

        _sessions(client, environment, project, found, [OTHER])

        [old] = _stored(first)
        assert (old.status, old.reason) == ("blocked", BLOCKED_BECAUSE)


class TestAcrossSources:
    def test_a_conversation_with_an_email_is_one_case_as_transcript_and_dataset_row(
        self, client, environment, dataset, columns
    ):
        # The transcript is stored redacted and the row is read raw: both are
        # fingerprinted as redacted, so the email cannot split them.
        first = _post_transcripts(client, environment, _transcript(text=CHAT)).json()
        row = _row(dataset, 0, {columns["chat"]: CHAT})

        again = _create_rows(
            client,
            environment,
            dataset,
            [row],
            columns,
            mapping=_mapping(columns, labels=[], expected=None),
        ).json()

        [case] = again["cases"]
        assert case["status"] == "duplicate"
        assert case["duplicate_of"]["import_id"] == first["id"]


_CHAT = "User: Where is my parcel?\nAgent: It left the depot today."
# How long a thread may stand blocked on a row lock before it reads as waiting.
_BLOCKED = 2


def _transcript_import(environment, user, name):
    return create_transcript_import(
        environment=environment,
        transcripts=[TranscriptInput(name, "text", _CHAT)],
        production=True,
        user=user,
    )


def _in_thread(work, errors, results=None):
    def run():
        try:
            value = work()
            if results is not None:
                results.append(value)
        except Exception as exc:  # noqa: BLE001 - asserted empty by the test
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


class TestDiscardRaces:
    """Discard takes the environment's lock, as creating an import and adding
    to the suite do, so neither interleaves with it."""

    @pytest.mark.django_db(transaction=True)
    def test_an_import_made_during_a_discard_repeats_the_promoted_case(
        self, environment, user
    ):
        first = _transcript_import(environment, user, "first")
        second = _transcript_import(environment, user, "second")
        [promoted] = second.cases.all()
        promoting, resume = threading.Event(), threading.Event()
        promote = evidence_import_service._promote_duplicates

        def paused(*args, **kwargs):
            promote(*args, **kwargs)
            promoting.set()
            resume.wait(timeout=30)

        errors, made = [], []
        with patch.object(evidence_import_service, "_promote_duplicates", paused):
            discarding = _in_thread(lambda: discard_evidence_import(first), errors)
            assert promoting.wait(timeout=30)
            creating = _in_thread(
                lambda: _transcript_import(environment, user, "third"), errors, made
            )
            # Without the lock it commits now, repeating the case being discarded.
            creating.join(timeout=_BLOCKED)
            resume.set()
            discarding.join(timeout=30)
            creating.join(timeout=30)

        assert not errors
        [third] = made[0].cases.all()
        assert (third.status, third.duplicate_of_id) == ("duplicate", promoted.id)

    @pytest.mark.django_db(transaction=True)
    def test_a_discard_during_an_add_keeps_the_added_case(self, environment, user):
        first = _transcript_import(environment, user, "first")
        [case] = first.cases.all()
        holding, release = threading.Event(), threading.Event()

        def adding():
            # What add-to-suite does under the environment's lock: mark it added.
            with transaction.atomic():
                HostedHarnessJob.no_workspace_objects.select_for_update(
                    of=("self",)
                ).filter(id=environment.id).first()
                EvidenceImportCase.objects.filter(id=case.id).update(
                    status=EvidenceImportCase.Status.ADDED
                )
                holding.set()
                release.wait(timeout=30)

        errors, deleted = [], []
        adder = _in_thread(adding, errors)
        assert holding.wait(timeout=30)
        discarder = _in_thread(lambda: discard_evidence_import(first), errors, deleted)
        discarder.join(timeout=_BLOCKED)
        release.set()
        adder.join(timeout=30)
        discarder.join(timeout=30)

        assert not errors
        # The add committed first, so the discard keeps the import and its case.
        assert deleted == [False]
        case.refresh_from_db()
        first.refresh_from_db()
        assert (case.status, first.deleted) == ("added", False)


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
