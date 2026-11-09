"""A Sentry client that records envelopes instead of sending them, for tests.

The application's own init_sentry configures the client, so its hooks and
integrations are the deployed ones; the transport keeps each serialized
envelope in a list. The DSN names a closed loopback port.

Initializing Sentry changes process state that outlives a test: the client
bound to the global scope, breadcrumbs kept on the scopes, and the logging
integration's patch of logging.Logger.callHandlers, which reads every record
afterwards. The capture restores all of it on exit, and marks the logging
integration as not set up when it was not before, so the next capture patches
it again.
"""

import logging
from contextlib import contextmanager


def _scopes():
    import sentry_sdk

    return (
        sentry_sdk.get_current_scope(),
        sentry_sdk.get_isolation_scope(),
        sentry_sdk.get_global_scope(),
    )


@contextmanager
def sentry_capture(monkeypatch):
    """Yield (client, envelopes) for one initialized application client."""
    import sentry_sdk
    from sentry_sdk import integrations
    from sentry_sdk.transport import Transport

    from tfc.logging import sentry as app_sentry
    from tfc.logging.config import configure_structlog

    envelopes = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope.serialize())

        def flush(self, timeout, callback=None):
            return None

    dispatch = logging.Logger.callHandlers
    installed = set(integrations._installed_integrations)
    processed = set(integrations._processed_integrations)
    for scope in _scopes():
        scope.clear_breadcrumbs()
    configure_structlog(cache_logger_on_first_use=False)
    monkeypatch.setattr(app_sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(app_sentry, "SENTRY_DSN", "http://public@127.0.0.1:9/1")
    monkeypatch.setattr(app_sentry, "_initialized_components", set())
    client = None
    try:
        assert app_sentry.init_sentry(component="celery")
        client = sentry_sdk.get_client()
        client.transport = Capture(client.options)
        yield client, envelopes
    finally:
        if client is not None:
            client.close()
        for scope in _scopes():
            scope.set_client(None)
            scope.clear_breadcrumbs()
        logging.Logger.callHandlers = dispatch
        if "logging" not in installed:
            integrations._installed_integrations.discard("logging")
        if "logging" not in processed:
            integrations._processed_integrations.discard("logging")
