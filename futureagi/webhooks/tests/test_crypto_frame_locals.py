"""K2, F73-W1-S-DEP-CRYPTO-LOCALS: a ciphertext that does not decrypt (corrupt, or written under a key since removed
from the ring) and a ring with a malformed entry leave neither the ciphertext, its plaintext nor any key in a Sentry
event (DESIGN.md section 5.3). No database.

FA's Sentry attaches the stack, with the variables of every frame, to the WA6 error lines :mod:`webhooks.crypto` logs
(``attach_stacktrace``, ``include_local_variables``), and scrubs variables by name only; ``blob`` is not such a name.
So each case runs the real function under the unscrubbed in-memory capture of :mod:`webhooks.tests.sentry_capture`
and searches the events as sent: messages, extra, breadcrumbs, and the variables of every frame, both of the log
line's stack and of the error's.

The keys, the plaintext and the ciphertext are generated at run time by :func:`_plant` and kept in the module-level
``_PLANT`` only; the calls are lambdas over it, so no frame of this test holds a planted value while Sentry reads the
stack. :func:`test_the_capture_finds_every_planted_value_a_logging_frame_holds` is the control: the same capture and
needles do find every planted value when a frame holds it, so a clean result is not vacuous. Every check is a boolean
computed before its assertion, and the events are dropped first, so a failure prints no planted value.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets

import pytest
import sentry_sdk
from cryptography.fernet import Fernet, InvalidToken

from webhooks import crypto
from webhooks.tests.sentry_capture import (
    frame_vars,
    frames,
    leaked,
    record_text,
    sentry_events,
)

ENV = "WEBHOOKS_ENCRYPTION_KEYS"
MODULE = "webhooks.crypto"
CONTROL_LOGGER = "webhooks.tests.crypto_frame_locals"

# The running case's planted values, written by _plant only: "blob" (the ciphertext as stored), "text" (its
# plaintext), and the needles searched for: "ciphertext" (its text and a middle slice of it), "plaintext" (a marker
# inside it) and "keys" (every entry of the ring, and a key since removed from it).
_PLANT: dict = {}

# Lambdas over the module-level plant: no frame of the test holds the ciphertext while the call logs.
CALLS = {
    "decrypt": lambda: crypto.decrypt(_PLANT["blob"]),
    "decrypt_json": lambda: crypto.decrypt_json(_PLANT["blob"]),
    "rotate": lambda: crypto.rotate(_PLANT["blob"]),
    "on_newest_key": lambda: crypto.on_newest_key(_PLANT["blob"]),
}

# (case, function): each function that logs a ciphertext that did not decrypt, in both such cases; and each function
# that takes a ciphertext, with a malformed ring. on_newest_key logs nothing when the newest key does not decrypt (it
# answers False), so it is in the malformed-ring case only.
CASES = [
    *(
        (case, fn)
        for case in ("corrupt", "removed_key")
        for fn in ("decrypt", "decrypt_json", "rotate")
    ),
    *(
        ("invalid_ring", fn)
        for fn in ("decrypt", "decrypt_json", "rotate", "on_newest_key")
    ),
]


@pytest.fixture(autouse=True)
def _drop_plant():
    yield
    _PLANT.clear()


def _plant(case: str, monkeypatch) -> None:
    """Generate the case's ring, plaintext and ciphertext, set the ring the call will see, and keep the values in
    ``_PLANT``. Nothing is returned, so this frame has ended before the call runs."""
    _PLANT.clear()
    marker = f"pt-{secrets.token_hex(16)}"
    plaintext = json.dumps({"url": f"https://hooks.example.test/{marker}"})
    live = Fernet.generate_key().decode("ascii")
    keys = [live]
    if case == "removed_key":
        removed = Fernet.generate_key().decode("ascii")
        keys.append(removed)
        monkeypatch.setenv(ENV, removed)
        blob = crypto.encrypt(plaintext)
        monkeypatch.setenv(ENV, live)  # its key has left the ring
    else:
        monkeypatch.setenv(ENV, live)
        blob = crypto.encrypt(plaintext)
    if case == "corrupt":
        raw = bytearray(base64.urlsafe_b64decode(blob))
        # The HMAC's last byte: still a well-formed token, but it does not verify.
        raw[-1] ^= 0x01
        blob = base64.urlsafe_b64encode(bytes(raw))
    elif case == "invalid_ring":
        bad = f"not-a-fernet-key-{secrets.token_hex(8)}"
        keys.append(bad)
        monkeypatch.setenv(ENV, f"{live},{bad}")
    token = blob.decode("ascii")
    middle = len(token) // 2
    _PLANT.update(
        blob=blob,
        text=plaintext,
        ciphertext=[token, token[middle - 16 : middle + 16]],
        plaintext=[marker],
        keys=keys,
    )


def _needles() -> list[str]:
    return [*_PLANT["ciphertext"], *_PLANT["plaintext"], *_PLANT["keys"]]


def _report(call):
    """Run ``call`` and send its error to Sentry, as FA's integrations do with an error that escapes a request or an
    activity. Returns the error's type."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        return type(exc)
    raise AssertionError("expected a refusal")


@pytest.mark.parametrize(("case", "fn"), CASES)
def test_a_ciphertext_that_does_not_decrypt_leaves_no_secret_in_sentry(
    case, fn, monkeypatch, caplog
):
    _plant(case, monkeypatch)
    caplog.set_level(logging.INFO, logger=MODULE)
    with sentry_events() as events:
        raised = _report(CALLS[fn])
    right_error = raised is (
        crypto.KeysInvalid if case == "invalid_ring" else InvalidToken
    )
    # The WA6 line, with the stack Sentry attaches to it; and the error, with its frames.
    right_events = sorted(
        "exception" if "exception" in e else str(e.get("logger")) for e in events
    ) == ["exception", MODULE]
    # The function's own frame is in both events with its variables captured, so the search below covers it.
    own = [v for e in events for v in frame_vars(e, MODULE, fn)]
    own_frames_seen = len(own) == len(events) == 2 and all(own)
    no_blob_variable = not any(
        "blob" in (f.get("vars") or {})
        for e in events
        for f in frames(e)
        if f.get("module") == MODULE
    )
    sentry_clean = not leaked(events, _needles())
    logs_clean = not any(
        n in record_text(r) for r in caplog.records for n in _needles()
    )
    one_wa6_line = [
        r.__dict__.get("alert") for r in caplog.records if r.name == MODULE
    ] == ["WA6"]
    del events, own
    assert right_error is True
    assert right_events is True
    assert own_frames_seen is True
    assert no_blob_variable is True
    assert sentry_clean is True
    assert logs_clean is True
    assert one_wa6_line is True


def _log_while_holding() -> bool:
    """The control's frame: it holds every planted value, as decrypt's frame held ``blob``, while an error is
    logged."""
    held_blob = _PLANT["blob"]
    held_text = _PLANT["text"]
    held_keys = ",".join(_PLANT["keys"])
    logging.getLogger(CONTROL_LOGGER).error("control: a frame holds the plant")
    return all((held_blob, held_text, held_keys))


@pytest.mark.parametrize("case", ["corrupt", "removed_key", "invalid_ring"])
def test_the_capture_finds_every_planted_value_a_logging_frame_holds(
    case, monkeypatch, caplog
):
    """The control for the test above: the same capture and needles find every planted value of the case (the
    ciphertext as a ``bytes`` variable, the plaintext and the keys) in the stack of a logged error whose frame holds
    them, so the clean result above is not vacuous."""
    _plant(case, monkeypatch)
    caplog.set_level(logging.INFO, logger=CONTROL_LOGGER)
    with sentry_events() as events:
        _log_while_holding()
    one_event = [e.get("logger") for e in events] == [CONTROL_LOGGER]
    found_every_needle = len(leaked(events, _needles())) == len(_needles())
    del events
    assert one_event is True
    assert found_every_needle is True


def test_the_ciphertext_handoff_keeps_every_success_path(monkeypatch, caplog):
    """What decrypts still decrypts, from bytes or a memoryview, and logs nothing: the same values as before."""
    old = Fernet.generate_key().decode("ascii")
    new = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv(ENV, old)
    blob = crypto.encrypt("x")
    json_blob = crypto.encrypt_json({"a": [1, "b"]})
    monkeypatch.setenv(ENV, f"{new},{old}")
    caplog.set_level(logging.INFO, logger=MODULE)
    with sentry_events() as events:
        results = (
            crypto.decrypt(blob) == "x",
            crypto.decrypt(memoryview(blob)) == "x",
            crypto.decrypt(None) is None,
            crypto.decrypt_json(json_blob) == {"a": [1, "b"]},
            crypto.decrypt_json(None) is None,
            crypto.on_newest_key(blob) is False,
            crypto.on_newest_key(crypto.rotate(blob)) is True,
            crypto.decrypt(crypto.rotate(memoryview(blob))) == "x",
        )
    quiet = len(events) == 0 and not [r for r in caplog.records if r.name == MODULE]
    del events
    assert results == (True,) * 8
    assert quiet is True
