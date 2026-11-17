"""Primary-pool, independently committed nonce consumption for eval requests."""

import secrets

from django.db import connections


class DurableNonceStore:
    def consume(self, audience, identifier, iat, exp, now):
        if audience != "fa-eval":
            return False
        # A separate wrapper is essential: ATOMIC_REQUESTS or an eval rollback
        # must never roll back nonce consumption. Never borrow the Redis cache.
        connection = connections["default"].copy(alias="envelope_nonce_primary")
        try:
            connection.ensure_connection()
            connection.set_autocommit(True)
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO accounts_envelope_nonce (aud, id, exp) VALUES (%s, %s, %s) "
                    "ON CONFLICT (aud, id) DO NOTHING RETURNING id",
                    [audience, identifier, exp],
                )
                consumed = cursor.fetchone() is not None
                if consumed and secrets.randbelow(100) == 0:
                    cursor.execute(
                        "DELETE FROM accounts_envelope_nonce WHERE (aud, id) IN "
                        "(SELECT aud, id FROM accounts_envelope_nonce WHERE exp < %s "
                        "ORDER BY exp LIMIT 500)",
                        [int(now) - 300],
                    )
            return consumed
        finally:
            connection.close()
