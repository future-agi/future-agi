"""Sandbox cleanup finalizes state without billing; the backend seal bills later."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import sys
import tarfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4, uuid5

import pytest
from django.db import transaction
from temporalio.common import WorkflowIDConflictPolicy

from simulate.models import (
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessJob,
)
from simulate.services import harness_usage
from simulate.services.hosted_harness import (
    begin_scenarios,
    canonical_digest,
    create_hosted_job,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    _read_harness_usage,
)
from simulate.services.hosted_harness_ingestion import ingest_result_receipt
from simulate.tasks import hosted_harness_usage as usage_tasks
from simulate.tests.test_harness_usage import (
    _provision,
    _record,
    _report,
    requires_cloud_billing,
)
from simulate.tests.test_hosted_harness_channels import _payload
from tfc import ee_gating
from tfc.ee_loader import has_ee

PRICED_SPEND = {
    "stages": [
        {
            "stage": "understand-agent",
            "models": ["gemini-3.7-flash"],
            "tokens_in": 1000,
            "tokens_out": 100,
        }
    ]
}


class _EeUsageUnimportable:
    """Meta path finder that refuses every ``ee.usage`` import."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ee.usage" or fullname.startswith("ee.usage."):
            raise ModuleNotFoundError(f"{fullname} is unavailable here", name=fullname)
        return None


@contextmanager
def block_ee_usage():
    """Make ``import ee.usage...`` raise ImportError inside the block.

    Cached ``ee.usage*`` modules are evicted for the duration, otherwise the import
    system answers from ``sys.modules`` and never consults the finder; the same
    module objects are put back afterwards, so patches on them survive.
    ``tfc.ee_gating.is_oss`` is cached for the process and would read the block as
    "no ee", so it is answered before the window and forgotten after it.
    """
    process_is_oss = ee_gating.is_oss
    process_is_oss()
    evicted = {
        name: module
        for name, module in sys.modules.items()
        if name == "ee.usage" or name.startswith("ee.usage.")
    }
    for name in evicted:
        del sys.modules[name]
    previous_meta_path = sys.meta_path
    sys.meta_path = [_EeUsageUnimportable(), *previous_meta_path]
    try:
        yield
    finally:
        sys.meta_path = previous_meta_path
        for name in [
            name
            for name in sys.modules
            if name == "ee.usage" or name.startswith("ee.usage.")
        ]:
            del sys.modules[name]
        sys.modules.update(evicted)
        process_is_oss.cache_clear()


def test_blocked_window_leaves_the_process_oss_answer_alone():
    ee_gating.is_oss.cache_clear()
    unblocked = ee_gating.is_oss()
    ee_gating.is_oss.cache_clear()

    with block_ee_usage():
        assert ee_gating.is_oss() == unblocked
        ee_gating.is_oss.cache_clear()
        ee_gating.is_oss()

    assert ee_gating.is_oss() == unblocked


def test_blocked_window_refuses_billing_imports_and_restores_them_after():
    with block_ee_usage():
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("ee.usage.services.emitter")
        assert not has_ee("ee.usage")

    pytest.importorskip("ee.usage.services.emitter")


@pytest.fixture
def ee_usage_unimportable(monkeypatch):
    """Block ``ee.usage`` for the whole test; request it after the job fixtures.

    ``is_oss`` is pinned False because that is the cloud runner's answer, and it is
    what makes a replay reach an ``ee.usage`` import at all.
    """
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    with block_ee_usage():
        yield


@pytest.fixture
def seal_requests(monkeypatch):
    """Record seal enqueues at the Temporal boundary instead of connecting to it."""
    requests = []
    monkeypatch.setattr(
        usage_tasks.seal_hosted_harness_usage,
        "apply_async",
        lambda args, **options: requests.append((list(args), options)),
    )
    return requests


@pytest.fixture
def cleanup_ready(organization):
    """A registered attempt whose job carries priced authoring spend for attempt 1."""
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()
    job.payload["metadata"] = {"harness_spend": {"attempts": {"1": PRICED_SPEND}}}
    job.save(update_fields=["payload", "updated_at"])
    return capability.attempt


def _job(attempt):
    return HostedHarnessJob.no_workspace_objects.get(id=attempt.job_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("terminal_stage", "retry_pending", "attempt_state", "job_state"),
    [
        # The fixture job has no durable authoring snapshot, so a completed attempt
        # closes the job as failed (authoring_snapshot_missing); it still closes.
        (
            "completed",
            False,
            HostedHarnessAttempt.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
        ),
        (
            "canceled",
            False,
            HostedHarnessAttempt.State.CANCELED,
            HostedHarnessJob.State.CANCELED,
        ),
        (
            None,
            True,
            HostedHarnessAttempt.State.FAILED,
            HostedHarnessJob.State.RETRY_WAIT,
        ),
    ],
)
def test_cleanup_finalizes_without_any_billing_import(
    cleanup_ready,
    seal_requests,
    ee_usage_unimportable,
    django_capture_on_commit_callbacks,
    terminal_stage,
    retry_pending,
    attempt_state,
    job_state,
):
    attempt = cleanup_ready
    attempt.terminal_stage = terminal_stage
    attempt.save(update_fields=["terminal_stage", "updated_at"])

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(
            attempt.id,
            provider_ref="",
            verified_absent=True,
            retry_pending=retry_pending,
        )

    attempt.refresh_from_db()
    assert attempt.cleanup_verified_at is not None
    assert attempt.state == attempt_state
    assert _job(attempt).state == job_state
    assert [args for args, _ in seal_requests] == [[str(attempt.id)]]


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_cleanup_finalizes_when_billing_config_is_unavailable(
    cleanup_ready, seal_requests, monkeypatch, django_capture_on_commit_callbacks
):
    from ee.usage.services.config import BillingConfig, BillingConfigError

    def unavailable():
        raise BillingConfigError("billing.yaml not found")

    monkeypatch.setattr(BillingConfig, "get", unavailable)
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    attempt = cleanup_ready

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(attempt.id, provider_ref="", verified_absent=True)

    attempt.refresh_from_db()
    job = _job(attempt)
    assert attempt.cleanup_verified_at is not None
    assert job.state == HostedHarnessJob.State.FAILED
    assert job.terminal_at is not None
    assert [args for args, _ in seal_requests] == [[str(attempt.id)]]


@pytest.mark.django_db
def test_cleanup_schedules_exactly_one_seal_after_commit(
    cleanup_ready, seal_requests, django_capture_on_commit_callbacks
):
    attempt = cleanup_ready

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(attempt.id, provider_ref="", verified_absent=True)
        assert seal_requests == []

    assert seal_requests == [
        (
            [str(attempt.id)],
            {
                "task_id": f"hosted-harness-usage-seal-{attempt.id}",
                "id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING,
                "dispatch_timeout_seconds": 30,
            },
        )
    ]


@pytest.mark.django_db
def test_cleanup_of_a_superseded_attempt_still_schedules_its_seal(
    cleanup_ready, seal_requests, django_capture_on_commit_callbacks
):
    first = cleanup_ready
    register_attempt(first.job_id, endpoint_base_url="https://platform.example")

    with django_capture_on_commit_callbacks(execute=True):
        job = record_cleanup(first.id, provider_ref="", verified_absent=True)

    assert first.attempt_number < job.current_attempt_number
    assert job.terminal_at is None
    assert [args for args, _ in seal_requests] == [[str(first.id)]]


@pytest.mark.django_db
def test_rolled_back_cleanup_schedules_nothing(
    cleanup_ready, seal_requests, django_capture_on_commit_callbacks
):
    attempt = cleanup_ready

    class _Abort(Exception):
        pass

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(_Abort):
        with transaction.atomic():
            record_cleanup(attempt.id, provider_ref="", verified_absent=True)
            raise _Abort

    attempt.refresh_from_db()
    assert attempt.cleanup_verified_at is None
    assert seal_requests == []


@pytest.mark.django_db
def test_failed_seal_enqueue_does_not_undo_cleanup(
    cleanup_ready, monkeypatch, django_capture_on_commit_callbacks
):
    attempt = cleanup_ready
    log = MagicMock()
    monkeypatch.setattr(usage_tasks, "logger", log)

    def refuse(args, **options):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(usage_tasks.seal_hosted_harness_usage, "apply_async", refuse)

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(attempt.id, provider_ref="", verified_absent=True)

    attempt.refresh_from_db()
    assert attempt.cleanup_verified_at is not None
    assert _job(attempt).terminal_at is not None
    assert HostedHarnessCleanupReceipt.no_workspace_objects.filter(
        attempt=attempt
    ).exists()
    log.error.assert_called_once()
    assert log.error.call_args.kwargs["attempt_id"] == str(attempt.id)


@pytest.mark.django_db
def test_cleanup_still_closes_the_sandbox_runtime_inline(cleanup_ready, seal_requests):
    attempt = cleanup_ready
    harness_usage.record_sandbox_runtime(attempt, started=True)

    record_cleanup(attempt.id, provider_ref="", verified_absent=True)

    attempt.refresh_from_db()
    assert attempt.sandbox_runtime["ended_at"]
    assert attempt.sandbox_runtime["seconds"] >= 0


@pytest.mark.django_db
def test_recovered_usage_journal_is_stored_without_emitting(
    cleanup_ready, django_capture_on_commit_callbacks
):
    attempt = cleanup_ready
    record = _record("voice_call", amount=2)
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(
            download_file=lambda path, timeout=None: json.dumps(_report([record]))
        )
    )

    with django_capture_on_commit_callbacks() as callbacks:
        _read_harness_usage(attempt, sandbox)

    attempt.refresh_from_db()
    assert [item["id"] for item in attempt.usage_report["records"]] == [record["id"]]
    assert callbacks == []


def _skipped_receipt(attempt):
    registration = attempt.job.scenario_registrations.get()
    body = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "scenario_key": "case-one",
        "scenario_id": str(registration.scenario_id),
        "scenario_attempt": 1,
        "world_index": None,
        "status": "skipped",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    body["digest"] = canonical_digest(body)
    return body


@pytest.mark.django_db
def test_receipt_ingest_can_skip_the_usage_replay(
    cleanup_ready, django_capture_on_commit_callbacks
):
    attempt = cleanup_ready
    _provision(attempt)
    attempt.job.refresh_from_db()
    begin_scenarios(
        attempt,
        {
            "run_test_id": str(attempt.job.run_test_id),
            "scenario_keys": ["case-one"],
        },
    )
    body = _skipped_receipt(attempt)

    with django_capture_on_commit_callbacks() as silent:
        _, created = ingest_result_receipt(attempt, body, replay_usage=False)
    assert created is True
    assert silent == []

    with django_capture_on_commit_callbacks() as silent_again:
        _, created = ingest_result_receipt(attempt, body, replay_usage=False)
    assert created is False
    assert silent_again == []

    with django_capture_on_commit_callbacks() as replayed:
        ingest_result_receipt(attempt, body)
    assert len(replayed) == 1


def test_offline_recovery_ingests_receipts_without_replaying_usage(monkeypatch):
    artifact = b"result body"
    digest = hashlib.sha256(artifact).hexdigest()
    files = {
        f"outbound-spool/artifacts/{digest}.bin": artifact,
        f"outbound-spool/artifacts/{digest}.json": json.dumps(
            {
                "digest": digest,
                "kind": "result",
                "size": len(artifact),
                "content_type": "application/json",
                "scenario_key": "one",
            }
        ).encode(),
        "outbound-spool/receipts/receipt.json": b'{"digest":"receipt","scenario_key":"one"}',
        "outbound-spool/manifest.json": b'{"digest":"manifest"}',
    }
    archive_body = io.BytesIO()
    with tarfile.open(fileobj=archive_body, mode="w:gz") as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    sandbox = SimpleNamespace(
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0)
        ),
        fs=SimpleNamespace(
            download_file_stream=lambda path, timeout=None: iter(
                [archive_body.getvalue()]
            )
        ),
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(get=lambda *args, **kwargs: sandbox)
    attempt = SimpleNamespace(
        id="attempt-1",
        provider_ref="sandbox-1",
        job=SimpleNamespace(max_artifact_bytes=1024 * 1024),
    )
    receipt_options = []
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_artifact",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_result_receipt",
        lambda *args, **kwargs: receipt_options.append(kwargs),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_manifest",
        lambda *args, **kwargs: None,
    )

    assert gateway._recover_offline_delivery(attempt) is True

    assert receipt_options[0]["replay_usage"] is False


def _poll_with_complete_priced_authoring(attempt, monkeypatch):
    """Run the real poll with ``ee.usage`` unimportable; it must store, never price."""
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    gateway_log = MagicMock()
    monkeypatch.setattr("simulate.services.hosted_harness_gateway.logger", gateway_log)
    job = _job(attempt)
    job.payload["metadata"] = {}
    job.save(update_fields=["payload", "updated_at"])
    files = {
        "/work/authoring/contract.json": b'{"modality":"voice"}',
        "/work/authoring/environment-bundle/environment-plan.json": b'{"runtime":{}}',
        "/work/authoring/scenarios.json": b'[{"scenario_key":"one"}]',
        "/work/bundle/manifest.json": b'{"schema_version":"v2"}',
        "/work/authoring/cost.json": json.dumps(
            {"total_usd": 0.5, "unpriced_turns": 0, **PRICED_SPEND}
        ).encode(),
        "/tmp/authoring-rerun.tar.gz": b"frozen-authoring",
    }
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(download_file=lambda path, timeout=None: files[path]),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
        ),
    )

    with (
        block_ee_usage(),
        patch("simulate.services.hosted_harness_gateway.store_authoring_archive"),
    ):
        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

    job.refresh_from_db()
    attempt.refresh_from_db()
    assert job.payload["metadata"]["harness_spend"]["attempts"]["1"]["stages"] == (
        PRICED_SPEND["stages"]
    )
    assert attempt.authoring_usage_report is None
    priced = [
        call
        for call in gateway_log.exception.call_args_list
        if str(call.args[0]).startswith("could not price")
    ]
    assert priced == []


@pytest.mark.django_db
def test_poll_stores_the_spend_without_pricing_authoring(cleanup_ready, monkeypatch):
    _poll_with_complete_priced_authoring(cleanup_ready, monkeypatch)


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_poll_stores_the_spend_and_leaves_authoring_pricing_to_the_seal(
    cleanup_ready, seal_requests, monkeypatch, django_capture_on_commit_callbacks
):
    from ee.usage.services import emitter

    events = []
    monkeypatch.setattr(emitter, "emit", events.append)
    attempt = cleanup_ready

    _poll_with_complete_priced_authoring(attempt, monkeypatch)
    assert events == []

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(attempt.id, provider_ref="", verified_absent=True)
    assert [args for args, _ in seal_requests] == [[str(attempt.id)]]
    with django_capture_on_commit_callbacks(execute=True):
        usage_tasks.seal_hosted_harness_usage._original_func(str(attempt.id))

    attempt.refresh_from_db()
    assert attempt.authoring_usage_report["records"][0]["stage"] == "understand-agent"
    assert [event.event_type for event in events] == ["harness_authoring"]
    assert events[0].event_id == str(
        uuid5(UUID(str(attempt.id)), "authoring:understand-agent")
    )
