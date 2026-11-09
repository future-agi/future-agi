"""Candidate serialization is independent of the cancellation receipt.

Never take a lifecycle row lock while saving results. The result-key lock also
covers publication, which freezes the candidate before the terminal CAS. The
watchdog touches only the lifecycle row and cannot wait behind a result write.
"""

from contextlib import contextmanager

from django.db import connection, transaction

RESULT_LOCK_TIMEOUT_MS = 250


@contextmanager
def result_lock(generation):
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Applies to both the candidate key and the final lifecycle CAS.
            # Local to the transaction, never a session-wide setting.
            cursor.execute("SET LOCAL lock_timeout = '250ms'")
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ["trace-analysis-result:" + str(generation)],
            )
        yield
