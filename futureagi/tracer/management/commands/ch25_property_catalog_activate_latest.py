"""Select the newest qualified production property-catalog build once.

This command is intentionally narrower than the activation-control library. It
is the production bootstrap bridge: status is read-only, while ``--execute``
can append only the first ACTIVATE event (or replay that exact request). Future
disable, rollback, and head advancement remain separate reviewed operations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.management.commands.ch25_property_catalog_lifecycle_controller import (
    discover_workspace_scopes,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_MAX_EVENTS,
    ACTIVATION_CONTROL_TABLE,
    ActivationControlError,
    ActivationControlRequest,
    ActivationControlScope,
    ActivationControlTarget,
    ClickHouseActivationControlStore,
    PropertyCatalogActivationControlPlane,
    activation_control_event_sql,
    qualified_activation_sql,
    selected_control_target,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_uuid,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_CLOUD_DEPLOYMENTS,
    PRODUCTION_LIFECYCLE_ACK,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    require_prod_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS

ACTIVATION_CONTROL_ACK = "PROPERTY_CATALOG_ACTIVATION_CONTROL_V1"
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
    r"`?(?P<table>[A-Za-z_][A-Za-z0-9_]*)`? "
    r"TO `?(?P<user>[A-Za-z_][A-Za-z0-9_]*)`?$"
)
_MAX_EXPECTED_CLICKHOUSE_HOSTNAMES = 16


class ProductionActivationCommandError(RuntimeError):
    """The one-shot production activation was not admitted."""


@dataclass(frozen=True, slots=True)
class ActivationCommandConfig:
    database: str
    host: str
    port: int
    user: str
    password: str
    expected_hostnames: tuple[str, ...]
    catalog_epoch: int
    projection_version: int
    workspace_scope_mode: str
    workspace_ids: tuple[str, ...]


class _ActivationControlClient:
    """Two-table native client closed over the immutable activation bridge."""

    def __init__(
        self,
        driver: ClickHouseClient,
        *,
        database: str,
        user: str,
        expected_hostnames: Sequence[str],
    ) -> None:
        self.catalog_database = require_prod_catalog_database(database)
        self._driver = driver
        self._user = user
        self._expected_hostnames = tuple(expected_hostnames)
        self._validate_identity()
        self._attest_server_identity_and_grants()
        self._allowed_reads = frozenset(
            {
                activation_control_event_sql(self.catalog_database),
                qualified_activation_sql(self.catalog_database),
            }
        )

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
        values = [tuple(rows[0][column] for column in ordered_columns)]
        column_sql = ", ".join(ordered_columns)
        self._driver.execute(
            f"INSERT INTO {expected_table} ({column_sql}) VALUES",
            values,  # type: ignore[arg-type]
            settings={
                "insert_deduplication_token": deduplication_token,
                "max_execution_time": timeout_ms / 1_000,
            },
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
        _validate_control_writer_grants(
            self._driver.execute(_CLICKHOUSE_GRANTS_SQL),
            database=self.catalog_database,
            user=self._user,
        )


class Command(BaseCommand):
    help = (
        "Inspect or append the first production property-catalog activation-control "
        "event for one active workspace in the configured lifecycle scope."
    )
    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--workspace-id", required=True)
        parser.add_argument("--request-id")
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Append one initial ACTIVATE event; omission is read-only status.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        client: _ActivationControlClient | None = None
        try:
            config = activation_command_config(settings_object=settings)
            workspace_id = canonical_uuid(
                options.get("workspace_id"),
                field="workspace_id",
            )
            if (
                config.workspace_scope_mode == "allowlist"
                and workspace_id not in config.workspace_ids
            ):
                raise ProductionActivationCommandError(
                    "workspace is outside the exact production lifecycle allowlist"
                )
            scopes, skipped = discover_workspace_scopes((workspace_id,))
            if skipped or len(scopes) != 1:
                raise ProductionActivationCommandError(
                    "workspace has no active project scope"
                )
            execute = bool(options.get("execute"))
            request_id = options.get("request_id")
            if execute and not request_id:
                raise ProductionActivationCommandError(
                    "--execute requires one explicit --request-id"
                )
            if not execute and request_id:
                raise ProductionActivationCommandError(
                    "--request-id is accepted only with --execute"
                )
            driver = ClickHouseClient(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                database=config.database,
                server_enforced_readonly=False,
                connect_timeout=5,
                send_timeout=30,
                receive_timeout=30,
                pool_size=1,
                read_timeout_ceiling_ms=RUNTIME_LIMITS.state_store_timeout_ms,
            )
            client = _ActivationControlClient(
                driver,
                database=config.database,
                user=config.user,
                expected_hostnames=config.expected_hostnames,
            )
            store = ClickHouseActivationControlStore(
                client,
                database=config.database,
            )
            payload = run_initial_activation(
                store=store,
                scope=ActivationControlScope(
                    organization_id=scopes[0].organization_id,
                    workspace_id=workspace_id,
                ),
                catalog_epoch=config.catalog_epoch,
                projection_version=config.projection_version,
                execute=execute,
                request_id=request_id,
                now=datetime.now(UTC),
            )
            return canonical_json(payload, max_bytes=256 * 1024)
        except (
            ActivationControlError,
            ProductionActivationCommandError,
            PropertyCatalogPublishError,
            TypeError,
            ValueError,
        ) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if client is not None:
                client.close()


def activation_command_config(*, settings_object: Any) -> ActivationCommandConfig:
    environment = str(getattr(settings_object, "ENV_TYPE", "")).strip().lower()
    cloud = str(getattr(settings_object, "CLOUD_DEPLOYMENT", "")).strip()
    if environment not in {"prod", "production"}:
        raise ProductionActivationCommandError(
            "activation control requires ENV_TYPE=production"
        )
    if cloud not in PRODUCTION_CLOUD_DEPLOYMENTS:
        raise ProductionActivationCommandError(
            "activation control requires an exact supported production cloud"
        )
    if (
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_ENABLED", False)
        is not True
    ):
        raise ProductionActivationCommandError(
            "activation control requires the production lifecycle gate"
        )
    if (
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_ACK", "")
        != PRODUCTION_LIFECYCLE_ACK
    ):
        raise ProductionActivationCommandError(
            "activation control requires the production lifecycle acknowledgement"
        )
    if (
        getattr(settings_object, "PROPERTY_CATALOG_ACTIVATION_CONTROL_ACK", "")
        != ACTIVATION_CONTROL_ACK
    ):
        raise ProductionActivationCommandError(
            "activation control requires its exact production acknowledgement"
        )
    database = str(
        getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE", "")
    ).strip()
    require_prod_catalog_database(database)
    host = _required_text(
        settings_object,
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST",
    )
    port = getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT", 0)
    if type(port) is not int or not 1 <= port <= 65_535:
        raise ProductionActivationCommandError(
            "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT must be a valid port"
        )
    user = _required_text(
        settings_object,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER",
    )
    password = getattr(
        settings_object,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD",
        "",
    )
    if not isinstance(password, str) or not password:
        raise ProductionActivationCommandError(
            "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD must be non-empty"
        )
    other_users = {
        str(getattr(settings_object, name, "")).strip()
        for name in (
            "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_USER",
            "PROPERTY_CATALOG_CH_USER",
            "CH25_USER",
        )
    }
    if user in other_users:
        raise ProductionActivationCommandError(
            "activation-control writer must be a dedicated ClickHouse identity"
        )
    expected_hostnames = _required_hostnames(
        settings_object,
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES",
    )
    catalog_epoch = _positive_uint16_setting(
        settings_object,
        "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH",
    )
    projection_version = _positive_uint16_setting(
        settings_object,
        "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION",
    )
    workspace_scope_mode = (
        str(
            getattr(
                settings_object,
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE",
                "allowlist",
            )
        )
        .strip()
        .lower()
    )
    if workspace_scope_mode not in {"all", "allowlist"}:
        raise ProductionActivationCommandError(
            "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE must equal "
            "allowlist or all"
        )
    workspace_ids = tuple(
        sorted(
            canonical_uuid(value, field="workspace_id")
            for value in getattr(
                settings_object,
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST",
                (),
            )
        )
    )
    if workspace_scope_mode == "all" and workspace_ids:
        raise ProductionActivationCommandError(
            "all lifecycle workspace scope requires an empty workspace allowlist"
        )
    if workspace_scope_mode == "allowlist" and (
        not workspace_ids or len(workspace_ids) != len(set(workspace_ids))
    ):
        raise ProductionActivationCommandError(
            "activation control requires a non-empty unique workspace allowlist"
        )
    return ActivationCommandConfig(
        database=database,
        host=host,
        port=port,
        user=user,
        password=password,
        expected_hostnames=expected_hostnames,
        catalog_epoch=catalog_epoch,
        projection_version=projection_version,
        workspace_scope_mode=workspace_scope_mode,
        workspace_ids=workspace_ids,
    )


def run_initial_activation(
    *,
    store: Any,
    scope: ActivationControlScope,
    catalog_epoch: int,
    projection_version: int,
    execute: bool,
    request_id: Any,
    now: datetime,
) -> dict[str, Any]:
    qualified = tuple(store.list_qualified_activations(scope))
    if not qualified:
        raise ProductionActivationCommandError(
            "workspace has no qualified catalog activation"
        )
    target = qualified[-1].target
    if (
        target.catalog_epoch != catalog_epoch
        or target.projection_version != projection_version
    ):
        raise ProductionActivationCommandError(
            "newest qualified activation does not match the configured epoch/projection"
        )
    events = tuple(store.list_control_events(scope))
    if not execute:
        selected = selected_control_target(events)
        return {
            "control_event_count": len(events),
            "mode": "status",
            "newest_qualified_target": _target_payload(target),
            "selected_target": (
                _target_payload(selected) if selected is not None else None
            ),
        }
    checked_request_id = canonical_uuid(request_id, field="request_id")
    result = PropertyCatalogActivationControlPlane(store).activate(
        request=ActivationControlRequest(
            request_id=checked_request_id,
            target=target,
            expected_head=None,
        ),
        now=now,
    )
    return {
        "control_sequence": result.event.control_sequence,
        "idempotent": result.idempotent,
        "mode": "execute",
        "request_id": result.event.request_id,
        "selected_target": (
            _target_payload(result.selected_target)
            if result.selected_target is not None
            else None
        ),
    }


def _target_payload(target: ActivationControlTarget) -> dict[str, Any]:
    return {
        "activation_sha256": target.activation_sha256,
        "build_token": target.build_token,
        "catalog_epoch": target.catalog_epoch,
        "catalog_revision": target.catalog_revision,
        "organization_id": target.organization_id,
        "projection_version": target.projection_version,
        "workspace_id": target.workspace_id,
    }


def _required_text(source: Any, name: str) -> str:
    value = getattr(source, name, None)
    if not isinstance(value, str) or not value.strip():
        raise ProductionActivationCommandError(f"{name} must be non-empty")
    return value.strip()


def _positive_uint16_setting(source: Any, name: str) -> int:
    value = getattr(source, name, None)
    if type(value) is not int or not 1 <= value < (1 << 16):
        raise ProductionActivationCommandError(f"{name} must be a positive UInt16")
    return value


def _required_hostnames(source: Any, name: str) -> tuple[str, ...]:
    values = getattr(source, name, ())
    if not isinstance(values, (tuple, list)):
        raise ProductionActivationCommandError(f"{name} must be an exact list")
    hostnames = tuple(values)
    if (
        not 1 <= len(hostnames) <= _MAX_EXPECTED_CLICKHOUSE_HOSTNAMES
        or any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > 253
            or any(character in value for character in "\r\n\x00")
            for value in hostnames
        )
        or len(set(hostnames)) != len(hostnames)
    ):
        raise ProductionActivationCommandError(
            f"{name} must contain unique bounded exact hostnames"
        )
    return tuple(sorted(hostnames))


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


__all__ = [
    "ACTIVATION_CONTROL_ACK",
    "ActivationCommandConfig",
    "Command",
    "ProductionActivationCommandError",
    "activation_command_config",
    "run_initial_activation",
]
