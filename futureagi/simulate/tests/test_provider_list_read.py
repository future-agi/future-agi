from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from simulate.models import ProviderList
from simulate.services import provider_list
from simulate.services.provider_list import (
    ProviderListNotPublished,
    ProviderListVersionNotFound,
    read_provider_list,
)

CAPABILITIES = ["transcript", "turn_timing", "tool_calls"]


def _document(sdk_version, providers=("retell", "vapi")):
    return {
        "providers": [
            {
                "provider": name,
                "display_name": name.title(),
                "facts_schema_version": "1",
                "auth_strategy": "bearer",
                "ask": [
                    {
                        "name": "api_key",
                        "label": "API Key",
                        "secret": True,
                        "required": True,
                    },
                    {
                        "name": "assistant_id",
                        "label": "Agent ID",
                        "secret": False,
                        "required": True,
                    },
                ],
                "key_check": {"method": "GET", "url": f"https://api.{name}.test/check"},
                "capabilities": CAPABILITIES,
            }
            for name in providers
        ],
        "agent_schema": {"type": "object"},
        "episode_export": {
            "episode_schema": {"type": "object"},
            "capabilities": CAPABILITIES,
        },
        "sdk_version": sdk_version,
    }


def _row(sdk_version, published_at, providers=("retell", "vapi")):
    return ProviderList.no_workspace_objects.create(
        sdk_version=sdk_version,
        document=_document(sdk_version, providers),
        content_hash="sha256:" + "0" * 64,
        last_published_at=published_at,
    )


@pytest.fixture(autouse=True)
def empty_cache():
    provider_list._cache.clear()
    yield
    provider_list._cache.clear()


@pytest.mark.django_db
def test_current_is_the_last_published():
    now = timezone.now()
    _row("0.3.0", now - timedelta(minutes=5))
    _row("0.2.9", now)
    _row("0.4.0", now - timedelta(minutes=1))

    snapshot = read_provider_list()

    assert snapshot.sdk_version == "0.2.9"
    assert (
        snapshot.published_at
        == ProviderList.no_workspace_objects.get(sdk_version="0.2.9").last_published_at
    )


@pytest.mark.django_db
def test_current_on_a_tie_is_the_greater_version_string():
    now = timezone.now()
    _row("0.10.0", now)
    _row("0.9.0", now)

    assert read_provider_list().sdk_version == "0.9.0"


@pytest.mark.django_db
def test_a_version_read_is_exact_and_never_falls_back():
    now = timezone.now()
    _row("0.3.0", now - timedelta(minutes=5), providers=("vapi",))
    _row("0.4.0", now)

    snapshot = read_provider_list(sdk_version="0.3.0")

    assert snapshot.sdk_version == "0.3.0"
    assert list(snapshot.providers) == ["vapi"]
    with pytest.raises(ProviderListVersionNotFound):
        read_provider_list(sdk_version="0.3")


@pytest.mark.django_db
def test_unlisted_provider_is_none_and_match_is_case_sensitive():
    _row("0.3.0", timezone.now())

    snapshot = read_provider_list()
    vapi = snapshot.providers.get("vapi")

    assert vapi.display_name == "Vapi"
    assert vapi.auth_strategy == "bearer"
    assert [field.name for field in vapi.ask] == ["api_key", "assistant_id"]
    assert vapi.ask[0].secret is True
    assert vapi.capabilities == tuple(CAPABILITIES)
    assert vapi.raw["key_check"]["url"] == "https://api.vapi.test/check"
    for name in ("eleven_labs", "others", "livekit_bridge", "Vapi"):
        assert snapshot.providers.get(name) is None
    with pytest.raises(TypeError):
        snapshot.providers["bland"] = vapi


@pytest.mark.django_db
def test_one_query_then_cached_and_a_miss_is_not_cached(monkeypatch):
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(ProviderListNotPublished):
            read_provider_list()
    assert len(queries) == 1

    _row("0.3.0", timezone.now())
    with CaptureQueriesContext(connection) as queries:
        first = read_provider_list()
        second = read_provider_list()
    assert len(queries) == 1
    assert second is first

    clock = [1000.0]
    monkeypatch.setattr(provider_list.time, "monotonic", lambda: clock[0])
    provider_list._cache.clear()
    read_provider_list(sdk_version="0.3.0")
    clock[0] += provider_list.PROVIDER_LIST_CACHE_SECONDS
    with CaptureQueriesContext(connection) as queries:
        read_provider_list(sdk_version="0.3.0")
    assert len(queries) == 1


@pytest.mark.django_db
def test_error_messages_are_the_fixed_strings():
    with pytest.raises(ProviderListNotPublished, match="^provider list not published$"):
        read_provider_list()
    with pytest.raises(
        ProviderListVersionNotFound,
        match="^provider list not published for sdk 9.9.9$",
    ):
        read_provider_list(sdk_version="9.9.9")
    for bad in ("", "x" * 33, 3):
        with pytest.raises(
            ValueError,
            match="^sdk_version must be a string of 1 to 32 characters or None$",
        ):
            read_provider_list(sdk_version=bad)
