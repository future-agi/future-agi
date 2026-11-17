"""Sorted organization/person advisory transaction locks.

There is no global resource and no table lock. Issuance and erasure use the same
canonical key space. Locks live until the enclosing transaction ends. A nested
operation may reuse held keys, but cannot expand a lock set out of order: it
refuses before writing. Closure discovery is repeated after acquisition and
refuses if concurrent writes introduced an unheld key; callers may retry the
whole transaction. Only explicitly trusted out-of-application DB administrators
are the accepted session residual; this is not database-side enforcement.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import blake2b
from uuid import UUID

from django.db import connections, transaction

_ACTIVE = ContextVar("hard_erase_lifecycle", default=None)


def identity_keys(*, organizations=(), people=()):
    """Domain-separated UUIDs, canonical across strings and UUID instances."""
    return frozenset(
        (domain, str(UUID(str(value))))
        for domain, values in (("organization", organizations), ("person", people))
        for value in values
        if value is not None and str(value) != "-"
    )


def advisory_number(key):
    value = ("fa-hard-erase-1757/" + "/".join(key)).encode("ascii")
    return int.from_bytes(blake2b(value, digest_size=8).digest(), "big", signed=True)


def require_held(using, keys):
    from accounts.services.hard_erase import _refuse

    active = _ACTIVE.get()
    if (
        active is None
        or active[0] is not connections[using]
        or not active[0].in_atomic_block
        or not frozenset(keys).issubset(active[1])
    ):
        _refuse("lifecycle key set changed; retry the whole transaction")


@contextmanager
def lifecycle_lock(using="default", *, keys):
    from accounts.services.hard_erase import _refuse

    connection = connections[using]
    if connection.vendor != "postgresql":
        _refuse("lifecycle serialization requires PostgreSQL")
    keys = frozenset(keys)
    if _ACTIVE.get() is not None:
        require_held(using, keys)
        yield
        return
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0].lower() != "read committed":
                _refuse("READ COMMITTED is required for the locked recheck")
            for key in sorted(keys):
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)", [advisory_number(key)]
                )
        token = _ACTIVE.set((connection, keys))
        try:
            yield
        finally:
            _ACTIVE.reset(token)
