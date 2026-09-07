"""One SOURCE SELECT on the exact native socket whose identity was proved.

The caller owns the closed-table SQL boundary and admitted route. This module
does not discover sources, grant raw connection access, or retry failed reads.
The locked SOURCE profile supplies server limits; no query settings are sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any
from uuid import UUID

from tracer.services.clickhouse.server_readonly import (
    _top_level_tokens,
    ensure_read_statement,
    without_query_settings,
)

from .native_write_transport import _verified_connection
from .write_admission import WriteMember

_MAX_SOURCE_READ_TIMEOUT_MS = 120_000


class NativeReadTransportError(RuntimeError):
    """The source response was not proved on the pinned readonly connection."""


@contextmanager
def _source_connection(driver, remaining):
    # ClickHouseClient.connection() intentionally rejects SOURCE raw access.
    # Keep that public guard intact; this closed SELECT adapter alone borrows
    # through the same bounded read admission and exclusive native pool.
    admission = driver._read_admission
    if not admission.acquire(timeout=remaining()):
        raise TimeoutError("native source read admission deadline exhausted")
    native = None
    try:
        remaining()
        native = driver._get_client()
        yield native
    finally:
        try:
            if native is not None:
                driver._return_client(native)
        finally:
            admission.release()


def _parameter(value, depth=0):
    if depth > 16:
        raise NativeReadTransportError("native read parameters exceed nesting bound")
    if isinstance(value, datetime):
        # Driver datetime escaping otherwise discards fractional seconds.
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise NativeReadTransportError("native read timestamps must be UTC")
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    if type(value) in (tuple, list):
        return type(value)(_parameter(item, depth + 1) for item in value)
    if (
        value is None
        or type(value) in (str, bytes, bool, date, UUID)
        or (type(value) is int and -(1 << 63) <= value < 1 << 64)
        or (type(value) is float and isfinite(value))
    ):
        return value
    raise NativeReadTransportError("unsupported native read parameter")


def read_once(
    driver: Any,
    *,
    member: WriteMember,
    database: str,
    user: str,
    sql: str,
    params: Mapping[str, Any] | None,
    timeout_ms: int,
    query_id: str | None = None,
) -> tuple:
    """Return native ``(rows, column_types)`` after one pinned readonly SELECT.

    Empty results still require actual node/database/user/locked-profile proof.
    Identity uses Client.execute with settings=None; after proof, parameter
    binding and raw ordinary dispatch cannot establish another connection.
    The wall must fit the SOURCE driver's configured ceiling and 120 seconds;
    the separate mutation transport retains its 30-second maximum.
    """
    ceiling_ms = getattr(driver, "read_timeout_ceiling_ms", None)
    if (
        type(timeout_ms) is not int
        or type(ceiling_ms) is not int
        or ceiling_ms < 1
        or not 1 <= timeout_ms <= min(ceiling_ms, _MAX_SOURCE_READ_TIMEOUT_MS)
        or type(member) is not WriteMember
        or getattr(driver, "database", None) != database
        or getattr(driver, "user", None) != user
        or getattr(driver, "server_enforced_readonly", None) is not True
        or not isinstance(database, str)
        or not database
        or not isinstance(user, str)
        or not user
        or (params is not None and not isinstance(params, Mapping))
        or (
            query_id is not None
            and (
                not isinstance(query_id, str)
                or not 1 <= len(query_id.encode()) <= 1024
                or any(c in query_id for c in "\x00\r\n")
            )
        )
        or not callable(getattr(driver, "_get_client", None))
        or not callable(getattr(driver, "_return_client", None))
        or not callable(
            getattr(getattr(driver, "_read_admission", None), "acquire", None)
        )
        or not callable(
            getattr(getattr(driver, "_read_admission", None), "release", None)
        )
    ):
        raise NativeReadTransportError("native read identity/deadline invalid")
    if not isinstance(sql, str) or not sql or len(sql.encode()) > 1 << 20:
        raise NativeReadTransportError("native read SQL is invalid or oversized")
    ensure_read_statement(sql)
    tokens = [token for token, _, _ in _top_level_tokens(sql)]
    if (
        tokens[0] not in {"SELECT", "WITH"}
        or any(token in {"INTO", "OUTFILE", "FORMAT"} for token in tokens)
        or without_query_settings(sql) != sql
    ):
        raise NativeReadTransportError("native read requires a settings-free SELECT")
    parameters = (
        None if params is None else {key: _parameter(v) for key, v in params.items()}
    )
    if parameters is not None and any(not isinstance(k, str) for k in parameters):
        raise NativeReadTransportError("native read parameter names must be strings")
    with _verified_connection(
        lambda remaining: _source_connection(driver, remaining),
        member=member,
        database=database,
        user=user,
        timeout_ms=timeout_ms,
        method="process_ordinary_query",
        readonly=True,
        error=NativeReadTransportError,
    ) as (native, check):
        native.make_query_settings(None)
        context = native.connection.context
        if (
            native.settings != {}
            or context.settings != {}
            or context.client_settings.get("server_side_params") is not False
        ):
            raise NativeReadTransportError("native read inherited query settings")
        bound_sql = (
            sql
            if parameters is None
            else native.substitute_params(sql, parameters, context)
        )
        if not isinstance(bound_sql, str) or len(bound_sql.encode()) > 8 << 20:
            raise NativeReadTransportError(
                "native read bound SQL is invalid or oversized"
            )
        check()
        result = native.process_ordinary_query(
            bound_sql,
            params=None,
            with_column_types=True,
            external_tables=None,
            query_id=query_id,
            types_check=False,
            columnar=False,
        )
        check()
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], (list, tuple))
            or not isinstance(result[1], (list, tuple))
            or not result[1]
            or any(
                not isinstance(c, tuple)
                or len(c) != 2
                or any(not isinstance(v, str) or not v for v in c)
                for c in result[1]
            )
            or any(
                not isinstance(row, (list, tuple)) or len(row) != len(result[1])
                for row in result[0]
            )
        ):
            raise NativeReadTransportError("native read result lacks rows/column types")
        return result
