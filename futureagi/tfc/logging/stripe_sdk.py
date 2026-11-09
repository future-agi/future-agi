"""Keep Stripe SDK payload logs out of every configured output consumer.

The pinned SDK emits its request/response/error logs on the ``stripe`` logger.
At DEBUG those include arbitrary customer details and metadata: redacting only
session identifiers is insufficient. Drop SDK records at the source, before
Logger.callHandlers (including Sentry's hook), console, file or OTLP handlers.
FA's fixed reason/exception-class diagnostics remain enabled.

Install once for all threads, not by temporarily changing a shared log level
around one request. The checkout reader also installs before constructing the
SDK client, so an alternate settings/logging module cannot bypass the boundary.
The checkout reader separately refuses before HTTP if the SDK raw-stderr
channel (STRIPE_LOG or stripe.log) is enabled; it never toggles shared state.
"""

import logging


class _StripeSDKPayloadFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


_PAYLOAD_FILTER = _StripeSDKPayloadFilter()


def install_stripe_sdk_log_filter() -> None:
    logging.getLogger("stripe").addFilter(_PAYLOAD_FILTER)
