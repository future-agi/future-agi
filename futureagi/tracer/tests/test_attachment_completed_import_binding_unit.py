"""No-DB, no-network completion binding/crypto boundaries, not SQL proof."""

import base64
import hashlib
import json
import sys
from dataclasses import fields
from datetime import timedelta
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tracer.tests import test_attachment_simplify_sources_control as shared
from tracer.tests.test_attachment_simplify_sources_control import (
    NOW,
    Query,
    row,
)

source_clock = shared.source_clock
modules = shared.modules


def info():
    return {
        "egressId": "EG_test",
        "roomName": "room",
        "status": "EGRESS_COMPLETE",
        "updatedAt": "1",
        "fileResults": [
            {
                "location": "https://example.test/object?private=sentinel",
                "size": "10",
                "duration": "1000000",
            }
        ],
    }


@pytest.mark.parametrize("room", ["room", " room ", "é", "é", "room-\U0001f3b5"])
def test_exact_utf8_room_digest_no_normalization(modules, room):
    assert (
        modules.provider_recordings._digest(room)
        == hashlib.sha256(room.encode()).hexdigest()
    )


def test_invalid_unicode_room_rejected(modules):
    with pytest.raises(modules.contract.AttachmentError):
        modules.provider_recordings._digest("\ud800")


@pytest.mark.parametrize("mode", [None, [], {}, True, "explicit", "sdk_explicit "])
def test_invalid_dispatch_mode_is_closed_before_authority(modules, mode):
    with pytest.raises(modules.contract.AttachmentError) as error:
        modules.provider_recordings.register(
            row(),
            scope_id=uuid4(),
            connection_id=uuid4(),
            egress_id="EG_test",
            room_name="room",
            recording_id=str(uuid4()),
            association={},
            direction="mixed",
            dispatch_mode=mode,
        )
    assert error.value.code == "validation_failed"
    modules.bootstrap.check_session.assert_not_called()


def test_pending_plaintext_exact_64k_boundary(modules):
    p = modules.provider_recordings
    connection = row(authority_generation=7)
    value = info()
    value["padding"] = ""
    size = len(json.dumps({"egressInfo": value, "authority_generation": 7}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
    value["padding"] = "x" * (65536 - size)
    retained = p._pending_plaintext(connection, value)
    assert len(retained) < 65536 and b"padding" not in retained
    value["padding"] += "x"
    with pytest.raises(modules.contract.AttachmentError) as error:
        p._pending_plaintext(connection, value)
    assert error.value.code == "too_large"


@pytest.mark.parametrize(
    "change",
    [
        {"status": "EGRESS_ACTIVE"},
        {"egressId": "EG_"},
        {"roomName": ""},
        {"fileResults": []},
        {"fileResults": [{"size": True, "duration": 1}]},
        {"fileResults": [{"size": 10, "duration": "0"}]},
    ],
)
def test_only_supported_completed_payloads_are_retained(modules, change):
    value = {**info(), **change}
    assert (
        modules.provider_recordings._pending_plaintext(
            row(authority_generation=0), value
        )
        is None
    )


def test_pending_crypto_domain_scope_event_and_rotation(modules, monkeypatch):
    p = modules.provider_recordings
    event = row(
        id=uuid4(), scope_id=uuid4(), connection_id=uuid4(), event_id="signed-event"
    )
    aad = p._pending_aad(event)
    plaintext = p._pending_plaintext(row(authority_generation=0), info())
    key, nonce, ciphertext = modules.envelopes.seal_bytes(plaintext, aad)
    assert b"sentinel" not in ciphertext
    assert modules.envelopes.open_bytes(key, nonce, ciphertext, aad) == plaintext
    for name in ("id", "scope_id", "connection_id", "event_id"):
        changed = row(
            **{
                field: getattr(event, field)
                for field in ("id", "scope_id", "connection_id", "event_id")
            }
        )
        setattr(changed, name, uuid4())
        with pytest.raises(modules.contract.AttachmentError):
            modules.envelopes.open_bytes(
                key, nonce, ciphertext, p._pending_aad(changed)
            )
    with pytest.raises(modules.contract.AttachmentError):
        modules.envelopes.open_bytes(
            key, nonce, ciphertext, modules.envelopes._aad(event.scope_id, event.id)
        )
    old = modules.settings.FI_ATTACHMENTS_ENVELOPE_KEYS
    monkeypatch.setattr(
        modules.settings,
        "FI_ATTACHMENTS_ENVELOPE_KEYS",
        "new:" + base64.urlsafe_b64encode(b"y" * 32).decode() + "," + old,
    )
    assert modules.envelopes.open_bytes(key, nonce, ciphertext, aad) == plaintext
    monkeypatch.setattr(
        modules.settings,
        "FI_ATTACHMENTS_ENVELOPE_KEYS",
        "new:" + base64.urlsafe_b64encode(b"y" * 32).decode(),
    )
    with pytest.raises(modules.contract.AttachmentError):
        modules.envelopes.open_bytes(key, nonce, ciphertext, aad)


@pytest.mark.parametrize("mode", ["auto", "sdk_explicit"])
def test_completion_only_auto_gets_dispatch_cursor(modules, monkeypatch, mode):
    p = modules.provider_recordings
    registration = row(
        provider_state="registered",
        provider_updated_at=0,
        room_digest=p._digest("room"),
        registrar_kind="user",
        registrar_id=uuid4(),
        direction="mixed",
        trace_id=uuid4(),
        span_id="1234567890abcdef",
        source_authority_generation=0,
        dispatch_mode=mode,
    )
    monkeypatch.setattr(modules.envelopes, "seal", Mock(return_value=uuid4()))
    assert p._apply_info(
        registration,
        info(),
        metadata={
            "url": "https://example.test/object",
            "generation_kind": "http_strong_etag",
            "generation": '"v1"',
            "source_bytes": 10,
            "duration_ms": 1,
            "mime_type": "audio/wav",
        },
        scope=row(),
        expires_at=NOW + timedelta(minutes=1),
        capture_session_id=uuid4(),
    )
    assert registration.provider_state == "complete"
    assert (registration.dispatch_due_at is None) == (mode == "sdk_explicit")


def test_explicit_completed_is_never_selected_by_dispatch_or_reconcile(
    modules, monkeypatch
):
    p = modules.provider_recordings
    explicit = row(
        id=uuid4(),
        dispatch_mode="sdk_explicit",
        provider_state="complete",
        import_job_id=None,
        dispatch_due_at=NOW - timedelta(seconds=1),
    )
    modules.rm.AttachmentProviderRecording.objects = Query([explicit])
    imports = ModuleType("tracer.services.attachments.imports")
    imports.submit = Mock(side_effect=AssertionError("explicit cannot auto-admit"))
    monkeypatch.setitem(sys.modules, imports.__name__, imports)
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments"], "imports", imports, raising=False
    )
    assert p.dispatch_completed()["dispatched"] == 0
    assert p.reconcile_registered()["observed"] == 0
    imports.submit.assert_not_called()


def test_sourcefacts_exact_fifteen_fields_unchanged(modules):
    assert tuple(field.name for field in fields(modules.interfaces.SourceFacts)) == (
        "source_kind",
        "source_id",
        "creator_kind",
        "creator_id",
        "generation_kind",
        "generation",
        "byte_length",
        "mime_type",
        "duration_ms",
        "expires_at",
        "authority_generation",
        "parent_trace_id",
        "parent_span_id",
        "direction",
        "connection_id",
    )


def test_signed_extraneous_credentials_are_never_retained(modules):
    value = info()
    value["request"] = {"s3": {"accessKey": "credential-sentinel", "secret": "credential-sentinel"}}
    value["jwt"] = "credential-sentinel"
    value["fileResults"][0]["credentials"] = "credential-sentinel"
    plaintext = modules.provider_recordings._pending_plaintext(row(authority_generation=7), value)
    assert b"credential-sentinel" not in plaintext
    document = json.loads(plaintext)
    assert set(document) == {"egressInfo", "authority_generation"}
    assert set(document["egressInfo"]) == {"egressId", "roomName", "status", "updatedAt", "fileResults"}
    assert set(document["egressInfo"]["fileResults"][0]) == {"location", "size", "duration"}


@pytest.mark.parametrize("updated", [-1, 2**63, "99999999999999999999"])
def test_pending_update_clock_fits_durable_integer(modules, updated):
    value = {**info(), "updatedAt": updated}
    assert modules.provider_recordings._pending_plaintext(row(authority_generation=7), value) is None
