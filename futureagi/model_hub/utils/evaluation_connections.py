"""A bounded connection-open budget for background evaluation row threads."""

from functools import wraps

from django.db import connections

EVALUATION_CONNECT_TIMEOUT_SECONDS = 5


def evaluation_connection_budget(function):
    """Keep interactive connection settings out of concurrent background writes.

    Django's wrappers are thread-local, but their settings dictionaries originate
    from shared DATABASES entries. Copy both dictionaries rather than modifying
    either shared object. Apply the policy in the row thread, not its parent
    executor; retain routing, autocommit, connection age and finally cleanup.
    This changes only socket establishment, never statement timeouts or retries.
    """

    @wraps(function)
    def wrapped(*args, **kwargs):
        originals = []
        try:
            for connection in connections.all():
                if connection.vendor != "postgresql":
                    continue
                original = connection.settings_dict
                originals.append((connection, original))
                options = dict(original.get("OPTIONS", {}))
                options["connect_timeout"] = max(
                    int(options.get("connect_timeout") or 0),
                    EVALUATION_CONNECT_TIMEOUT_SECONDS,
                )
                connection.settings_dict = {**original, "OPTIONS": options}
            return function(*args, **kwargs)
        finally:
            for connection, original in reversed(originals):
                connection.settings_dict = original

    return wrapped
