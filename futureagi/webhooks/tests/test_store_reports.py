"""What the Postgres store's error reports carry (K2; DESIGN.md section 5.3). Needs the FA test database.

One part of an endpoint's configuration does not decrypt after others did: its signing secret, or its custom headers
(decrypted last, after the URL and the secrets). Two error log lines follow (the decrypt failure and WA6's
"configuration could not be read"), and Sentry attaches the stack, with each frame's variables, to both. Captured
unscrubbed (``sentry_capture``), neither event holds the URL's token, a header value or a secret, because no frame
variable ever holds a decrypted value (``store_rows.endpoint_config``). Before, the decrypted headers sat in a local
named ``secret_headers``, which only a name-based scrubber kept out of Sentry.
"""

from __future__ import annotations

import time

import pytest

from webhooks import crypto
from webhooks.models import Delivery, Endpoint, Secret
from webhooks.store import OrmStore
from webhooks.tests.factories import make_endpoint, use_test_keys
from webhooks.tests.sentry_capture import frame_vars, leaked, sentry_events
from webhooks.tests.test_store import pub

URL_MARK = "u-tok-c81d"
HEADER_MARK = "hv-k2-0b6e"
NOT_FERNET = b"gAAAAAB-not-a-fernet-token"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("broken", ["secret", "headers"])
def test_a_configuration_that_fails_to_decrypt_reports_no_decrypted_value(
    broken, organization, workspace, monkeypatch
):
    use_test_keys(monkeypatch)
    ep = make_endpoint(
        organization, workspace, url=f"https://hooks.acme.ai/omega?token={URL_MARK}"
    )
    Endpoint.objects.filter(id=ep.id).update(
        headers_encrypted=crypto.encrypt_json(
            {"Authorization": f"Bearer {HEADER_MARK}"}
        )
    )
    if broken == "secret":
        Secret.objects.filter(endpoint=ep).update(secret_encrypted=NOT_FERNET)
    else:
        Endpoint.objects.filter(id=ep.id).update(headers_encrypted=NOT_FERNET)
    pub(organization, workspace, 1)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    with sentry_events() as events:
        assert store.claim_due(now, "normal", 48, 16) == []
    assert sorted(e.get("logger") for e in events) == [
        "webhooks.crypto",
        "webhooks.store",
    ]
    # The decrypt failure's stack holds endpoint_config's frame, with its variables captured: none is a decrypted
    # value, though the URL (and, for broken headers, the secrets) had been decrypted by then.
    (decrypt_failed,) = [e for e in events if e.get("logger") == "webhooks.crypto"]
    (config_vars,) = frame_vars(
        decrypt_failed, "webhooks.store_rows", "endpoint_config"
    )
    assert set(config_vars) == {"ep", "now", "decrypt", "secret_rows"}
    assert leaked(events, [URL_MARK, HEADER_MARK, "whsec_"]) == []
    dl = Delivery.objects.get(endpoint=ep)
    assert (dl.status, dl.attempts_count) == (
        "pending",
        0,
    )  # released alone, due again (WA6)
