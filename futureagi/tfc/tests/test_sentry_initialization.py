"""Exercise centralized initialization with the real SDK and an in-memory transport."""

import json
import secrets

import pytest
import sentry_sdk

from tfc.logging import sentry


@pytest.fixture
def initialized_sentry(monkeypatch):
    events = []
    real_init = sentry_sdk.init
    global_scope = sentry_sdk.get_global_scope()
    previous_client = global_scope.client

    def init_with_memory_transport(**options):
        # Keep the application's options and explicit integrations, but avoid
        # network I/O and auto-instrumenting unrelated installed test packages.
        return real_init(
            **options,
            transport=events.append,
            default_integrations=False,
            auto_enabling_integrations=False,
        )

    monkeypatch.setattr(sentry_sdk, "init", init_with_memory_transport)
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(sentry, "IS_STAGING", False)
    monkeypatch.setattr(sentry, "_initialized_components", set())
    with sentry_sdk.isolation_scope():
        try:
            assert sentry.init_sentry(component="temporal", tags={"queue": "tasks_l"})
            yield sentry_sdk.get_client(), events
        finally:
            client = global_scope.client
            if client is not previous_client:
                client.close()
            global_scope.set_client(previous_client)


def test_init_sentry_caps_event_strings_at_8192(initialized_sentry):
    _client, events = initialized_sentry

    assert sentry_sdk.capture_event({"message": "x" * 9000}) is not None
    assert len(events) == 1
    assert len(events[0]["message"]) <= 8192


def test_init_sentry_preserves_strings_at_the_limit(initialized_sentry):
    _client, events = initialized_sentry
    message = "x" * 8192

    assert sentry_sdk.capture_event({"message": message}) is not None
    assert len(events) == 1
    assert events[0]["message"] == message
    assert events[0]["tags"]["component"] == "temporal"
    assert events[0]["tags"]["queue"] == "tasks_l"


def _strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item)


def test_a_secret_whose_marker_is_past_8192_is_scrubbed_and_no_string_is_longer(
    initialized_sentry,
):
    _client, events = initialized_sentry
    secret = secrets.token_hex(16)
    # The value comes first and the name that marks it as a secret comes after
    # the 8192nd character. A cut made before the scrub would keep the value
    # and drop the name.
    marked_late = f"value {secret} " + "pad " * 3_000 + "customer_api_key"
    assert marked_late.index("customer_api_key") > 8192

    assert (
        sentry_sdk.capture_event(
            {
                "message": "y" * 20_000,
                "extra": {"note": marked_late, "blob": "z" * 50_000},
            }
        )
        is not None
    )
    assert len(events) == 1
    assert secret not in json.dumps(events[0], default=str)
    assert max(len(s) for s in _strings_in(events[0])) <= 8192
