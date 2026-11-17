"""Cache keys shared by the listing, export and checker code, and one place to drop them."""

from django.core.cache import cache

EXPORT = "omega_hub:export"
BLOCKLIST = "omega_hub:blocklist"
TRENDING = "omega_hub:trending"
CODE_HASHES = "omega_hub:code_hashes"


def invalidate_listing() -> None:
    cache.delete_many([EXPORT, TRENDING, CODE_HASHES])


def invalidate_blocklist() -> None:
    cache.delete_many([BLOCKLIST, EXPORT])
