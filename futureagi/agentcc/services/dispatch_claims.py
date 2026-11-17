"""Durably claim one explicit dispatch before any transport can send it."""

from django.db import DEFAULT_DB_ALIAS, IntegrityError, connection, transaction

from agentcc.models.dispatch_claim import (
    DISPATCH_CLAIM_KEY_MAX_LENGTH,
    AgentccDispatchClaim,
)

_UNIQUE_VIOLATION = "23505"
_KEY_COLUMN = "idempotency_key"


def _is_duplicate_key(error: IntegrityError) -> bool:
    """True only for a unique violation raised by the idempotency_key constraint.

    The driver error is Django's __cause__: psycopg 3 names the SQLSTATE
    `sqlstate`, psycopg 2 `pgcode`. A violation of any other constraint, any other
    integrity failure and every non-integrity error is not a duplicate claim.
    """
    cause = error.__cause__
    sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
    return (
        sqlstate == _UNIQUE_VIOLATION
        and isinstance(constraint, str)
        and _KEY_COLUMN in constraint
    )


def claim_dispatch_once(idempotency_key: str) -> bool:
    """Return True only after the first insert of this key has committed.

    Call OUTSIDE the hold transaction, after the hold commits and before the
    transport claim. Nested atomic blocks (including TestCase wrappers) and
    manual transactions are refused; a savepoint cannot make a claim durable.
    Database errors, including commit errors, propagate and must stop sending.

    The claim is an ORM insert on the default primary, inside its own durable
    transaction, so the accounts hard-erase SQL guard recognizes Django's own
    compiler path. A raw cursor INSERT is refused for this table: its
    idempotency_key column is in the protected credential census. The unique
    constraint decides the race; its violation, and only its violation, is False.
    It never reads a claim or consults a replica. False means the key was already
    claimed; it is terminal, not a retry signal.
    Claims are retained indefinitely here, with a minimum retention of 30 days.
    """
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > DISPATCH_CLAIM_KEY_MAX_LENGTH
        or "\x00" in idempotency_key
    ):
        raise ValueError(
            "idempotency_key must be a non-blank string of at most 255 characters without NUL"
        )

    # durable=True alone exempts Django TestCase's wrapping atomic block and
    # does not commit a transaction whose autocommit was disabled manually.
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("dispatch claims require autocommit outside atomic blocks")

    try:
        # A failed insert must leave this block so the transaction rolls back
        # before the duplicate is caught below.
        with transaction.atomic(using=DEFAULT_DB_ALIAS, durable=True):
            AgentccDispatchClaim.objects.using(DEFAULT_DB_ALIAS).create(
                idempotency_key=idempotency_key
            )
    except IntegrityError as error:
        if _is_duplicate_key(error):
            return False
        raise

    # Exiting atomic successfully is required before exposing True to a sender.
    return True
