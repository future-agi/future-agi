"""Cloud rehearsal settings; never selected by a product entrypoint."""

import os

from tfc.settings.test import *  # noqa: F403

if os.environ.get("G18_SYNTHETIC_REHEARSAL") != "1":
    raise RuntimeError("Synthetic process settings require explicit opt-in")
if os.environ.get("PG_HOST") not in ("localhost", "127.0.0.1"):
    raise RuntimeError("Synthetic process settings require loopback PostgreSQL")
if not os.environ.get("PG_DB", "").startswith("g18_operator_"):
    raise RuntimeError("Synthetic process settings require a disposable database")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ["REDIS_URL"],
    }
}
# Bound every synthetic connection, including legacy migrations that use the
# default alias. This does not attest production alias routing or timeout parity.
for alias in ("default", "default_direct"):
    DATABASES[alias]["OPTIONS"] = {  # noqa: F405
        "connect_timeout": 2,
        "options": "-c lock_timeout=2000 -c statement_timeout=30000",
    }
