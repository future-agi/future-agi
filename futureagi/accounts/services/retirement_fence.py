"""Transaction-local exemption for the database's durable customer-write fence.

The trigger writes supersession in the SAME statement/transaction as any ordinary
key update, including raw SQL and a second revoke. PostgreSQL's key-row lock
serializes it against retirement and reversal. No timestamp or process-local
signal is trusted as customer provenance. Only the bounded operator call is
exempt, on its own connection; concurrent customer connections are not.
"""

from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connections, transaction

FENCE_VERSION = "database-v1"


@contextmanager
def gateway_retirement_writes(using=DEFAULT_DB_ALIAS):
    with transaction.atomic(using=using):
        connection = connections[using]
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('futureagi.retirement_733', true)")
            previous = cursor.fetchone()[0] or ""
            cursor.execute("SELECT set_config('futureagi.retirement_733', 'on', true)")
        yield
        # On an exception the enclosing atomic block rolls the setting back.
        # On success restore it even when the caller owns a larger transaction.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('futureagi.retirement_733', %s, true)", [previous]
            )
