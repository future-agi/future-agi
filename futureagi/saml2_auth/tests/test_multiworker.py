"""Opt-in, two-process checks for the database-backed SAML protocol.

The normal suite deliberately does not start ASGI workers.  These tests run
only with the two Granian processes described in ``test-matrix.md`` so they
can prove that the attempt store and TX-B locks are not process-local.
"""

from __future__ import annotations

import base64
import os
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest
import requests
from django.conf import settings
from django.db import close_old_connections, connection, transaction

from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from saml2_auth.models import SamlLoginAttempt, SAMLMetadataModel
from saml2_auth.tests.saml_fixtures import signed_response

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.integration,
    pytest.mark.multiworker,
]


@dataclass(frozen=True)
class LiveAttempt:
    session: requests.Session
    relay_key: str
    request_id: str
    candidate_key: str


def _worker_urls() -> tuple[str, str]:
    """Return the explicitly provisioned worker URLs, or skip by default."""

    if os.getenv("SAML_MULTIWORKER") != "1":
        pytest.skip("set SAML_MULTIWORKER=1 to run the two-process SAML suite")
    raw_urls = os.getenv("SAML_WORKER_URLS", "")
    urls = tuple(url.strip().rstrip("/") for url in raw_urls.split(",") if url.strip())
    if len(urls) != 2:
        pytest.skip("SAML_WORKER_URLS must contain exactly two ASGI worker URLs")
    return urls  # type: ignore[return-value]


def _request_id(redirect_url: str) -> tuple[str, str]:
    query = parse_qs(urlparse(redirect_url).query)
    relay_key = query["RelayState"][0]
    compressed = base64.b64decode(query["SAMLRequest"][0])
    request_xml = zlib.decompress(compressed, -zlib.MAX_WBITS)
    return relay_key, ElementTree.fromstring(request_xml).attrib["ID"]


def _candidate_key(response: requests.Response) -> str:
    assert response.status_code == 303, response.text
    keys = parse_qs(urlparse(response.headers["Location"]).query).get("c", [])
    assert len(keys) == 1
    return keys[0]


def _post_to_other_worker(
    urls: tuple[str, str], *, email: str, idp_key, recipient: str
) -> LiveAttempt:
    """Initiate on worker one, put the candidate on worker two, complete on one."""

    session = requests.Session()
    initiated = session.get(
        f"{urls[0]}/saml2_auth/idp-login/", params={"email": email}, timeout=15
    )
    assert initiated.status_code == 200, initiated.text
    relay_key, request_id = _request_id(initiated.json()["result"]["url"])
    payload = signed_response(
        idp_key,
        request_id=request_id,
        recipient=recipient,
        destination=recipient,
        audience=urls[0],
        subject_email=email,
    )
    candidate = session.post(
        f"{urls[1]}/saml2_auth/acs/",
        data={"RelayState": relay_key, "SAMLResponse": payload},
        allow_redirects=False,
        timeout=15,
    )
    return LiveAttempt(session, relay_key, request_id, _candidate_key(candidate))


def _complete(url: str, attempt: LiveAttempt) -> requests.Response:
    return attempt.session.get(
        f"{url}/saml2_auth/complete/",
        params={"c": attempt.candidate_key},
        allow_redirects=False,
        timeout=30,
    )


def _wait_for_advisory_wait(timeout: float = 10) -> None:
    """Wait until a worker is parked on the test-only TX-B advisory barrier."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND wait_event_type = 'Lock'
                  AND wait_event = 'advisory'
                """
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.05)
    pytest.fail("a SAML worker did not reach the requested TX-B barrier")


def _hold_barrier(stage: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_lock(hashtext(%s))", [f"saml_tx_b_barrier:{stage}"]
        )


def _release_barrier(stage: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))", [f"saml_tx_b_barrier:{stage}"]
        )


def _in_worker_thread(action: Callable[[], None]) -> None:
    close_old_connections()
    try:
        action()
    finally:
        close_old_connections()


def _replace_metadata(idp_id: str) -> None:
    with transaction.atomic():
        idp = SAMLMetadataModel.no_workspace_objects.select_for_update().get(id=idp_id)
        idp.meta = f"{idp.meta}\n"
        idp.security_generation += 1
        idp.save(update_fields=["meta", "security_generation"])
        AuthToken.no_workspace_objects.filter(origin_idp=idp, is_active=True).update(
            is_active=False
        )


def _disable_idp(idp_id: str) -> None:
    with transaction.atomic():
        idp = SAMLMetadataModel.no_workspace_objects.select_for_update().get(id=idp_id)
        idp.is_enabled = False
        idp.security_generation += 1
        idp.save(update_fields=["is_enabled", "security_generation"])
        AuthToken.no_workspace_objects.filter(origin_idp=idp, is_active=True).update(
            is_active=False
        )


def _deactivate_user(user_id: str) -> None:
    User.objects.filter(id=user_id).update(is_active=False)


def _soft_delete_membership(user_id: str, organization_id: str) -> None:
    OrganizationMembership.no_workspace_objects.filter(
        user_id=user_id, organization_id=organization_id
    ).update(deleted=True)


def _hard_delete_membership(user_id: str, organization_id: str) -> None:
    OrganizationMembership.no_workspace_objects.filter(
        user_id=user_id, organization_id=organization_id
    ).delete()


def test_attempt_store_shared_across_workers(saml_tenants, idp_a, idp_keypairs):
    """AC-CORR-12: worker two can persist what worker one completes."""

    urls = _worker_urls()
    attempt = _post_to_other_worker(
        urls,
        email=saml_tenants.a2.email,
        idp_key=idp_keypairs["a"],
        recipient=f"{urls[0]}/saml2_auth/acs/",
    )

    response = _complete(urls[0], attempt)

    assert response.status_code == 302
    assert "sso_token=" in response.headers["Location"]
    assert SamlLoginAttempt.objects.get(relay_key=attempt.relay_key).state == "consumed"
    assert (
        AuthToken.no_workspace_objects.filter(
            user=saml_tenants.a2, auth_origin="saml", is_active=True
        ).count()
        == 1
    )


def test_concurrent_duplicate_post_single_issuance(saml_tenants, idp_a, idp_keypairs):
    """AC-CORR-09: duplicate ACS candidates still linearize at one token."""

    urls = _worker_urls()
    session = requests.Session()
    initiated = session.get(
        f"{urls[0]}/saml2_auth/idp-login/",
        params={"email": saml_tenants.a2.email},
        timeout=15,
    )
    relay_key, request_id = _request_id(initiated.json()["result"]["url"])
    payload = signed_response(
        idp_keypairs["a"],
        request_id=request_id,
        recipient=f"{urls[0]}/saml2_auth/acs/",
        destination=f"{urls[0]}/saml2_auth/acs/",
        audience=urls[0],
        subject_email=saml_tenants.a2.email,
    )

    def post_candidate(worker_url: str) -> str:
        response = requests.post(
            f"{worker_url}/saml2_auth/acs/",
            data={"RelayState": relay_key, "SAMLResponse": payload},
            allow_redirects=False,
            timeout=15,
        )
        return _candidate_key(response)

    with ThreadPoolExecutor(max_workers=2) as pool:
        candidates = list(pool.map(post_candidate, urls))
    assert len(set(candidates)) == 2

    def copied_session() -> requests.Session:
        copied = requests.Session()
        copied.cookies.update(session.cookies)
        return copied

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda candidate_key: copied_session().get(
                    f"{urls[0]}/saml2_auth/complete/",
                    params={"c": candidate_key},
                    allow_redirects=False,
                    timeout=30,
                ),
                candidates,
            )
        )

    assert (
        sum("sso_token=" in item.headers.get("Location", "") for item in outcomes) == 1
    )
    attempt = SamlLoginAttempt.objects.get(relay_key=relay_key)
    assert attempt.state == "consumed"
    assert attempt.claimed_at is not None
    assert (
        AuthToken.no_workspace_objects.filter(
            user=saml_tenants.a2, auth_origin="saml"
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    ("stage", "mutation"),
    [
        ("after_idp", "replace_metadata"),
        ("after_idp", "disable_idp"),
        ("after_user", "deactivate_user"),
        ("after_member", "soft_delete_membership"),
        ("after_member", "hard_delete_membership"),
    ],
)
def test_tx_b_interleavings_leave_no_usable_credential(
    saml_tenants, idp_a, idp_keypairs, stage, mutation
):
    """AC-SESSION-06: every lock-order interleaving fails closed on first use."""

    urls = _worker_urls()
    assert settings.SAML_LOGIN_ENABLED
    attempt = _post_to_other_worker(
        urls,
        email=saml_tenants.a2.email,
        idp_key=idp_keypairs["a"],
        recipient=f"{urls[0]}/saml2_auth/acs/",
    )
    _hold_barrier(stage)
    complete_result: list[requests.Response] = []

    def complete_in_thread() -> None:
        complete_result.append(_complete(urls[0], attempt))

    def mutate() -> None:
        if mutation == "replace_metadata":
            _replace_metadata(idp_a.id)
        elif mutation == "disable_idp":
            _disable_idp(idp_a.id)
        elif mutation == "deactivate_user":
            _deactivate_user(str(saml_tenants.a2.id))
        elif mutation == "soft_delete_membership":
            _soft_delete_membership(str(saml_tenants.a2.id), str(saml_tenants.org_a.id))
        elif mutation == "hard_delete_membership":
            _hard_delete_membership(str(saml_tenants.a2.id), str(saml_tenants.org_a.id))
        else:  # pragma: no cover - parametrization is local and exhaustive.
            raise AssertionError(f"unknown mutation {mutation}")

    barrier_released = False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            complete_future = pool.submit(_in_worker_thread, complete_in_thread)
            _wait_for_advisory_wait()
            mutation_future = pool.submit(_in_worker_thread, mutate)
            # The worker still holds the protected row while parked at the barrier.
            assert not mutation_future.done()
            _release_barrier(stage)
            barrier_released = True
            complete_future.result(timeout=30)
            mutation_future.result(timeout=30)
    finally:
        if not barrier_released:
            _release_barrier(stage)

    assert len(complete_result) == 1
    token_response = complete_result[0]
    token = parse_qs(urlparse(token_response.headers["Location"]).query).get(
        "sso_token", [None]
    )[0]
    if token:
        checked = requests.get(
            f"{urls[1]}/accounts/user-info/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        assert checked.status_code in {401, 403}
    active_token = AuthToken.no_workspace_objects.filter(
        user=saml_tenants.a2, auth_origin="saml", is_active=True
    )
    if stage == "after_idp":
        assert not active_token.exists()
    else:
        # User and membership mutations intentionally do not bulk-revoke: the
        # DB-authoritative validator rejects this row before it can be used.
        assert token is not None
