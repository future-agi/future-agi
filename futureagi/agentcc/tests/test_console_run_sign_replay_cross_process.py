"""Replay protection in a fresh interpreter sharing the committed test database.

A fresh interpreter cannot inherit a process-local replay cache. No nonce store,
signature verification or signer is mocked. Only synthetic test values cross the
subprocess's stdin; the worker reports statuses and booleans, never credentials.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import connections, transaction
from rest_framework.test import APIRequestFactory

from accounts.models.envelope_nonce import EnvelopeNonce
from accounts.services.service_grants import issue_person_grant
from agentcc.services.console_run_signer_keys import add_key
from agentcc.services.delegated_availability import CONSOLE_RUN_SWITCH

pytestmark = pytest.mark.django_db(transaction=True)

CHAT_ROUTE = "/-/delegated/v1/chat/completions"
SIGN_ROUTE = "/agentcc/console-run/sign/"
CALL_BODY = b'{"model":"gpt-4o-mini","messages":[]}'
MODELS = ["gpt-4o-mini"]


@pytest.fixture(autouse=True)
def configured(settings, monkeypatch):
    settings.SECRET_KEY = "test_only_fa_signing_key_for_console_run_0000"
    settings.AGENTCC_ADMIN_TOKEN = "test-only-console-run-sign-admin-token-0123456789"
    monkeypatch.delenv(CONSOLE_RUN_SWITCH, raising=False)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def ee_key():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    add_key(deployment="ee-prod-1", kid="ee-k1", public_key=b64(raw), operator="ops")
    return private


def authorization(user, workspace):
    with transaction.atomic():
        token, _, _ = issue_person_grant(
            kind="console-run-auth",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="chat:" + str(uuid.uuid4()),
            ttl=3600,
            models=MODELS,
            ee="ee-prod-1",
        )
    return token


def sign_body(token):
    return json.dumps(
        {
            "run_authorization": token,
            "route": CHAT_ROUTE,
            "method": "POST",
            "body_sha256": hashlib.sha256(CALL_BODY).hexdigest(),
            "model": "gpt-4o-mini",
        },
        separators=(",", ":"),
    ).encode()


def assertion(private, raw, *, jti, now):
    header = {"alg": "EdDSA", "kid": "ee-k1", "typ": "omega-ee-assertion+jwt"}
    claims = {
        "iss": "ee-prod-1",
        "aud": "fa-console-run-signer",
        "htu": SIGN_ROUTE,
        "htm": "POST",
        "iat": now,
        "exp": now + 60,
        "jti": jti,
        "bodyhash": hashlib.sha256(raw).hexdigest(),
    }
    signing_input = (
        b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
    )
    return signing_input + "." + b64(private.sign(signing_input.encode("ascii")))


def post(raw, token):
    from agentcc.views.console_run import ConsoleRunSignView

    request = APIRequestFactory().post(
        SIGN_ROUTE,
        data=raw,
        content_type="application/json",
        HTTP_X_OMEGA_EE_ASSERTION=token,
    )
    return ConsoleRunSignView.as_view()(request)


def refusal(response):
    assert response.status_code == 401, response.data
    assert response.data["code"] == "assertion_rejected"
    assert response["Cache-Control"] == "no-store"
    return response.status_code, response.data["code"], response["Cache-Control"]


def nonce_exp(jti):
    return (
        EnvelopeNonce.objects.using("default").get(aud="fa-eval", id="ccr:" + jti).exp
    )


# Runs only on the cloud rig, under the same Python environment as pytest.
REPLAY_WORKER = """
import json
import os
import sys

payload = json.load(sys.stdin)
os.environ['DJANGO_SETTINGS_MODULE'] = payload['settings_module']
from django.conf import settings
settings.DATABASES = {'default': payload['database']}
settings.SECRET_KEY = payload['secret_key']
settings.AGENTCC_ADMIN_TOKEN = payload['admin_token']
settings.AGENTCC_DELEGATED_CONSOLE_RUN_ENABLED = True
import django
django.setup()
from django.db import connections
from rest_framework.test import APIRequestFactory
from accounts.models.envelope_nonce import EnvelopeNonce
from agentcc.views.console_run import ConsoleRunSignView

def call(assertion):
    request = APIRequestFactory().post(
        '/agentcc/console-run/sign/',
        data=payload['body'].encode(),
        content_type='application/json',
        HTTP_X_OMEGA_EE_ASSERTION=assertion,
    )
    return ConsoleRunSignView.as_view()(request)

try:
    before = EnvelopeNonce.objects.get(
        aud='fa-eval', id='ccr:' + payload['replayed_jti']
    ).exp
    replay = call(payload['once'])
    fresh = call(payload['fresh'])
    result = {
        'pid': os.getpid(),
        'seen_exp': before,
        'replay_status': replay.status_code,
        'replay_code': replay.data.get('code'),
        'replay_cache': replay.get('Cache-Control'),
        'fresh_status': fresh.status_code,
        'same_grant': fresh.data.get('result', {}).get('grant')
            == payload['expected_grant'],
        'fresh_nonce_present': EnvelopeNonce.objects.filter(
            aud='fa-eval', id='ccr:' + payload['fresh_jti']
        ).exists(),
    }
    print('REPLAY_RESULT ' + json.dumps(result))
finally:
    connections.close_all()
"""


def test_replayed_assertion_is_refused_in_a_fresh_process(
    user, workspace, ee_key, settings
):
    raw = sign_body(authorization(user, workspace))
    now = int(time.time())
    replayed_jti = b64(os.urandom(16))
    fresh_jti = b64(os.urandom(16))
    assert fresh_jti != replayed_jti
    once = assertion(ee_key, raw, jti=replayed_jti, now=now)
    # Identical lifetime and body: expiry cannot explain only the replay failing.
    fresh = assertion(ee_key, raw, jti=fresh_jti, now=now)

    main_connection = connections["default"]
    assert main_connection.get_autocommit()
    assert not main_connection.in_atomic_block
    assert not (
        EnvelopeNonce.objects.using("default")
        .filter(aud="fa-eval", id__in=["ccr:" + replayed_jti, "ccr:" + fresh_jti])
        .exists()
    )

    first = post(raw, once)
    assert first.status_code == 200, first.data
    assert first.data["result"]["grant"].startswith("fe1c.")
    assert first.data["result"]["envelope"].startswith("fe1.")
    assert main_connection.get_autocommit()
    assert not main_connection.in_atomic_block
    assert nonce_exp(replayed_jti) == now + 60
    same_connection_refusal = refusal(post(raw, once))

    # A fresh interpreter has neither this process's imports nor a replay cache.
    # Pass the actual committed TEST database, never the default application DB.
    database = dict(main_connection.settings_dict)
    database["OPTIONS"] = {
        **database.get("OPTIONS", {}),
        "connect_timeout": 5,
        "options": "-c statement_timeout=10000 -c lock_timeout=5000",
    }
    payload = {
        "settings_module": settings.SETTINGS_MODULE or "tfc.settings.test",
        "database": database,
        "secret_key": settings.SECRET_KEY,
        "admin_token": settings.AGENTCC_ADMIN_TOKEN,
        "body": raw.decode(),
        "once": once,
        "fresh": fresh,
        "replayed_jti": replayed_jti,
        "fresh_jti": fresh_jti,
        "expected_grant": first.data["result"]["grant"],
    }
    worker = subprocess.run(
        [sys.executable, "-c", REPLAY_WORKER],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert worker.returncode == 0, "The separate signer worker failed"
    reports = [
        line.removeprefix("REPLAY_RESULT ")
        for line in worker.stdout.splitlines()
        if line.startswith("REPLAY_RESULT ")
    ]
    assert len(reports) == 1, "The separate worker did not report its result"
    report = json.loads(reports[0])
    assert report["pid"] != os.getpid()
    assert report["seen_exp"] == now + 60
    assert (
        report["replay_status"],
        report["replay_code"],
        report["replay_cache"],
    ) == same_connection_refusal
    assert report["fresh_status"] == 200
    assert report["same_grant"] is True
    assert report["fresh_nonce_present"] is True

    # The worker's successful sign also leaves a nonce visible to the first caller.
    assert nonce_exp(replayed_jti) == now + 60
    assert nonce_exp(fresh_jti) == now + 60
    assert refusal(post(raw, fresh)) == same_connection_refusal
