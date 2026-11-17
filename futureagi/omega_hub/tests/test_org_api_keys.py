"""Historical organization keys are refused before Hub permission checks.

A personal key still cannot stand in for a reviewer's interactive session.
Legacy system keys never resolve to the organization's first user, even on
public reads; anonymous requests without invalid credentials remain public.
"""

import json
import re
import uuid

import pytest
from django.urls import get_resolver
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from accounts.models import OrgApiKey
from omega_hub.api.base import HubView
from omega_hub.models import AuditEvent, HubToken, Submission, Version
from omega_hub.permissions import is_maintainer
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import (
    approve_payload,
    entry_files,
    make_person,
    run_checks,
    session_client,
    submit,
    token_client,
)
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
SESSION_REQUIRED = "OMEGA_HUB_SESSION_REQUIRED"
DISCLOSURE = json.loads(entry_files("first-agent")["omega.hub.json"])["disclosure"]
ENTRY = "/omega-hub/v1/entries/future-agi/first-agent"
METHODS = ("get", "post", "patch", "delete")
# SPEC §9.2, by URL name: the only requests an org key may make, and it makes them as nobody.
PUBLIC_READS = {
    ("entries", "get"),
    ("entry", "get"),
    ("entry-versions", "get"),
    ("version", "get"),
    ("version-file", "get"),
    ("version-bundle", "get"),
    ("version-cast", "get"),
    ("entry-remixes", "get"),
    ("publishers", "get"),
    ("publisher", "get"),
    ("publisher-avatar", "get"),
    ("picks", "get"),
    ("tags", "get"),
    ("export", "get"),
    ("rules", "get"),
    ("blocklist", "get"),
}


def make_key(person, key_type: str) -> OrgApiKey:
    from accounts.tests.personal_keys import personal_key_for

    tag = uuid.uuid4().hex[:12]
    fields = {
        "api_key": f"ak-{tag}",
        "secret_key": f"sk-{tag}",
        "workspace": person.workspace,
    }
    if key_type == "user":
        return personal_key_for(person.user, person.organization, **fields)
    return OrgApiKey.objects.create(
        organization=person.organization, type=key_type, user=None, **fields
    )


def key_client(key: OrgApiKey) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def resolved_user(key: OrgApiKey):
    """Who the platform's own APIKeyAuthentication says the key is, before the Hub has a say."""
    request = Request(
        APIRequestFactory().get(
            "/omega-hub/v1/me",
            HTTP_X_API_KEY=key.api_key,
            HTTP_X_SECRET_KEY=key.secret_key,
        )
    )
    try:
        user, _ = APIKeyAuthentication().authenticate(request)
    finally:
        clear_workspace_context()
    return user


def refusal(response) -> tuple[int, str | None]:
    try:
        return response.status_code, response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return response.status_code, None


@pytest.fixture
def system_key(world):
    """Seed the historical pair without hiding behavioral assertions in setup."""
    return make_key(world.maint_a, "system")


def test_system_key_admission_is_retired(system_key):
    """The historical maintainer pair must fail admission, not name a person."""
    from accounts.api_key_policy import APIKeyRetired

    with pytest.raises(APIKeyRetired) as retired:
        resolved_user(system_key)
    assert retired.value.get_codes() == "api_key_retired"
    assert retired.value.create_url == "/dashboard/keys"


def test_the_system_key_cannot_approve_a_members_own_submission(world, system_key):
    member = make_person(
        "member-m",
        org_level=Level.MEMBER,
        workspace_level=Level.WORKSPACE_MEMBER,
        organization=world.maint_org,
    )
    m = session_client(member)
    created_pub = m.post(
        "/omega-hub/v1/publishers",
        {"handle": "m-pub", "kind": "user", "displayName": "M"},
        format="json",
    )
    assert created_pub.status_code == 201, created_pub.content
    files = entry_files("first-agent")
    created = submit(m, "m-pub", files, name="first-agent", version="1.0.0")
    assert created.status_code == 201, created.content
    _, report, result = run_checks(files)
    assert result["submission"] == "in_review", result
    sub_id = created.json()["id"]
    decision = f"/omega-hub/v1/review/submissions/{sub_id}/decision"
    payload = approve_payload(report, disclosure=DISCLOSURE)

    assert refusal(m.post(decision, payload, format="json")) == (
        403,
        "OMEGA_HUB_NOT_MAINTAINER",
    )
    assert refusal(key_client(system_key).post(decision, payload, format="json")) == (
        401,
        "OMEGA_HUB_UNAUTHENTICATED",
    )

    # Nothing reached users, and nothing was recorded in maintainer A's name.
    assert Submission.objects.get(pk=sub_id).status == "in_review"
    assert not Version.objects.filter(entry__publisher__handle="m-pub").exists()
    anonymous = APIClient()
    assert anonymous.get("/omega-hub/v1/entries/m-pub/first-agent").status_code == 404
    assert (
        anonymous.get(
            "/omega-hub/v1/entries/m-pub/first-agent/versions/1.0.0/bundle"
        ).status_code
        == 404
    )
    assert "m-pub" not in anonymous.get("/omega-hub/v1/export").content.decode()
    assert not AuditEvent.objects.filter(action="review.approved").exists()

    # The payload was valid: maintainer B, signed in, approves it, and the audit names B.
    assert world.b.post(decision, payload, format="json").status_code == 200
    assert (
        AuditEvent.objects.get(action="review.approved").actor_id
        == world.maint_b.user.pk
    )


def test_a_maintainers_own_user_key_cannot_review(world):
    key = make_key(world.maint_b, "user")
    assert resolved_user(key) == world.maint_b.user and is_maintainer(
        world.maint_b.user
    )
    files = entry_files("first-agent")
    created = submit(
        token_client(world.token_a),
        "future-agi",
        files,
        name="first-agent",
        version="1.0.0",
    )
    _, report, _ = run_checks(files)
    sub_id = created.json()["id"]
    client = key_client(key)
    assert refusal(client.get("/omega-hub/v1/review/queue")) == (403, SESSION_REQUIRED)
    assert refusal(client.get(f"/omega-hub/v1/review/submissions/{sub_id}")) == (
        403,
        SESSION_REQUIRED,
    )
    assert refusal(
        client.post(
            f"/omega-hub/v1/review/submissions/{sub_id}/decision",
            approve_payload(report, disclosure=DISCLOSURE),
            format="json",
        )
    ) == (403, SESSION_REQUIRED)
    assert Submission.objects.get(pk=sub_id).status == "in_review"
    assert world.b.get("/omega-hub/v1/review/queue").status_code == 200


def test_the_system_key_cannot_mint_tokens_or_act_for_the_official_publisher(
    world, system_key
):
    client = key_client(system_key)
    tokens_before = HubToken.objects.count()
    minted = client.post(
        "/omega-hub/v1/me/tokens",
        {"publisher": "future-agi", "name": "taken"},
        format="json",
    )
    assert refusal(minted) == (401, "OMEGA_HUB_UNAUTHENTICATED")
    assert HubToken.objects.count() == tokens_before
    assert refusal(client.get("/omega-hub/v1/me/tokens")) == (
        401,
        "OMEGA_HUB_UNAUTHENTICATED",
    )
    assert refusal(
        client.delete(f"/omega-hub/v1/me/tokens/{world.token_a_obj.pk}")
    ) == (401, "OMEGA_HUB_UNAUTHENTICATED")
    me = client.get("/omega-hub/v1/me")
    assert (
        refusal(me) == (401, "OMEGA_HUB_UNAUTHENTICATED")
        and "isMaintainer" not in me.content.decode()
    )

    files = entry_files("first-agent")
    assert refusal(
        submit(client, "future-agi", files, name="first-agent", version="1.0.0")
    ) == (401, "OMEGA_HUB_UNAUTHENTICATED")
    assert not Submission.objects.exists()
    patched = client.patch(
        "/omega-hub/v1/publishers/future-agi", {"bio": "taken over"}, format="json"
    )
    assert refusal(patched) == (401, "OMEGA_HUB_UNAUTHENTICATED")
    world.official.refresh_from_db()
    assert world.official.bio != "taken over"

    entry = publish(world)
    sub_id = Submission.objects.get().pk
    for response in (
        client.post(f"{ENTRY}/versions/1.0.0/yank", {"reason": "taken"}, format="json"),
        client.post(f"{ENTRY}/unlist", {}, format="json"),
        client.post(f"/omega-hub/v1/submissions/{sub_id}/withdraw"),
        client.get(f"/omega-hub/v1/submissions/{sub_id}"),
        client.get(f"/omega-hub/v1/submissions/{sub_id}/bundle"),
        client.post(
            "/omega-hub/v1/publishers/future-agi/verification-request",
            {"evidence": "x"},
            format="json",
        ),
        client.post(
            "/omega-hub/v1/reports",
            {"entry": "future-agi/first-agent", "reason": "spam", "details": "x"},
            format="json",
        ),
        client.post(
            "/omega-hub/v1/review/entries/future-agi/first-agent/remove",
            {"category": "spam", "note": "x"},
            format="json",
        ),
    ):
        assert refusal(response) == (401, "OMEGA_HUB_UNAUTHENTICATED"), response.content
    entry.refresh_from_db()
    assert entry.latest_version.yanked_at is None
    assert APIClient().get(ENTRY).status_code == 200


def test_public_reads_refuse_retired_keys_but_remain_public_without_them(
    world, system_key
):
    publish(world)
    assert world.a.post(f"{ENTRY}/like").status_code == 200
    assert world.a.get(ENTRY).json()["likedByMe"] is True
    client = key_client(system_key)
    detail = client.get(ENTRY)
    assert refusal(detail) == (401, "OMEGA_HUB_UNAUTHENTICATED")
    assert "likedByMe" not in detail.json()
    anonymous = APIClient().get(ENTRY)
    assert anonymous.status_code == 200
    assert "likedByMe" not in anonymous.json()
    for path in (f"{ENTRY}/versions/1.0.0/bundle", "/omega-hub/v1/export"):
        assert refusal(client.get(path)) == (401, "OMEGA_HUB_UNAUTHENTICATED")
        assert APIClient().get(path).status_code == 200


def _hub_routes():
    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                yield from walk(pattern, prefix + str(pattern.pattern))
            else:
                yield prefix + str(pattern.pattern), pattern

    for route, pattern in walk(get_resolver()):
        cls = getattr(pattern.callback, "cls", None)
        if (
            route.startswith("omega-hub/")
            and cls is not None
            and issubclass(cls, HubView)
        ):
            path = "/" + route
            path = re.sub(r"<uuid:\w+>", lambda _m: str(uuid.uuid4()), path)
            path = re.sub(r"<path:\w+>", "README.md", path)
            path = re.sub(r"<(?:str:)?\w+>", "x-ref", path)
            for method in METHODS:
                if hasattr(cls, method):
                    yield pattern.name, method, path


def test_every_hub_request_that_needs_a_person_refuses_an_org_key(world, system_key):
    """Fail-closed over the whole urlconf: a new Hub view or method is covered without a new test."""
    client = key_client(system_key)
    seen, refused = set(), 0
    for name, method, path in _hub_routes():
        seen.add((name, method))
        call = getattr(client, method)
        response = (
            call(path) if method in ("get", "delete") else call(path, {}, format="json")
        )
        status, code = refusal(response)
        assert status == 401 and code == "OMEGA_HUB_UNAUTHENTICATED", (
            method,
            path,
            status,
            code,
        )
        if (name, method) not in PUBLIC_READS:
            refused += 1
    assert PUBLIC_READS <= seen, PUBLIC_READS - seen
    assert refused >= 40, refused


def test_the_public_read_exemption_covers_safe_methods_only():
    """No Hub view grants AllowAny on a write today; if one ever does, a key still cannot write through it."""
    from types import SimpleNamespace

    from rest_framework.permissions import AllowAny

    from omega_hub.errors import HubError
    from omega_hub.permissions import HubAuthenticated, refuse_org_api_key

    keyed = lambda method: SimpleNamespace(method=method, org_api_key=object())  # noqa: E731
    for method in ("GET", "HEAD", "OPTIONS"):
        refuse_org_api_key(keyed(method), [AllowAny()])
    for method, permissions in (
        ("POST", [AllowAny()]),
        ("PATCH", [AllowAny()]),
        ("DELETE", [AllowAny()]),
        ("GET", [HubAuthenticated()]),
    ):
        with pytest.raises(HubError) as refused:
            refuse_org_api_key(keyed(method), permissions)
        assert (refused.value.status, refused.value.code) == (403, SESSION_REQUIRED)
    refuse_org_api_key(
        SimpleNamespace(method="POST"), [HubAuthenticated()]
    )  # no key: not this check's business
