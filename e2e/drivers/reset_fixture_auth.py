# ruff: noqa: E402 -- bootstrap Django before importing models.
"""Reset only test-stack login IP budgets between independent fixture cohorts.

The product's password/token/role checks remain real. These flows don't claim
login-rate-limit coverage; multiple test identities share one Docker host IP.
"""

import os
import sys

if os.getenv("E2E_ERROR_FEED_PROFILE") != "th8613" or os.getenv("ENV_TYPE") != "local":
    raise SystemExit("Fixture auth isolation requires the dedicated local profile")
sys.path.insert(0, "/app/backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django

django.setup()
from django.core.cache import cache

keys = [
    key
    for pattern in ("ip_requests_*", "blocked_ip_*")
    for key in cache.iter_keys(pattern)
]
cache.delete_many(keys)
print(f"Reset {len(keys)} test-stack login IP budget keys")
