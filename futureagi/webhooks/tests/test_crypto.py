"""Encryption at rest (DESIGN.md section 5.3, K2): rotation order, and a malformed key reported by its position only,
with no frame of the error holding key material (FA's Sentry captures frame variables). No database.

A refused ``encrypt`` / ``encrypt_json`` (no key, a malformed key, a value that cannot be encoded) leaves the plaintext
in no log record and in no Sentry event: neither the WA6 log line, with the stack Sentry attaches to it, nor the
error, with its frames. The events are captured unscrubbed (``sentry_capture``), so this holds whatever the variable
names are.
"""

from __future__ import annotations

import logging
import os

import pytest
import sentry_sdk
from cryptography.fernet import Fernet, InvalidToken

from webhooks import crypto
from webhooks.tests.sentry_capture import (
    frame_vars,
    leaked,
    record_text,
    sentry_events,
)

URL_MARK = "u-tok-4e9b"
HEADER_MARK = "hv-k2-58c0"
BAD_ENTRY = "not-a-fernet-key-but-a-secret-7f3a"
PLAIN_URL = f"https://hooks.acme.ai/omega?token={URL_MARK}"
PLAIN_HEADERS = {"Authorization": f"Bearer {HEADER_MARK}"}
# Lambdas over module globals: no test frame holds the plaintext while Sentry captures the stack.
CALLS = {
    "encrypt": lambda: crypto.encrypt(PLAIN_URL),
    "encrypt_json": lambda: crypto.encrypt_json(PLAIN_HEADERS),
}


def test_first_key_encrypts_and_every_key_decrypts(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", old)
    blob = crypto.encrypt("https://hooks.acme.ai/x?token=abc")
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{new}, {old}")
    assert crypto.decrypt(blob) == "https://hooks.acme.ai/x?token=abc"
    assert crypto.decrypt_json(crypto.encrypt_json(PLAIN_HEADERS)) == PLAIN_HEADERS
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", new)
    with pytest.raises(InvalidToken):
        crypto.decrypt(blob)
    monkeypatch.delenv("WEBHOOKS_ENCRYPTION_KEYS")
    with pytest.raises(crypto.KeysMissing):
        crypto.encrypt("x")


def test_a_malformed_key_names_its_position_and_leaves_no_key_in_any_frame(monkeypatch):
    good = Fernet.generate_key().decode()
    bad = "not-a-fernet-key-but-a-secret-7f3a"
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{good},{bad}")
    with pytest.raises(crypto.KeysInvalid) as info:
        crypto.encrypt("x")
    assert "entry 1" in str(info.value)
    assert bad not in str(info.value) and good not in str(info.value)
    assert info.value.__context__ is None and info.value.__cause__ is None
    tb = info.value.__traceback__.tb_next  # the frames below this test's own
    assert tb is not None
    while tb is not None:
        seen = repr(dict(tb.tb_frame.f_locals))
        assert bad not in seen and good not in seen, tb.tb_frame.f_code.co_name
        tb = tb.tb_next


def _report(call):
    """Run ``call`` and send its error to Sentry, as FA's integrations do with an error that escapes a request or an
    activity. Returns the error's type."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        return type(exc)
    raise AssertionError("expected a refusal")


def _needles() -> list[str]:
    keys = [k for k in os.environ.get("WEBHOOKS_ENCRYPTION_KEYS", "").split(",") if k]
    return [URL_MARK, HEADER_MARK, BAD_ENTRY, *keys]


@pytest.mark.parametrize("fn", ["encrypt", "encrypt_json"])
@pytest.mark.parametrize("keys", ["missing", "malformed"])
def test_a_refused_encrypt_leaves_no_plaintext_in_logs_or_sentry(
    keys, fn, monkeypatch, caplog
):
    if keys == "missing":
        monkeypatch.delenv("WEBHOOKS_ENCRYPTION_KEYS", raising=False)
    else:
        monkeypatch.setenv(
            "WEBHOOKS_ENCRYPTION_KEYS", Fernet.generate_key().decode() + "," + BAD_ENTRY
        )
    caplog.set_level(logging.INFO, logger="webhooks.crypto")
    with sentry_events() as events:
        raised = _report(CALLS[fn])
    assert raised is (crypto.KeysMissing if keys == "missing" else crypto.KeysInvalid)
    # The error; and for a malformed key also the WA6 log line, with the stack attached.
    kinds = sorted("exception" if "exception" in e else e.get("logger") for e in events)
    assert kinds == (
        ["exception"] if keys == "missing" else ["exception", "webhooks.crypto"]
    )
    # The function's own frame is in every event, with its variables captured: they hold no plaintext.
    seen = [v for e in events for v in frame_vars(e, "webhooks.crypto", fn)]
    assert len(seen) == len(events) and all(set(v) == {"cipher"} for v in seen)
    assert leaked(events, _needles()) == []
    assert [
        r for r in caplog.records if any(n in record_text(r) for n in _needles())
    ] == []


def test_a_value_that_cannot_be_encoded_is_refused_without_its_plaintext(
    monkeypatch, caplog
):
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    caplog.set_level(logging.INFO, logger="webhooks.crypto")
    with sentry_events() as events:
        raised = [
            # Not JSON: json's own frames hold the value.
            _report(lambda: crypto.encrypt_json({**PLAIN_HEADERS, "tags": {"a", "b"}})),
            # A lone surrogate: the UnicodeEncodeError's repr quotes the whole text.
            _report(lambda: crypto.encrypt_json({"X-Sig": HEADER_MARK + "\ud800"})),
            _report(lambda: crypto.encrypt(PLAIN_URL + "\ud800")),
        ]
    assert raised == [ValueError, ValueError, ValueError]
    assert [e["exception"]["values"][-1]["value"] for e in events] == [
        "the value to encrypt is not JSON text (TypeError)",
        "the value to encrypt is not JSON text (UnicodeEncodeError)",
        "the text to encrypt is not valid Unicode",
    ]
    # One exception each: the suppressed original, whose frames or repr hold the value, is not sent.
    assert [len(e["exception"]["values"]) for e in events] == [1, 1, 1]
    assert leaked(events, _needles()) == []
    assert [
        r for r in caplog.records if any(n in record_text(r) for n in _needles())
    ] == []


def test_the_public_key_check_refuses_like_encrypt_and_keys_ready_logs_nothing(
    monkeypatch, caplog
):
    """``require_keys`` is the K2 gate the API calls before it touches a request's URL, secret or headers; it
    raises what ``encrypt`` raises. ``keys_ready`` answers the same question without raising or logging."""
    caplog.set_level(logging.INFO, logger="webhooks.crypto")
    monkeypatch.delenv("WEBHOOKS_ENCRYPTION_KEYS", raising=False)
    with pytest.raises(crypto.KeysMissing):
        crypto.require_keys()
    assert crypto.keys_ready() is False

    good = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{good},{BAD_ENTRY}")
    with pytest.raises(crypto.KeysInvalid) as info:
        crypto.require_keys()
    assert "entry 1" in str(info.value) and BAD_ENTRY not in str(info.value)
    assert [r.__dict__.get("alert") for r in caplog.records] == ["WA6"]
    caplog.clear()
    assert crypto.keys_ready() is False
    assert caplog.records == []

    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f" {good} ,")
    crypto.require_keys()
    assert crypto.keys_ready() is True
    assert crypto.decrypt(crypto.encrypt("x")) == "x"
    assert caplog.records == []
