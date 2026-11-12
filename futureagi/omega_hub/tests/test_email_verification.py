"""HUB-25: likes, reports and new publishers need a confirmed email (SPEC §6.1, decision 13).

An unverified account's like answers 403 `OMEGA_HUB_EMAIL_UNVERIFIED`; after it opens the link the
Hub mailed, the same like answers 200.
"""

import re

import pytest
from django.core import mail

from omega_hub.models import AuditEvent, EmailVerification, Like, Publisher, Report
from omega_hub.services import email_verification
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import make_person, session_client, token_client

pytestmark = pytest.mark.django_db
LIKE = "/omega-hub/v1/entries/future-agi/first-agent/like"
SEND = "/omega-hub/v1/me/email-verification"
CONFIRM = "/omega-hub/v1/me/email-verification/confirm"


def _code(response):
    return response.json()["error"]["code"]


def _mails_to(address: str) -> list[tuple[str, str]]:
    """(subject, body) of every confirmation email to `address`: delivered ones in Django's test
    outbox and, once the Hub's durable email outbox exists (HUB-OPS), the rows queued in it."""
    delivered = [(m.subject, m.body) for m in mail.outbox if address in m.to]
    if delivered:
        return delivered
    try:
        from omega_hub.models import EmailOutbox
    except ImportError:
        return []
    rows = EmailOutbox.objects.filter(template="verify_email").order_by("created_at")
    return [(row.subject, row.body) for row in rows if address in row.recipients]


def _token_from_mail(address: str) -> str:
    sent = _mails_to(address)
    assert len(sent) == 1, sent
    subject, text = sent[0]
    assert subject == "Omega Hub: confirm your email address"
    link = re.search(r"https?://\S+/verify-email\?token=(\S+)", text)
    assert link, text
    return link.group(1)


def test_an_unverified_account_is_refused_then_confirms_and_likes(world, django_capture_on_commit_callbacks):
    publish(world)
    uma = make_person("uma", email_verified=False)
    client = session_client(uma)
    assert client.get("/omega-hub/v1/me").json()["emailVerified"] is False

    refused = client.post(LIKE)
    assert refused.status_code == 403 and _code(refused) == "OMEGA_HUB_EMAIL_UNVERIFIED"
    report = client.post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "broken",
                                                   "details": "It crashes on start with a stack trace."}, format="json")
    assert report.status_code == 403 and _code(report) == "OMEGA_HUB_EMAIL_UNVERIFIED"
    created = client.post("/omega-hub/v1/publishers", {"handle": "uma-tools", "kind": "user", "displayName": "Uma"},
                          format="json")
    assert created.status_code == 403 and _code(created) == "OMEGA_HUB_EMAIL_UNVERIFIED"
    assert not Like.objects.exists() and not Report.objects.exists()
    assert not Publisher.objects.filter(handle="uma-tools").exists()

    with django_capture_on_commit_callbacks(execute=True):
        sent = client.post(SEND)
    assert sent.status_code == 202 and sent.json() == {"verified": False, "sent": True, "sentTo": uma.email}
    token = _token_from_mail(uma.email)

    confirmed = client.post(CONFIRM, {"token": token}, format="json")
    assert confirmed.status_code == 200 and confirmed.json() == {"verified": True}
    assert client.get("/omega-hub/v1/me").json()["emailVerified"] is True
    liked = client.post(LIKE)
    assert liked.status_code == 200 and liked.json() == {"likes": 1, "likedByMe": True}
    assert AuditEvent.objects.filter(action="email.verified", target_id=str(uma.user.pk)).count() == 1
    assert client.post(CONFIRM, {"token": token}, format="json").json() == {"verified": True}  # idempotent

    with django_capture_on_commit_callbacks(execute=True):
        again = client.post(SEND)
    assert again.status_code == 200 and again.json() == {"verified": True, "sent": False}


def test_a_link_works_only_for_its_account_and_address(world, django_capture_on_commit_callbacks):
    uma = make_person("uma", email_verified=False)
    vic = make_person("vic", email_verified=False)
    with django_capture_on_commit_callbacks(execute=True):
        session_client(uma).post(SEND)
    token = _token_from_mail(uma.email)

    other = session_client(vic).post(CONFIRM, {"token": token}, format="json")
    assert other.status_code == 400 and _code(other) == "OMEGA_HUB_EMAIL_LINK_INVALID"
    tampered = session_client(uma).post(CONFIRM, {"token": token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]},
                                        format="json")
    assert tampered.status_code == 400 and _code(tampered) == "OMEGA_HUB_EMAIL_LINK_INVALID"
    assert session_client(uma).post(CONFIRM, {}, format="json").status_code == 400

    uma.user.email = f"new-{uma.user.email}"
    uma.user.save(update_fields=["email"])
    moved = session_client(uma).post(CONFIRM, {"token": token}, format="json")
    assert moved.status_code == 400 and _code(moved) == "OMEGA_HUB_EMAIL_LINK_INVALID"
    assert not EmailVerification.objects.filter(user=uma.user).exists()


def test_an_expired_link_is_refused(world, monkeypatch, django_capture_on_commit_callbacks):
    uma = make_person("uma", email_verified=False)
    with django_capture_on_commit_callbacks(execute=True):
        session_client(uma).post(SEND)
    token = _token_from_mail(uma.email)
    monkeypatch.setattr(email_verification, "MAX_AGE_SECONDS", -1)
    expired = session_client(uma).post(CONFIRM, {"token": token}, format="json")
    assert expired.status_code == 400 and _code(expired) == "OMEGA_HUB_EMAIL_LINK_EXPIRED"


def test_changing_the_email_needs_a_new_confirmation(world):
    publish(world)
    carol = world.carol.user
    assert world.c.post(LIKE).status_code == 200
    world.c.delete(LIKE)
    carol.email = f"moved-{carol.email}"
    carol.save(update_fields=["email"])
    refused = world.c.post(LIKE)
    assert refused.status_code == 403 and _code(refused) == "OMEGA_HUB_EMAIL_UNVERIFIED"


def test_only_a_signed_in_person_can_ask_for_a_link(world):
    by_token = token_client(world.token_c).post(SEND)
    assert by_token.status_code == 403 and _code(by_token) == "OMEGA_HUB_TOKEN_SCOPE"
    anon = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient().post(SEND)
    assert anon.status_code == 401


def test_sending_links_is_throttled(world, monkeypatch, django_capture_on_commit_callbacks):
    from omega_hub.throttles import HubVerifyEmailThrottle

    monkeypatch.setattr(HubVerifyEmailThrottle, "rate", "2/hour")
    uma = make_person("uma", email_verified=False)
    client = session_client(uma)
    with django_capture_on_commit_callbacks(execute=True):
        codes = [client.post(SEND).status_code for _ in range(3)]
    assert codes == [202, 202, 429]
    assert len(_mails_to(uma.email)) == 2
