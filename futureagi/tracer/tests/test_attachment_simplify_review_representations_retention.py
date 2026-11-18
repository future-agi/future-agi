"""Actual retention control flow with synthetic IO; does not stand in for SQL locks."""

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "_review_retention_helpers",
    Path(__file__).with_name("test_attachment_simplify_lifecycle_behavior.py"),
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
interfaces = base.interfaces


@pytest.fixture
def cleanup(monkeypatch, interfaces):
    ctx = base.cleanup.__wrapped__(monkeypatch, interfaces)
    ctx.env["AttachmentState"] = NS(
        DELETED="deleted",
        READY="ready",
        FAILED="failed",
        REJECTED="rejected",
        EXPIRED="expired",
    )
    ctx.asset.verified_blob_id, ctx.asset.verified_at = uuid4(), base.NOW
    # Load the new helper without altering the historical fixture or any body.
    try:
        helper = base.load(
            "retention",
            {"_unpublished_failure"},
            AttachmentState=ctx.env["AttachmentState"],
        )
        ctx.env["_unpublished_failure"] = helper["_unpublished_failure"]
    except AssertionError:
        pass  # baseline run: helper did not exist; exercise unchanged old code
    return ctx


def unpublished(ctx, state="rejected", bound=True):
    ctx.chain[5].clear()
    ctx.asset.state = ctx.upload.state = state
    ctx.asset.deleted_at = None
    ctx.asset.verified_blob_id = ctx.asset.verified_at = None
    ctx.asset.verified_storage_key = ctx.asset.verified_storage_version = None
    ctx.metadata = base.Row(
        scope=ctx.scope,
        attachment_id=ctx.asset.pk,
        deleted_at=None,
        expires_at=base.NOW + timedelta(days=10 if bound else 1),
        source_expires_at=base.NOW + timedelta(days=10) if bound else None,
        deletion_generation=ctx.scope.deletion_generation,
        bound_at=base.NOW - timedelta(hours=1) if bound else None,
    )
    # Run the real survivor predicate; metadata stays a survivor. The separate
    # terminal allocation fence, not a weakened survivor query, allows cleanup.
    survivor = base.load(
        "retention",
        {"_survivors"},
        AttachmentOccurrence=NS(objects=base.Query([ctx.metadata])),
    )
    ctx.env["_survivors"] = survivor["_survivors"]
    ctx.storage.erase_all_versions = Mock(
        return_value=ctx.interfaces.EraseResult(True, False, None, base.NOW)
    )
    ctx.settle = Mock(return_value=True)
    sys.modules["tracer.services.attachments.uploads"].settle = ctx.settle
    return ctx


@pytest.mark.parametrize("state", ["failed", "rejected", "expired"])
@pytest.mark.parametrize("bound", [False, True])
def test_terminal_allocation_cleanup_preserves_failure_metadata(cleanup, state, bound):
    ctx = unpublished(cleanup, state, bound)
    assert ctx.env["_survivors"](ctx.scope, [ctx.asset.pk], base.NOW).exists()
    assert base.run_erase(ctx)
    assert ctx.asset.state == ctx.upload.state == state
    assert ctx.asset.deleted_at is None and ctx.metadata.deleted_at is None
    assert ctx.upload.cleanup_checked_at == base.NOW
    assert ctx.metadata.expires_at > base.NOW
    assert ctx.metadata.bound_at is not None if bound else ctx.metadata.bound_at is None
    assert ctx.storage.erase_all_versions.call_count == 2
    ctx.settle.assert_called_once_with(
        ctx.upload.pk, scope_id=ctx.scope.pk, outcome=state
    )
    assert "derivatives" not in ctx.calls
    # Replay reconciles locators, not an immutable outcome rewrite.
    assert base.run_erase(ctx)
    assert ctx.asset.state == ctx.upload.state == state


@pytest.mark.parametrize(
    "case",
    [
        "partial",
        "reason",
        "retryable",
        "future",
        "early",
        "none",
        "active_upload",
        "active_validate",
        "horizon",
    ],
)
def test_terminal_cleanup_keeps_charge_without_absence_and_termination(cleanup, case):
    ctx = unpublished(cleanup)
    results = {
        "partial": ctx.interfaces.EraseResult(False, True, "pending", base.NOW),
        "reason": ctx.interfaces.EraseResult(True, False, "pending", base.NOW),
        "retryable": ctx.interfaces.EraseResult(True, True, None, base.NOW),
        "future": ctx.interfaces.EraseResult(
            True, False, None, base.NOW + timedelta(seconds=1)
        ),
        "early": ctx.interfaces.EraseResult(
            True, False, None, base.NOW - timedelta(days=1)
        ),
        "none": None,
    }
    if case in results:
        ctx.storage.erase_all_versions.return_value = results[case]
    elif case == "active_upload":
        ctx.upload.transfer_started_at = base.NOW - timedelta(days=1)
    elif case == "active_validate":
        ctx.env["AttachmentOutbox"] = NS(
            objects=base.Query(
                [
                    base.Row(
                        upload_id=ctx.upload.pk,
                        event="validate",
                        processed_at=None,
                        attempts=1,
                    )
                ]
            )
        )
    else:
        ctx.upload.cleanup_not_before = base.NOW + timedelta(seconds=1)
    assert not base.run_erase(ctx)
    assert ctx.upload.cleanup_checked_at is None
    ctx.settle.assert_not_called()
    assert ctx.asset.deleted_at is None


@pytest.mark.parametrize("change", ["epoch", "key", "horizon", "state", "publication"])
def test_terminal_allocation_revalidated_after_unlocked_storage(cleanup, change):
    ctx = unpublished(cleanup)

    def erase(locator):
        if change == "epoch":
            ctx.upload.allocation_epoch += 1
        elif change == "key":
            ctx.upload.serving_key += "changed"
        elif change == "horizon":
            ctx.upload.cleanup_not_before = base.NOW + timedelta(seconds=1)
        elif change == "state":
            ctx.upload.state = "deleted"
        else:
            ctx.asset.verified_blob_id = uuid4()
        return ctx.interfaces.EraseResult(True, False, None, base.NOW)

    ctx.storage.erase_all_versions.side_effect = erase
    try:
        assert not base.run_erase(ctx)
    except base.Error as error:
        assert error.code == "cleanup_fenced"
    assert ctx.upload.cleanup_checked_at is None
    ctx.settle.assert_not_called()


def test_published_asset_survivor_still_protects_every_representation(cleanup):
    ctx = cleanup
    ctx.asset.state, ctx.asset.deleted_at = "ready", None
    ctx.env["_survivors"] = lambda *args: base.Query([base.Row()])
    assert not base.run_erase(ctx)
    assert ctx.calls == []
    assert ctx.upload.cleanup_checked_at is None


def test_terminal_asset_with_published_history_is_not_unpublished(cleanup):
    ctx = unpublished(cleanup)
    ctx.asset.verified_blob_id = uuid4()
    assert not base.run_erase(ctx)
    ctx.storage.erase_all_versions.assert_not_called()
    ctx.settle.assert_not_called()


def test_failed_erase_event_completes_without_tombstoning_metadata(cleanup):
    ctx = unpublished(cleanup)
    event = base.Row(
        event="erase",
        attachment_id=ctx.asset.pk,
        upload_id=ctx.upload.pk,
        scope_id=ctx.scope.pk,
        generation=ctx.scope.deletion_generation,
        processed_at=None,
        available_at=base.NOW - timedelta(seconds=1),
        attempts=0,
    )
    ctx.env["AttachmentOutbox"] = NS(objects=base.Query([event]))
    assert ctx.env["erase"](event.pk, storage=ctx.storage)
    assert event.processed_at == base.NOW
    assert ctx.asset.deleted_at is None and ctx.metadata.deleted_at is None
