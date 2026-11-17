"""WT-27 (DESIGN.md sections 5.3 and 17): secrets at rest under ``WEBHOOKS_ENCRYPTION_KEYS``, not the integration
key; a new key plus ``webhooks_reencrypt`` leaves 0 rows on the old one and delivery still verifies; ``whsec_`` never
appears in a log line; a ciphertext no key decrypts is counted and left alone."""

from __future__ import annotations

import logging
import threading
import time
from io import StringIO

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core.management import CommandError, call_command
from django.db import close_old_connections, transaction

from webhooks import crypto, reencrypt
from webhooks.core.signing import verify
from webhooks.models import Endpoint, Secret
from webhooks.publish import publish
from webhooks.store_rows import endpoint_config
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint
from webhooks.tests.receiver import Receiver, real_dispatcher

SECRET = "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def _columns(ep: Endpoint) -> list[bytes]:
    ep.refresh_from_db()
    blobs = [ep.url_encrypted, ep.headers_encrypted]
    blobs += [s.secret_encrypted for s in Secret.objects.filter(endpoint=ep)]
    return [bytes(b) for b in blobs if b is not None]


@pytest.mark.django_db(transaction=True)
def test_wt27_rotation_reencrypts_everything_and_delivery_still_verifies(
    organization, workspace, monkeypatch, receiver, caplog
):
    caplog.set_level(logging.DEBUG)
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    integration = Fernet.generate_key()
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", integration.decode())
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", k1.decode())
    ep = make_endpoint(
        organization,
        workspace,
        url=receiver.url("/ok"),
        secret=SECRET,
        rate_limit_per_s=100,
    )
    Endpoint.objects.filter(id=ep.id).update(
        headers_encrypted=crypto.encrypt_json({"Authorization": "Bearer hv-canary-27"})
    )
    for blob in _columns(ep):
        assert blob.startswith(b"gAAAAA")  # a Fernet token
        Fernet(k1).decrypt(blob)
        with pytest.raises(InvalidToken):
            Fernet(integration).decrypt(blob)  # never the integration key (K2)

    # Rotate: the new key first, the old one still decrypts.
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{k2.decode()},{k1.decode()}")
    before = reencrypt.count_left()
    assert before["total"] == 3
    out = reencrypt.run(chunk=1)
    assert (out["rewritten"], out["undecryptable"], out["left"]["total"]) == (3, 0, 0)
    for blob in _columns(ep):
        Fernet(k2).decrypt(blob)
        with pytest.raises(InvalidToken):
            Fernet(k1).decrypt(blob)
    buf = StringIO()
    call_command("webhooks_reencrypt", stdout=buf)
    assert "left on older keys 0" in buf.getvalue()

    # The old key is removed from the list: the configuration still decrypts and a real delivery verifies.
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", k2.decode())
    from datetime import UTC, datetime

    cfg = endpoint_config(Endpoint.objects.get(id=ep.id), datetime.now(UTC))
    assert cfg.secrets_newest_first == (SECRET,)
    assert dict(cfg.custom_headers) == {"Authorization": "Bearer hv-canary-27"}
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=organization.id,
            workspace_id=workspace.id,
            data=label_event_data(),
            source_key="k1",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )
    d = real_dispatcher(receiver)
    stop = threading.Event()

    def loop():
        try:
            d.run(stop, run_for_s=3.0)
        finally:
            close_old_connections()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    t.join(30)
    (req,) = [r for r in receiver.requests if r["path"] == "/ok"]
    verdict = verify(SECRET, req["headers"], req["body"], int(time.time()))
    evidence.write(
        "WT-27",
        at_rest="URL, headers and the secret are Fernet tokens under WEBHOOKS_ENCRYPTION_KEYS, none under "
        "INTEGRATION_ENCRYPTION_KEY",
        left_on_older_keys_before=before,
        reencrypt_chunk_1=out,
        after_old_key_removed={
            "delivery_verified": verdict,
            "custom_header_arrived": "authorization" in req["headers"],
        },
        log_records_checked=len(caplog.records),
        receiver=evidence.receiver_lines(receiver),
    )
    assert verdict == "ok"
    assert req["headers"]["authorization"] == "Bearer hv-canary-27"
    # No secret in any log line of this whole test (WT-27): not the whsec_ string, not a key, not the header value.
    for r in caplog.records:
        text = r.getMessage() + repr(r.__dict__)
        assert "whsec_" not in text
        assert "hv-canary-27" not in text
        for k in (k1, k2, integration):
            assert k.decode() not in text


@pytest.mark.django_db(transaction=True)
def test_a_ciphertext_no_key_decrypts_is_counted_and_left_alone(
    organization, workspace, monkeypatch
):
    k1, stray = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", k1.decode())
    ep = make_endpoint(organization, workspace)
    foreign = Fernet(stray).encrypt(b"https://elsewhere.example/x")
    Endpoint.objects.filter(id=ep.id).update(url_encrypted=foreign)
    k2 = Fernet.generate_key()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{k2.decode()},{k1.decode()}")
    out = reencrypt.run()
    assert out["rewritten"] == 1  # the secret
    assert out["undecryptable"] == 1  # the URL under a key not in the list
    assert out["left"]["webhooks_endpoint.url_encrypted"] == 1
    ep.refresh_from_db()
    assert bytes(ep.url_encrypted) == foreign
    with pytest.raises(CommandError):
        call_command("webhooks_reencrypt", stdout=StringIO())


@pytest.mark.django_db(transaction=True)
def test_dry_run_counts_only(organization, workspace, monkeypatch):
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", k1.decode())
    ep = make_endpoint(organization, workspace)
    before = _columns(ep)
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", f"{k2.decode()},{k1.decode()}")
    out = reencrypt.run(dry_run=True)
    assert out["rewritten"] == 0 and out["left"]["total"] == 2
    ep.refresh_from_db()
    assert [bytes(ep.url_encrypted)] + [
        bytes(s.secret_encrypted) for s in Secret.objects.filter(endpoint=ep)
    ] == before
