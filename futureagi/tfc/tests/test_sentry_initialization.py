"""Exercise centralized initialization with the real SDK and an in-memory transport."""

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
    client, events = initialized_sentry

    assert client.options["max_value_length"] == 8192
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
