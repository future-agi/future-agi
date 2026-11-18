"""Bind a real Sentry client for one test, then put the previous one back.

``with sentry_sdk.init(...)`` closes its client on exit but leaves it bound to
the global scope. Its logging integration then stays active for the rest of the
test process: every later ``logger.exception`` builds a full event, and
serialising the frame locals calls ``repr()`` on whatever they hold, which for
a lazy queryset or an unloaded relation runs SQL inside another test's query
counter. Tests must bind a client only through ``sentry_capture``.

On exit ``sentry_capture`` closes its client, rebinds the client that was bound
before (normally the inactive ``NonRecordingClient``) and drops the forked
isolation and current scopes, so breadcrumbs and tags recorded during the test
do not leak either. The integrations a client activates live on that client,
so rebinding the previous client deactivates them. The SDK's one-time
``setup_once`` patches (``Logger.callHandlers``, Django's handler, Dedupe's
event processor) stay installed by design: they consult the bound client on
every call and do nothing while it has no matching integration, and undoing
their bookkeeping would make the next client patch everything a second time.
One such patch is not inert: DjangoIntegration's setup adds ``django.request``
and ``django.server`` to the SDK's ignored loggers for the rest of the process,
so a capture test must not expect events from those two loggers.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sentry_sdk
from sentry_sdk.transport import Transport

from tfc.logging.sentry import _get_before_send

TEST_DSN = "https://public@example.test/1"


class EnvelopeTransport(Transport):
    """Keeps every error event the client would send, as the JSON it would send."""

    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") == "event":
                self.events.append(item.payload.json)


@contextmanager
def sentry_capture(*integrations: Any, **options: Any) -> Iterator[EnvelopeTransport]:
    """Run the block with a real client that records events in memory.

    The client uses only ``integrations`` (no defaults) and the production
    ``before_send`` unless ``options`` override them.
    """
    transport = EnvelopeTransport()
    global_scope = sentry_sdk.get_global_scope()
    previous_client = global_scope.client
    options.setdefault("before_send", _get_before_send())
    with sentry_sdk.isolation_scope() as scope:
        scope.clear_breadcrumbs()
        sentry_sdk.init(
            dsn=TEST_DSN,
            transport=transport,
            default_integrations=False,
            integrations=list(integrations),
            **options,
        )
        client = global_scope.client
        try:
            yield transport
        finally:
            try:
                client.close()
            finally:
                global_scope.set_client(previous_client)
