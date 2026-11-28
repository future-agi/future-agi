"""Shared READY cleanup and truthful decoder-plan gates; no external IO."""

import sys
from datetime import timedelta
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tracer.tests import test_attachment_simplify_upload_accounting_ledger as shared
from tracer.tests.test_attachment_simplify_upload_accounting_ledger import NOW

env = shared.env


def readiness(monkeypatch, **fields):
    module = ModuleType("tracer.services.attachments.readiness")
    module.__dict__.update(fields)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments"], "readiness", module, raising=False
    )


@pytest.mark.parametrize("operation", ["validate", "normalize"])
def test_l16_plan_rejected_without_decoder_or_body_work(env, monkeypatch, operation):
    gate = Mock(side_effect=AssertionError("unsupported plan before readiness"))
    readiness(monkeypatch, require_decoder=gate)
    monkeypatch.setattr(env.v, "sandbox_probe", gate)
    with pytest.raises(env.error) as error:
        env.v.require_decoder(env.i.DecoderRequirements(operation, "audio/L16", 100))
    assert (error.value.code, error.value.status) == ("unsupported_media", 422)
    gate.assert_not_called()


@pytest.mark.parametrize(
    "failure", [None, "validation_unavailable", "validation_rejected", "wrong-facts"]
)
def test_validate_readiness_executes_actual_probe_path(env, monkeypatch, failure):
    events = []
    readiness(monkeypatch, require_decoder=lambda _: events.append("seccomp"))

    def probe(path):
        events.append("bubblewrap")
        with open(path, "rb") as sample:
            data = sample.read()
        assert len(data) == 46 and data[:4] == b"RIFF"
        if failure and failure != "wrong-facts":
            raise env.error(
                failure, 503 if failure == "validation_unavailable" else 422
            )
        return {
            "mime_type": "audio/wav",
            "sample_rate": 8000,
            "channels": 1,
            "frame_count": 2 if failure else 1,
        }

    monkeypatch.setattr(env.v, "sandbox_probe", probe)
    requirements = env.i.DecoderRequirements("validate", "audio/wav", 100)
    if failure:
        with pytest.raises(env.error) as error:
            env.v.require_decoder(requirements)
        assert (error.value.code, error.value.status) == ("validation_unavailable", 503)
    else:
        env.v.require_decoder(requirements)
    assert events == ["seccomp", "bubblewrap"]


def test_normalization_does_not_claim_bubblewrap_is_its_executor(env, monkeypatch):
    gate = Mock()
    readiness(monkeypatch, require_decoder=gate)
    probe = Mock(side_effect=AssertionError("normalization belongs to media worker"))
    monkeypatch.setattr(env.v, "sandbox_probe", probe)
    requirements = env.i.DecoderRequirements("normalize", "audio/wav", 100)
    env.v.require_decoder(requirements)
    gate.assert_called_once_with(requirements)
    probe.assert_not_called()


@pytest.mark.parametrize(
    "block", [None, "horizon", "quarantine", "serving", "transfer"]
)
def test_ready_reconciliation_preserves_serving_and_retains_charge_without_proof(
    env, monkeypatch, block
):
    scope = env.scope()
    upload, owner = env.owner(scope, producer_kind="import", state="ready")
    upload.quarantine_key, upload.serving_key = "quarantine/unit", "original/unit"
    upload.cleanup_checked_at = None
    if block == "horizon":
        upload.cleanup_not_before = NOW + timedelta(seconds=1)
    if block == "transfer":
        upload.transfer_started_at = NOW
    amount = env.i.ResourceVector(100, 1, 1, 0, 2, 1)
    with env.tx.atomic():
        locks = env.db.lock_capacity(
            scope_id=scope.id, pool_keys=(upload.reservation_pool_key,)
        )
        env.db.reserve_locked(
            locks, owner=owner, amount=amount, expires_at=upload.reservation_expires_at
        )
    attachment = NS(deleted_at=None)
    binding = uuid4()
    keep = env.i.ObjectIdentity(binding, "serving", upload.serving_key, "published")
    unknown = env.i.ObjectIdentity(binding, "serving", upload.serving_key, "unknown")
    live = [keep, unknown]
    calls = []
    storage = NS(binding_id=binding)

    def erase(locator):
        calls.append("quarantine")
        assert locator.purpose == "quarantine"
        return NS(
            empty=block != "quarantine", retryable=block == "quarantine", checked_at=NOW
        )

    def list_versions(locator, **kwargs):
        assert locator.purpose == "serving"
        return NS(versions=tuple(live), delete_markers=(), next_cursor=None)

    def cleanup(upload_id, **kwargs):
        identity = kwargs["identity"]
        assert identity != keep
        calls.append("unowned")
        if block != "serving":
            live.remove(identity)

    storage.erase_all_versions, storage.list_versions = erase, list_versions
    monkeypatch.setattr(
        env.v,
        "_lock_upload",
        lambda *args, **kwargs: (scope, None, None, upload, attachment, None),
    )
    monkeypatch.setattr(env.v, "_asset_facts", lambda _: NS(object=NS(identity=keep)))
    monkeypatch.setattr(env.v, "cleanup_unowned_result", cleanup)
    receipt = Mock(
        side_effect=lambda *args, **kwargs: setattr(upload, "usage_id", uuid4())
    )
    monkeypatch.setattr(env.a, "receipt_locked", receipt)
    released = env.v.reconcile_ready_upload(upload, storage)
    assert released is (block is None)
    assert keep in live
    if block:
        assert (
            upload.reservation_settled_at is None
            and env.db.owner_vector(upload) == amount
        )
        receipt.assert_not_called()
        if block in {"horizon", "transfer"}:
            assert calls == []
    else:
        assert live == [keep] and upload.reservation_settled_at == NOW
        assert upload.cleanup_checked_at == NOW
        receipt.assert_called_once()
        # Replay never erases the retained serving identity or releases twice.
        assert env.v.reconcile_ready_upload(upload, storage)
        assert calls == ["quarantine", "unowned"]
