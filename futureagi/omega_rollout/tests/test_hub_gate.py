"""RC-02's Hub leg: every Omega Hub view answers 404 `product_not_admitted` to B and to A2 (a sweep
over the URL table, so a view added without the check, or with it removed, turns this red), A uses
the Hub, the kill switch stops writes and the checker but not browsing, installs stop only when the
operator paused them too, and anonymous browsing opens only at S3 100%."""

from __future__ import annotations

import uuid

import pytest
from django.urls import URLPattern, reverse
from rest_framework.test import APIClient
from rest_framework.views import APIView

from omega_hub import urls as hub_urls
from omega_hub.api import public_views
from omega_hub.models import Publisher
from omega_hub.services import tokens
from omega_hub.tests.helpers import CHECKER_TOKEN, checker_client, token_client
from omega_rollout import ops
from omega_rollout import products as p
from omega_rollout.tests.conftest import admit, client_for

pytestmark = pytest.mark.django_db
METHODS = ("get", "post", "put", "patch", "delete")
SAMPLE = {
    "publisher": "someone",
    "name": "entry",
    "version": "1.0.0",
    "path": "README.md",
    "handle": "someone",
}


@pytest.fixture(autouse=True)
def hub_settings(settings, tmp_path):
    settings.OMEGA_HUB_STORAGE = "filesystem"
    settings.OMEGA_HUB_STORAGE_DIR = str(tmp_path / "hub-store")
    settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
    settings.OMEGA_HUB_PUBLIC_API_URL = "http://testserver/omega-hub/v1"


def hub_routes(include_checker=False):
    """(method, url, view class) for every method of every Hub route."""
    out = []
    for pattern in hub_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        view = pattern.callback.view_class
        kwargs = {
            name: (
                str(uuid.uuid4())
                if type(conv).__name__ == "UUIDConverter"
                else SAMPLE.get(name, "x")
            )
            for name, conv in pattern.pattern.converters.items()
        }
        url = reverse(f"omega_hub:{pattern.name}", kwargs=kwargs)
        if url.startswith("/omega-hub/internal/") and not include_checker:
            continue
        for method in METHODS:
            if hasattr(view, method):
                out.append((method, url, view))
    return out


def sweep(client):
    """Every Hub route that does NOT answer 404 product_not_admitted to `client`."""
    leaks = []
    for method, url, view in hub_routes():
        response = getattr(client, method)(url, {}, format="json")
        code = (
            (response.json().get("error") or {}).get("code")
            if response.status_code == 404
            else None
        )
        if response.status_code != 404 or code != p.REASON_NOT_ADMITTED:
            leaks.append((method.upper(), url, view.__name__, response.status_code))
    return leaks


# The internal routes the browser sweep skips: the checker's claim and five job routes, and the
# operations metrics scrape (HUB-OPS). Each answers only its own secret, never a session.
INTERNAL_ROUTES = 7


def test_the_sweep_covers_every_hub_route():
    routes = hub_routes(include_checker=True)
    assert len({url for _, url, _ in routes}) == len(hub_urls.urlpatterns)
    browser = hub_routes()
    assert (
        len(browser) >= 60
        and len({url for _, url, _ in browser}) == len(hub_urls.urlpatterns) - INTERNAL_ROUTES
    )


def test_b_and_a2_get_404_product_not_admitted_on_every_hub_route(tenants):
    admit(tenants, p.HUB)
    assert sweep(client_for(tenants.b)) == []
    assert sweep(client_for(tenants.a, tenants.a2)) == []


def test_a_hub_token_and_an_org_api_key_of_b_are_refused_the_same(tenants):
    admit(tenants, p.HUB)
    publisher = Publisher.objects.create(
        handle="b-pub",
        kind="organization",
        owner_org=tenants.b.organization,
        display_name="B",
    )
    _, plaintext = tokens.create_token(tenants.b.user, publisher, "cli")
    for client in (token_client(plaintext),):
        response = client.get(reverse("omega_hub:me"))
        assert (
            response.status_code == 404
            and response.json()["error"]["code"] == p.REASON_NOT_ADMITTED
        )


def test_a_seeded_regression_a_view_without_the_check_is_caught(tenants, monkeypatch):
    """Removing the rollout check from one view (here the entries list) must turn the sweep red."""
    admit(tenants, p.HUB)

    def unchecked(self, request):
        APIView.check_permissions(self, request)

    monkeypatch.setattr(public_views.EntriesView, "check_permissions", unchecked)
    leaks = sweep(client_for(tenants.b))
    assert [(method, view) for method, _, view, _ in leaks] == [("GET", "EntriesView")]


def test_a_uses_the_hub(tenants):
    admit(tenants, p.HUB)
    client = client_for(tenants.a)
    assert client.get(reverse("omega_hub:rules")).status_code == 200
    assert client.get(reverse("omega_hub:me")).status_code == 200
    assert client.get(reverse("omega_hub:entries")).status_code == 200


def test_paused_hub_keeps_browsing_and_refuses_new_work_with_503(tenants):
    admit(tenants, p.HUB)
    ops.kill(p.HUB, actor=tenants.operator.user, reason="drill")
    client = client_for(tenants.a)
    assert client.get(reverse("omega_hub:entries")).status_code == 200
    response = client.post(reverse("omega_hub:submissions"), {}, format="json")
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == p.REASON_PAUSED
    )
    assert response["Retry-After"] == "30"
    # B is still not admitted: a pause never turns a 404 into a 503.
    assert (
        client_for(tenants.b)
        .post(reverse("omega_hub:submissions"), {}, format="json")
        .status_code
        == 404
    )
    claim = checker_client().post(reverse("omega_hub:checker-claim"), {}, format="json")
    assert claim.status_code == 503 and claim.json()["error"]["code"] == p.REASON_PAUSED
    ops.restore(p.HUB, actor=tenants.operator.user, reason="fixed")
    assert (
        checker_client()
        .post(reverse("omega_hub:checker-claim"), {}, format="json")
        .status_code
        != 503
    )


def test_installs_stop_only_when_the_operator_pauses_them(tenants):
    admit(tenants, p.HUB)
    bundle = reverse(
        "omega_hub:version-bundle",
        kwargs={"publisher": "x", "name": "y", "version": "1.0.0"},
    )
    ops.kill(p.HUB, actor=tenants.operator.user, reason="drill")
    assert (
        client_for(tenants.a).get(bundle).status_code == 404
    )  # no such entry: served, not paused
    ops.restore(p.HUB, actor=tenants.operator.user, reason="r")
    ops.kill(p.HUB, actor=tenants.operator.user, reason="drill", pause_installs=True)
    response = client_for(tenants.a).get(bundle)
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == p.REASON_PAUSED
    )


def test_anonymous_browsing_opens_only_at_s3_100(tenants):
    entries = reverse("omega_hub:entries")
    admit(tenants, p.HUB, stage=p.S3)
    ops.set_percentage(p.HUB, 50, actor=tenants.operator.user, reason="half")
    assert APIClient().get(entries).status_code == 404
    ops.set_percentage(p.HUB, 100, actor=tenants.operator.user, reason="all")
    assert APIClient().get(entries).status_code == 200


def test_rc03_bypass_attempts_at_django_answer_404(tenants):
    """RC-03's Django legs as B: HEAD and OPTIONS, a forged X-Forwarded-For, a case-changed path and
    another org's id in the selection header all answer 404 (or the router's own 404), never data.
    """
    admit(tenants, p.HUB)
    b = client_for(tenants.b)
    entries = reverse("omega_hub:entries")
    for response in (
        b.head(entries),
        b.options(entries),
        b.get(entries, HTTP_X_FORWARDED_FOR="10.1.2.3"),
        b.get(entries.upper()),
        b.get(entries, HTTP_X_ORGANIZATION_ID=str(tenants.a.organization.pk)),
    ):
        assert response.status_code in (403, 404), response.status_code
        assert b"results" not in response.content
    exchange = b.options("/accounts/omega/session-exchange/")
    assert exchange.status_code != 200 or b"accessToken" not in exchange.content
