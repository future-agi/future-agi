"""Transaction-local application reads without a PostgreSQL statement cap.

Admission/request checks are cooperative boundaries, not query timers. Keep
them separate from the setting lifetime, including across nested read scopes.
"""

from collections.abc import Callable
from contextlib import ExitStack, contextmanager

from django.db import DatabaseError


class ApplicationPostgresReadError(DatabaseError):
    """A PostgreSQL read/control failed; this does not imply a timeout."""


@contextmanager
def application_postgres_reads(
    *,
    connection,
    atomic,
    check_request: Callable[[], object] | None = None,
    read_only: bool = False,
    repeatable_read: bool = False,
):
    """Lazily disable statement_timeout, restoring an enclosing transaction.

    Only the first actual statement opens a transaction/savepoint. Mock-only
    readers stay connection-lazy. On success restore the previous setting before
    release; on failure rollback restores SET LOCAL, including inside an outer
    transaction. No background thread or session-global SET is used.
    """

    def check():
        if check_request is not None:
            check_request()

    def control(cursor, sql, params=None):
        try:
            cursor.execute(sql, params)
        except Exception as exc:
            # Driver cursors intentionally bypass Django's wrapper stack, so
            # their errors need an explicit, non-timeout database boundary.
            raise ApplicationPostgresReadError(
                "PostgreSQL application read control unavailable"
            ) from exc

    check()
    if connection.vendor != "postgresql":
        yield
        return

    opened = False
    opening = False
    previous_timeout = None
    with ExitStack() as transactions:

        def execute_read(execute, sql, params, many, context):
            nonlocal opened, opening, previous_timeout
            if opening or sql.lstrip().upper().startswith(
                ("SAVEPOINT ", "RELEASE SAVEPOINT ", "ROLLBACK TO SAVEPOINT ")
            ):
                # A SAVEPOINT emitted by an enclosing lazy scope must not
                # initialize an inner scope before the enclosing one is ready.
                return execute(sql, params, many, context)
            check()
            if not opened:
                was_atomic = connection.in_atomic_block
                opening = True
                try:
                    transactions.enter_context(atomic())
                finally:
                    opening = False
                opened = True
                check()
                cursor = context["cursor"].cursor
                if not was_atomic and (read_only or repeatable_read):
                    characteristics = []
                    if repeatable_read:
                        characteristics.append("ISOLATION LEVEL REPEATABLE READ")
                    if read_only:
                        characteristics.append("READ ONLY")
                    control(cursor, "SET TRANSACTION " + ", ".join(characteristics))
                control(cursor, "SELECT current_setting('statement_timeout')")
                try:
                    previous_timeout = cursor.fetchone()[0]
                except Exception as exc:
                    raise ApplicationPostgresReadError(
                        "PostgreSQL prior statement setting unavailable"
                    ) from exc
                if not isinstance(previous_timeout, str):
                    raise ApplicationPostgresReadError(
                        "PostgreSQL prior statement setting unavailable"
                    )
                control(
                    cursor,
                    "SELECT set_config('statement_timeout', %s, true)",
                    ("0",),
                )
                check()
            return execute(sql, params, many, context)

        # Remove this wrapper before restoration and RELEASE SAVEPOINT. Other
        # enclosing scopes remain active and retain their own setting lifetime.
        with connection.execute_wrapper(execute_read):
            yield
        if opened:
            with connection.cursor() as cursor:
                control(
                    cursor.cursor,
                    "SELECT set_config('statement_timeout', %s, true)",
                    (previous_timeout,),
                )
