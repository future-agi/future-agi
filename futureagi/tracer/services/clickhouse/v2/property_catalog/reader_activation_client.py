"""Narrow native control adapter; production policy stays unchanged."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from tfc.settings.settings import validate_property_catalog_database

from .activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_MAX_EVENTS,
    ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS,
    ACTIVATION_CONTROL_TABLE,
    activation_control_event_sql,
    activation_history_sql,
    qualified_activation_sql,
)
from .native_write_proof import STRICT_READ_AGREEMENT, NativeReadAgreement

_ACTIVATION_TABLE = "property_catalog_activations"
_CLICKHOUSE_PROVENANCE_SQL = """
SELECT
    hostName(),
    currentDatabase(),
    currentUser(),
    toUInt64(value),
    toUInt8(readonly)
FROM system.settings
WHERE name = 'readonly'
"""
_CLICKHOUSE_GRANTS_SQL = "SHOW GRANTS FOR CURRENT_USER"
_DIRECT_TABLE_GRANT_RE = re.compile(
    r"^GRANT (?P<access>SELECT|INSERT)(?:, (?P<second>SELECT|INSERT))? "
    r"ON `?(?P<database>[A-Za-z_][A-Za-z0-9_]*)`?\."
    r"`?(?P<table>[A-Za-z_][A-Za-z0-9_]*|\*)`? "
    r"TO `?(?P<user>[A-Za-z_][A-Za-z0-9_]*)`?$"
)
_OSS_GRANT_RE = re.compile(
    r"^GRANT (?P<access>[A-Z ]+(?:, [A-Z ]+)*) "
    r"ON (?P<database_quote>`?)(?P<database>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=database_quote)\."
    r"(?P<table>\*|(?P<table_quote>`?)[A-Za-z_][A-Za-z0-9_]*(?P=table_quote)) "
    r"TO (?P<user_quote>`?)(?P<user>[A-Za-z_][A-Za-z0-9_]*)(?P=user_quote)$"
)
_OSS_CAPTURE_ACCESS = frozenset(
    {"SELECT", "INSERT", "CREATE TABLE", "ALTER DELETE", "ALTER TTL", "DROP TABLE"}
)


class ProductionActivationCommandError(RuntimeError):
    """The one-shot production activation was not admitted."""


class _ActivationControlClient:
    """Two-table native client closed over the immutable activation bridge."""

    def __init__(
        self,
        driver: Any,
        *,
        database: str,
        user: str,
        expected_hostnames: Sequence[str],
        deployment: str = "prod",
        durable_writer: Any = None,
        source_database: str | None = None,
    ) -> None:
        if deployment not in {"dev", "prod"}:
            raise ValueError("control writer deployment must be dev or prod")
        self.catalog_database = validate_property_catalog_database(
            database, deployment=deployment
        )
        self._deployment = deployment
        self._source_database = source_database
        self._driver = driver
        self._durable_writer = durable_writer
        if durable_writer is not None:
            from .durable_native_writer import DurableNativeCatalogWriter

            if (
                type(durable_writer) is not DurableNativeCatalogWriter
                or durable_writer.driver is not driver
                or durable_writer.database != self.catalog_database
            ):
                raise ProductionActivationCommandError(
                    "native control write identity differs"
                )
        self._user = user
        self._expected_hostnames = tuple(expected_hostnames)
        self._validate_identity()
        self._attest_server_identity_and_grants()
        from .state_store import activation_latest_rows_sql

        # Contracts belong to these exact reviewed statements, not arbitrary
        # LIMIT-bearing SQL. Every member must be below its raw sentinel before
        # duplicate/order differences can be ignored by the native proof.
        self._read_agreements = {
            activation_control_event_sql(
                self.catalog_database, deployment=deployment
            ): NativeReadAgreement.cap_plus_one(
                limit_parameter="catalog_control_result_limit",
                limit=ACTIVATION_CONTROL_MAX_EVENTS + 1,
            ),
            qualified_activation_sql(
                self.catalog_database, deployment=deployment
            ): NativeReadAgreement.cap_plus_one(
                limit=ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS + 1
            ),
            qualified_activation_sql(
                self.catalog_database, deployment=deployment, follow=True
            ): NativeReadAgreement.cap_plus_one(limit=4),
            # This is an existence selector, not a truncated history inventory.
            activation_history_sql(
                self.catalog_database, deployment=deployment
            ): STRICT_READ_AGREEMENT,
            # Exact all-status history is needed to distinguish a terminally
            # disabled FOLLOW anchor from its later self-anchored replacement.
            # It uses the existing activation SELECT grant, never broader SQL.
            activation_latest_rows_sql(
                self.catalog_database
            ): NativeReadAgreement.complete_result(),
        }
        self._allowed_reads = frozenset(self._read_agreements)

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]:
        self._validate_identity()
        if sql not in self._allowed_reads:
            raise ProductionActivationCommandError(
                "activation-control client rejected a non-reviewed read"
            )
        if self._durable_writer is not None:
            return self._durable_writer.query(
                sql,
                params,
                timeout_ms=timeout_ms,
                agreement=self._read_agreements[sql],
            )
        rows, columns, _ = self._driver.execute_read(
            sql,
            dict(params),
            timeout_ms=timeout_ms,
            settings={
                "max_result_rows": ACTIVATION_CONTROL_MAX_EVENTS + 1,
                "result_overflow_mode": "throw",
                "readonly": 2,
            },
        )
        names = tuple(
            str(column[0]) if isinstance(column, tuple) else str(column)
            for column in columns
        )
        if len(names) != len(set(names)):
            raise ProductionActivationCommandError(
                "ClickHouse returned duplicate activation-control columns"
            )
        return tuple(dict(zip(names, row, strict=True)) for row in rows)

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        self._validate_identity()
        self._attest_server_identity_and_grants()
        expected_table = f"`{self.catalog_database}`.`{ACTIVATION_CONTROL_TABLE}`"
        ordered_columns = tuple(columns)
        if table != expected_table or ordered_columns != ACTIVATION_CONTROL_COLUMNS:
            raise ProductionActivationCommandError(
                "activation-control client rejected a non-ledger insert"
            )
        if len(rows) != 1 or set(rows[0]) != set(ACTIVATION_CONTROL_COLUMNS):
            raise ProductionActivationCommandError(
                "activation-control append must contain exactly one complete row"
            )
        if self._durable_writer is not None:
            return self._durable_writer.insert(
                table,
                rows,
                columns=ordered_columns,
                timeout_ms=timeout_ms,
                deduplication_token=deduplication_token,
            )
        values = [tuple(rows[0][column] for column in ordered_columns)]
        column_sql = ", ".join(ordered_columns)
        self._driver.execute(
            f"INSERT INTO {expected_table} ({column_sql}) VALUES",
            values,  # type: ignore[arg-type]
            settings={
                # Reader-control publication needs a completed insert even if
                # the deployment's user profile enables asynchronous buffering.
                "async_insert": 0,
                "insert_deduplication_token": deduplication_token,
                "max_execution_time": timeout_ms / 1_000,
            },
        )

    @property
    def receipt_confirmation_available(self) -> bool:
        """Capability for store admission, not evidence of any write's outcome."""
        return self._durable_writer is not None

    def confirm_receipt(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        """Require the exact control-event receipt from the durable transport.

        This never inserts, replays, or substitutes visible rows for a native
        journal receipt. Legacy one-shot inserts remain a separate policy; a
        client without a durable writer cannot confirm a durable receipt.
        """
        self._validate_identity()
        self._attest_server_identity_and_grants()
        expected_table = f"`{self.catalog_database}`.`{ACTIVATION_CONTROL_TABLE}`"
        ordered_columns = tuple(columns)
        if table != expected_table or ordered_columns != ACTIVATION_CONTROL_COLUMNS:
            raise ProductionActivationCommandError(
                "activation-control client rejected a non-ledger receipt"
            )
        if (
            not isinstance(rows, Sequence)
            or len(rows) != 1
            or not isinstance(rows[0], Mapping)
            or set(rows[0]) != set(ACTIVATION_CONTROL_COLUMNS)
        ):
            raise ProductionActivationCommandError(
                "activation-control receipt must contain exactly one complete row"
            )
        from .durable_native_writer import DurableNativeCatalogWriter

        writer = self._durable_writer
        if writer is None:
            raise ProductionActivationCommandError(
                "activation-control receipt requires a durable native writer"
            )
        if (
            type(writer) is not DurableNativeCatalogWriter
            or writer.driver is not self._driver
            or writer.database != self.catalog_database
        ):
            raise ProductionActivationCommandError(
                "native control receipt identity differs"
            )
        confirm = getattr(writer, "confirm_receipt", None)
        if not callable(confirm):
            raise ProductionActivationCommandError(
                "durable native writer cannot confirm an exact control receipt"
            )
        return confirm(
            table,
            rows,
            columns=ordered_columns,
            timeout_ms=timeout_ms,
            deduplication_token=deduplication_token,
        )

    def close(self) -> None:
        self._driver.close()

    def _validate_identity(self) -> None:
        if (
            self._driver.database != self.catalog_database
            or self._driver.user != self._user
            or self._driver.server_enforced_readonly is not False
        ):
            raise ProductionActivationCommandError(
                "activation-control client identity changed"
            )

    def _attest_server_identity_and_grants(self) -> None:
        rows = self._driver.execute(_CLICKHOUSE_PROVENANCE_SQL)
        if len(rows) != 1 or len(rows[0]) != 5:
            raise ProductionActivationCommandError(
                "activation-control provenance did not return one complete row"
            )
        hostname, database, user, readonly_value, readonly_locked = rows[0]
        if (
            hostname not in self._expected_hostnames
            or database != self.catalog_database
            or user != self._user
            or readonly_value != 0
            or readonly_locked != 0
        ):
            raise ProductionActivationCommandError(
                "activation-control server identity or write profile mismatched"
            )
        validator = (
            _validate_control_writer_grants
            if self._deployment == "prod"
            else _validate_oss_control_writer_grants
        )
        validator(
            self._driver.execute(_CLICKHOUSE_GRANTS_SQL),
            database=self.catalog_database,
            user=self._user,
            **(
                {"source_database": self._source_database}
                if self._deployment == "dev"
                else {}
            ),
        )


def _validate_oss_control_writer_grants(
    rows: Any, *, database: str, user: str, source_database: str | None = None
) -> None:
    """Exact OSS grants; capture requires an explicitly configured source.

    SHOW GRANTS can reorder or split privileges. Compare the exact privilege
    set, rejecting duplicates, role grants, delegation and unrelated scopes.
    None preserves the legacy no-capture contract, never adopts observed grants.
    """
    expected = {
        (database, "*"): frozenset({"SELECT", "INSERT"}),
        ("system", "databases"): frozenset({"SELECT"}),
        ("system", "settings"): frozenset({"SELECT"}),
        ("system", "tables"): frozenset({"SELECT"}),
    }
    if source_database is not None:
        if (
            not isinstance(source_database, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_database) is None
            or source_database in {database, f"{database}_source_capture", "system"}
        ):
            raise ProductionActivationCommandError("invalid OSS capture source")
        expected[(source_database, "spans")] = frozenset({"SELECT"})
        expected[(f"{database}_source_capture", "*")] = _OSS_CAPTURE_ACCESS
    observed: dict[tuple[str, str], set[str]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ProductionActivationCommandError("invalid OSS control grants")
    for row in rows:
        if (
            not isinstance(row, (tuple, list))
            or len(row) != 1
            or not isinstance(row[0], str)
        ):
            raise ProductionActivationCommandError("invalid OSS control grant row")
        match = _OSS_GRANT_RE.fullmatch(row[0])
        if match is None or match.group("user") != user:
            raise ProductionActivationCommandError(
                "unexpected or delegated OSS control grant"
            )
        key = (match.group("database"), match.group("table").strip("`"))
        access = match.group("access").split(", ")
        privileges = set(access)
        if len(privileges) != len(access) or observed.get(key, set()) & privileges:
            raise ProductionActivationCommandError("duplicate OSS control grant")
        if not privileges <= expected.get(key, frozenset()):
            raise ProductionActivationCommandError(
                "unexpected or delegated OSS control grant"
            )
        observed.setdefault(key, set()).update(privileges)
    if observed != expected:
        raise ProductionActivationCommandError(
            "OSS control writer grants do not match isolated bootstrap contract"
        )


def _validate_control_writer_grants(
    rows: Any,
    *,
    database: str,
    user: str,
) -> None:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ProductionActivationCommandError(
            "activation-control grant query returned an invalid result"
        )
    observed: dict[str, frozenset[str]] = {}
    for row in rows:
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) != 1
            or not isinstance(row[0], str)
        ):
            raise ProductionActivationCommandError(
                "activation-control grant query returned an invalid row"
            )
        match = _DIRECT_TABLE_GRANT_RE.fullmatch(row[0])
        if (
            match is None
            or match.group("database") != database
            or match.group("user") != user
        ):
            raise ProductionActivationCommandError(
                "activation-control writer has an unexpected or delegated grant"
            )
        access = {match.group("access")}
        if match.group("second") is not None:
            access.add(match.group("second"))
        table = match.group("table")
        if table in observed:
            raise ProductionActivationCommandError(
                "activation-control writer has duplicate table grants"
            )
        observed[table] = frozenset(access)
    expected = {
        _ACTIVATION_TABLE: frozenset({"SELECT"}),
        ACTIVATION_CONTROL_TABLE: frozenset({"SELECT", "INSERT"}),
    }
    if observed != expected:
        raise ProductionActivationCommandError(
            "activation-control writer grants must match the two-table contract exactly"
        )
