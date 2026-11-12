"""HUB-04 (READINESS L03): one trusted client address for the throttles and the install counts.

A client writes `X-Forwarded-For` and `User-Agent` itself, so neither may change who it is. The L03
Hub test: a fixed `REMOTE_ADDR` with 200 random `X-Forwarded-For` and `User-Agent` values gets 429 on
request 121, and the version's `DownloadDaily.count` is 1.
"""

import random
import uuid

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from rest_framework.test import APIClient

from omega_hub import system_checks
from omega_hub.client_ip import client_ip
from omega_hub.models import DownloadDaily
from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db
BUNDLE = "/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle"
CLIENT = "203.0.113.50"


def _random_hop(rng: random.Random) -> str:
    return ".".join(str(rng.randint(1, 254)) for _ in range(4))


def _spoofed(rng: random.Random) -> dict:
    hops = ", ".join(_random_hop(rng) for _ in range(rng.randint(1, 3)))
    return {"REMOTE_ADDR": CLIENT, "HTTP_X_FORWARDED_FOR": hops, "HTTP_USER_AGENT": f"omega/{uuid.uuid4().hex}"}


def test_spoofed_forwarded_for_and_user_agent_neither_dodge_the_throttle_nor_count_again(world):
    publish(world)
    rng = random.Random(20261019)
    anon = APIClient()
    statuses = []
    for number in range(1, 201):
        response = anon.get(BUNDLE, **_spoofed(rng))
        statuses.append(response.status_code)
        if number == 120:
            # Every one of these 120 differed in X-Forwarded-For and User-Agent and was served;
            # they are one client, so one count (asserted before the throttle can hide anything).
            assert statuses == [200] * 120
            assert DownloadDaily.objects.get().count == 1
        if number == 121:
            assert response.status_code == 429, response.content
            assert response.json()["error"]["code"] == "OMEGA_HUB_THROTTLED"
            assert int(response["Retry-After"]) > 0
    assert statuses[120:] == [429] * 80
    assert DownloadDaily.objects.get().count == 1


def test_a_trusted_proxy_depth_reads_the_hop_that_proxy_appended(world, settings):
    publish(world)
    settings.OMEGA_HUB_TRUSTED_PROXY_DEPTH = "1"
    anon = APIClient()
    # One ingress appends the address it saw; whatever the client wrote before it is ignored.
    for spoof in ("1.2.3.4", "5.6.7.8, 9.9.9.9"):
        anon.get(BUNDLE, REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR=f"{spoof}, 198.51.100.7")
    assert DownloadDaily.objects.get().count == 1
    anon.get(BUNDLE, REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="198.51.100.8")
    assert DownloadDaily.objects.get().count == 2


def test_one_address_counts_a_bounded_number_of_downloads_a_day(world, settings):
    publish(world)
    publish(world, "notes-plugin")
    settings.OMEGA_HUB_COUNTED_DOWNLOADS_PER_ADDRESS_DAY = "1"
    anon = APIClient()
    anon.get(BUNDLE, REMOTE_ADDR=CLIENT)
    anon.get("/omega-hub/v1/entries/future-agi/notes-plugin/versions/1.0.0/bundle", REMOTE_ADDR=CLIENT)
    assert sum(DownloadDaily.objects.values_list("count", flat=True)) == 1
    anon.get("/omega-hub/v1/entries/future-agi/notes-plugin/versions/1.0.0/bundle", REMOTE_ADDR="203.0.113.51")
    assert sum(DownloadDaily.objects.values_list("count", flat=True)) == 2


def test_the_throttle_and_count_keys_hold_no_address_or_user_agent(world):
    publish(world)
    APIClient().get(BUNDLE, REMOTE_ADDR=CLIENT, HTTP_USER_AGENT="omega/secret-ua")
    keys = " ".join(str(key) for key in getattr(cache, "_cache", {}))
    assert keys and CLIENT not in keys and "secret-ua" not in keys


@pytest.mark.parametrize(
    ("meta", "settings_values", "expected"),
    [
        ({"REMOTE_ADDR": "203.0.113.9", "HTTP_X_FORWARDED_FOR": "1.1.1.1"}, {}, "203.0.113.9"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "1.1.1.1, 198.51.100.7"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "1"}, "198.51.100.7"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "1.1.1.1, 198.51.100.7, 10.0.0.9"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "2"}, "198.51.100.7"),
        # Fewer hops than trusted proxies: the request skipped a proxy, so every hop is client-written.
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "1.1.1.1"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "2"}, "10.0.0.2"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "not-an-ip"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "1"}, "10.0.0.2"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_REAL_IP": "198.51.100.9", "HTTP_X_FORWARDED_FOR": "1.1.1.1"},
         {"OMEGA_HUB_CLIENT_IP_HEADER": "X-Real-IP"}, "198.51.100.9"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_REAL_IP": "junk"}, {"OMEGA_HUB_CLIENT_IP_HEADER": "X-Real-IP"}, "10.0.0.2"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "198.51.100.7:51234"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "1"}, "198.51.100.7"),
        ({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "[2001:db8::7]:443"},
         {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "1"}, "2001:db8::7"),
        ({"REMOTE_ADDR": "10.0.0.2"}, {"OMEGA_HUB_TRUSTED_PROXY_DEPTH": "many"}, "10.0.0.2"),
    ],
)
def test_client_ip(settings, meta, settings_values, expected):
    for name, value in settings_values.items():
        setattr(settings, name, value)
    request = RequestFactory().get("/omega-hub/v1/entries", **meta)
    assert client_ip(request) == expected


def test_w003_warns_outside_local_until_the_proxy_depth_is_set(settings):
    settings.ENV_TYPE = "production"
    for name in ("OMEGA_HUB_TRUSTED_PROXY_DEPTH", "OMEGA_HUB_CLIENT_IP_HEADER"):
        if hasattr(settings, name):
            delattr(settings, name)
    assert [w.id for w in system_checks.client_address_unconfigured()] == ["omega_hub.W003"]
    settings.OMEGA_HUB_TRUSTED_PROXY_DEPTH = "0"  # Django faces clients directly: an explicit answer
    assert system_checks.client_address_unconfigured() == []
    settings.ENV_TYPE = "local"
    del settings.OMEGA_HUB_TRUSTED_PROXY_DEPTH
    assert system_checks.client_address_unconfigured() == []
