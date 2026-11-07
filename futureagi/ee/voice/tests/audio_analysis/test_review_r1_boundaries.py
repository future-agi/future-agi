"""Review regressions: retryable launches, pinned sources and ancestor tombstones."""

import copy
import io
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync

from ee.voice.services import recording_provenance
from ee.voice.services.audio_analysis.commit import commit_envelope
from ee.voice.services.audio_analysis.decode import fetch_audio
from ee.voice.services.audio_analysis.errors import (
    AudioDeterministicError,
    AudioProvenanceError,
    StaleAnalysisError,
)
from ee.voice.services.audio_provenance import build_vapi_provenance, validate_manifest
from ee.voice.temporal.activities import audio_analysis as aa
from ee.voice.tests.audio_analysis import test_schedule

audio_call = test_schedule.audio_call
eligible_call = test_schedule.eligible_call
schedule = test_schedule.schedule


def analysis_input(call, scheduled):
    return aa.AnalyzeCallAudioInput(
        str(call.id),
        scheduled.analysis_id,
        scheduled.generation,
        scheduled.deadline_at,
    )


@pytest.mark.parametrize("state", ["available", "partial", "unavailable", "failed"])
def test_terminal_identity_never_relaunches(eligible_call, state):
    scheduled = schedule(eligible_call)
    eligible_call.refresh_from_db()
    eligible_call.audio_metrics.update(state=state, computed_at="2026-10-02T00:00:00Z")
    eligible_call.save(update_fields=["audio_metrics"])
    original = copy.deepcopy(eligible_call.audio_metrics)
    retry = schedule(eligible_call)
    assert not retry.scheduled
    assert retry.skip_reason == "duplicate_identity"
    assert retry.analysis_id == scheduled.analysis_id
    assert retry.deadline_at == scheduled.deadline_at
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == original


@pytest.fixture
def capture(monkeypatch, settings):
    settings.UPLOAD_BUCKET_NAME = "recordings"
    storage = Mock()
    monkeypatch.setattr(recording_provenance, "get_audio_storage", lambda: storage)
    backoff = AsyncMock()
    monkeypatch.setattr(recording_provenance.asyncio, "sleep", backoff)
    return storage, backoff


def capture_target(call_id, *, pin_versions=True):
    recordings = SimpleNamespace(
        recording_url=None,
        stereo_recording_url=None,
        assistant_recording_url=None,
        customer_recording_url=(
            f"https://recordings.s3.amazonaws.com/call-recordings/{call_id}/customer.wav"
        ),
    )
    return async_to_sync(recording_provenance.recording_artifacts)(
        recordings, pin_versions=pin_versions
    )


@pytest.mark.parametrize("failure", [ConnectionError("storage unavailable"), {}])
def test_capture_retries_missing_pin_and_recovers(capture, failure):
    storage, backoff = capture
    storage.head_object.side_effect = [failure, failure, {"ETag": '"original"'}]
    artifacts = capture_target("call")
    assert artifacts[0].object_version_or_etag == '"original"'
    assert storage.head_object.call_count == 3
    assert backoff.await_count == 2


def test_null_storage_version_uses_etag(capture):
    storage, _ = capture
    storage.head_object.return_value = {"VersionId": "null", "ETag": '"original"'}
    assert capture_target("call")[0].object_version_or_etag == '"original"'
    storage.head_object.assert_called_once()


@pytest.mark.parametrize("failure", [ConnectionError("storage unavailable"), {}])
def test_failed_capture_then_replacement_never_schedules(
    eligible_call, capture, failure
):
    storage, backoff = capture
    storage.head_object.side_effect = (
        failure if isinstance(failure, Exception) else None
    )
    storage.head_object.return_value = failure
    artifacts = capture_target(str(eligible_call.id))
    assert artifacts[0].object_version_or_etag is None
    eligible_call.audio_provenance = asdict(
        build_vapi_provenance(
            call_id=str(eligible_call.id),
            direction="inbound",
            recording_owner_account="system",
            artifacts=artifacts,
        )
    )
    eligible_call.save(update_fields=["audio_provenance"])
    # The object is replaced before scheduling. A later HEAD must not retroactively
    # supply identity evidence for the recording whose capture-time pin failed.
    storage.head_object.side_effect = None
    storage.head_object.return_value = {"ETag": '"replacement"'}
    result = schedule(eligible_call)
    assert not result.scheduled
    assert result.skip_reason == "storage_unavailable"
    assert storage.head_object.call_count == 3
    assert backoff.await_count == 2
    storage.get_object.assert_not_called()
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics["state"] == "failed"
    assert eligible_call.audio_metrics["analysis_id"] is None
    for entry in eligible_call.audio_metrics["metrics"].values():
        assert entry["value"] is None
        assert entry["state"] == "failed"
        assert entry["reason"] == "storage_unavailable"


@pytest.mark.parametrize("version", [None, "", "null", " ", 42])
def test_manifest_rejects_missing_immutable_pin(eligible_call, settings, version):
    eligible_call.audio_provenance["artifacts"][0]["object_version_or_etag"] = version
    with pytest.raises(AudioProvenanceError, match="storage_unavailable"):
        validate_manifest(
            eligible_call.audio_provenance,
            str(eligible_call.id),
            settings.UPLOAD_BUCKET_NAME,
        )


@pytest.mark.parametrize(
    "field,condition", [("ETag", "IfMatch"), ("VersionId", "VersionId")]
)
def test_fetch_conditions_get_on_scheduled_pin(field, condition):
    storage = Mock()
    storage.head_object.return_value = {"ContentLength": 5, field: "original"}
    body = io.BytesIO(b"audio")
    storage.get_object.return_value = {"Body": body, field: "original"}
    assert (
        fetch_audio(
            storage,
            bucket="recordings",
            key="call-recordings/call/customer.wav",
            call_id="call",
            expected_version="original",
        )
        == b"audio"
    )
    storage.get_object.assert_called_once_with(
        Bucket="recordings",
        Key="call-recordings/call/customer.wav",
        **{condition: "original"},
    )
    assert body.closed


def test_fetch_rejects_replacement_before_get():
    storage = Mock()
    storage.head_object.return_value = {"ContentLength": 5, "ETag": '"replacement"'}
    with pytest.raises(AudioDeterministicError, match="storage_unavailable"):
        fetch_audio(
            storage,
            bucket="recordings",
            key="call-recordings/call/customer.wav",
            call_id="call",
            expected_version='"original"',
            sleep=Mock(),
        )
    storage.get_object.assert_not_called()


def delete_ancestor(call, ancestor):
    execution = call.test_execution
    (execution.run_test if ancestor == "run" else execution).delete()
    call.refresh_from_db()
    assert not call.deleted  # Parent deletion does not tombstone the call itself.


@pytest.mark.parametrize("ancestor", ["run", "execution"])
def test_deleted_ancestor_blocks_scheduling(eligible_call, ancestor):
    delete_ancestor(eligible_call, ancestor)
    assert schedule(eligible_call).skip_reason == "missing_call"
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics is None


@pytest.mark.parametrize("ancestor", ["run", "execution"])
def test_deleted_ancestor_blocks_queued_load(eligible_call, ancestor):
    scheduled = schedule(eligible_call)
    delete_ancestor(eligible_call, ancestor)
    with pytest.raises(StaleAnalysisError, match="tombstone"):
        aa._load_current(analysis_input(eligible_call, scheduled))


@pytest.mark.parametrize("ancestor", ["run", "execution"])
@pytest.mark.parametrize("boundary", ["commit_envelope", "commit_result", "finalizer"])
def test_ancestor_deleted_after_load_blocks_final_write(
    eligible_call, ancestor, boundary
):
    scheduled = schedule(eligible_call)
    input = analysis_input(eligible_call, scheduled)
    loaded = aa._load_current(input)
    original = copy.deepcopy(loaded.audio_metrics)
    terminal = aa._terminal_pending(copy.deepcopy(original), "timeout")
    delete_ancestor(eligible_call, ancestor)
    if boundary == "finalizer":
        aa._finalize_audio_analysis_failure(
            aa.FinalizeFailureInput(
                str(eligible_call.id), input.analysis_id, 0, "timeout"
            )
        )
    else:
        with pytest.raises(StaleAnalysisError, match="tombstone"):
            if boundary == "commit_envelope":
                commit_envelope(str(eligible_call.id), 0, input.analysis_id, terminal)
            else:
                aa._commit_result(input, terminal, provenance=loaded.audio_provenance)
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == original
    assert eligible_call.audio_provenance == loaded.audio_provenance


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ancestor", ["run", "execution"])
@pytest.mark.parametrize("boundary", ["commit_envelope", "commit_result"])
def test_commit_holds_ancestor_lock_until_write(
    eligible_call, monkeypatch, ancestor, boundary
):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import DatabaseError, connections, transaction

    from simulate.models.test_execution import CallExecution

    scheduled = schedule(eligible_call)
    eligible_call.refresh_from_db()
    input = analysis_input(eligible_call, scheduled)
    execution = eligible_call.test_execution
    parent = execution.run_test if ancestor == "run" else execution
    parent_model = type(parent)
    original_save = CallExecution.save
    lock_checks = []

    def try_parent_lock():
        # A soft-delete UPDATE takes this same row lock. Use a separate DB
        # connection and NOWAIT to test serialization without a timing race.
        try:
            with transaction.atomic():
                parent_model.objects.select_for_update(nowait=True).get(pk=parent.pk)
        except DatabaseError as exc:
            return getattr(exc.__cause__, "sqlstate", None) or getattr(
                exc.__cause__, "pgcode", None
            )
        finally:
            connections.close_all()
        return None

    def save_with_lock_check(call, *args, **kwargs):
        if "audio_metrics" in kwargs.get("update_fields", []):
            with ThreadPoolExecutor(max_workers=1) as pool:
                state = pool.submit(try_parent_lock).result(timeout=10)
            assert state == "55P03", "Ancestor deletion can race the final write"
            lock_checks.append(state)
        return original_save(call, *args, **kwargs)

    monkeypatch.setattr(CallExecution, "save", save_with_lock_check)
    terminal = aa._terminal_pending(
        copy.deepcopy(eligible_call.audio_metrics), "timeout"
    )
    if boundary == "commit_envelope":
        commit_envelope(str(eligible_call.id), 0, input.analysis_id, terminal)
    else:
        assert aa._commit_result(input, terminal).committed
    assert lock_checks == ["55P03"]
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == terminal


def test_versionless_language_ignores_post_recording_definition(eligible_call):
    agent = eligible_call.test_execution.agent_definition
    agent.language = "fr"
    agent.languages = ["fr"]
    agent.save(update_fields=["language", "languages"])
    scheduled = schedule(eligible_call)
    agent.language = "en"
    agent.languages = ["en"]
    agent.save(update_fields=["language", "languages"])
    loaded = aa._load_current(analysis_input(eligible_call, scheduled))
    assert aa._declared_language(loaded) is None
