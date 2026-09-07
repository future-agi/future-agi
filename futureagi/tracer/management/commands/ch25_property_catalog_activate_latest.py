"""Select the newest qualified production property-catalog build once.

This command is intentionally narrower than the activation-control library. It
is the production bootstrap bridge: status is read-only, while ``--execute``
can append only the first ACTIVATE event (or replay that exact request). Future
disable, rollback, and head advancement remain separate reviewed operations.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlError,
    ActivationControlRequest,
    ActivationControlScope,
    ActivationControlTarget,
    ClickHouseActivationControlStore,
    PropertyCatalogActivationControlPlane,
    selected_control_target,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_uuid,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
    InstallationIdentity,
    load_identity,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeWriteProof,
    NativeWriteProofError,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_CLOUD_DEPLOYMENTS,
    PRODUCTION_LIFECYCLE_ACK,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    require_prod_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    _CLICKHOUSE_GRANTS_SQL as _CLICKHOUSE_GRANTS_SQL,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    _CLICKHOUSE_PROVENANCE_SQL as _CLICKHOUSE_PROVENANCE_SQL,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    ProductionActivationCommandError,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    _ActivationControlClient as _ActivationControlClient,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    _validate_control_writer_grants as _validate_control_writer_grants,
)
from tracer.services.clickhouse.v2.property_catalog.replicated_write_startup import (
    replicated_write_admission_context,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS

ACTIVATION_CONTROL_ACK = "PROPERTY_CATALOG_ACTIVATION_CONTROL_V1"
_MAX_EXPECTED_CLICKHOUSE_HOSTNAMES = 16


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
    installation: InstallationIdentity | None = None


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
        from tracer.management.commands.ch25_property_catalog_lifecycle_controller import (
            discover_workspace_scopes,
        )

        resources = ExitStack()
        try:
            execute = bool(options.get("execute"))
            config = activation_command_config(
                settings_object=settings, require_managed=execute
            )
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
                    "workspace has no active authorized lifecycle scope"
                )
            request_id = options.get("request_id")
            if execute and not request_id:
                raise ProductionActivationCommandError(
                    "--execute requires one explicit --request-id"
                )
            if not execute and request_id:
                raise ProductionActivationCommandError(
                    "--request-id is accepted only with --execute"
                )
            if execute:
                request_id = canonical_uuid(request_id, field="request_id")
                coordinator_directory = _activation_state_directory(settings)
                writer = _managed_control_writer(
                    config, settings_object=settings, resources=resources
                )
                driver = writer.driver
                expected_hostnames = (writer.member.hostname,)
            else:
                # Status never constructs a native journal, admission producer,
                # or file coordinator. The adapter permits only reviewed reads.
                writer = None
                driver = _control_driver(config, resources=resources)
                expected_hostnames = config.expected_hostnames
            from tracer.services.clickhouse.v2.property_catalog.reader_activation import (
                FileActivationControlCoordinator,
                ReaderActivationClient,
            )

            client = ReaderActivationClient(
                driver,
                database=config.database,
                user=config.user,
                expected_hostnames=expected_hostnames,
                durable_writer=writer,
            )
            store = ClickHouseActivationControlStore(
                client,
                database=config.database,
                append_coordinator=(
                    FileActivationControlCoordinator(
                        coordinator_directory,
                        database=config.database,
                    )
                    if execute
                    else None
                ),
            )
            # Reauthorize the workspace immediately before the selection plane;
            # startup/discovery may have taken long enough for ownership to change.
            if execute:
                current, skipped = discover_workspace_scopes((workspace_id,))
                if (
                    skipped
                    or len(current) != 1
                    or (
                        current[0].organization_id != scopes[0].organization_id
                        or str(current[0].workspace_id) != workspace_id
                    )
                ):
                    raise ProductionActivationCommandError(
                        "workspace authorization changed during activation startup"
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
            NativeWriteProofError,
            NativeWriteUnresolved,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            # Register drivers before adapter construction, including failures
            # in schema/grant attestation. Proof drivers stay alive through replay.
            resources.close()


def activation_command_config(
    *, settings_object: Any, require_managed: bool = False
) -> ActivationCommandConfig:
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
    installation, catalog_epoch, projection_version = _command_identity(
        settings_object, database=database, required=require_managed
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
        installation=installation,
    )


def _activation_state_directory(settings_object: Any) -> Path:
    """Use the SAME coordinator directory as workspace_settings_overlay."""
    value = getattr(settings_object, "PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY", "")
    if (
        not isinstance(value, str)
        or not value
        or not Path(value).is_absolute()
        or Path(value).is_symlink()
        or not Path(value).is_dir()
        or Path(value) == Path(Path(value).anchor)
    ):
        raise ProductionActivationCommandError(
            "activation control requires the existing shared lifecycle runtime directory"
        )
    return Path(value)


def _installation_directory(settings_object: Any) -> Path:
    """Native receipts live beside the installed identity and revision fence."""
    value = getattr(
        settings_object, "PROPERTY_CATALOG_LIFECYCLE_REVISION_FENCE_FILE", ""
    )
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ProductionActivationCommandError(
            "activation control requires the shared absolute lifecycle fence path"
        )
    return Path(value).parent


def _command_identity(
    settings_object: Any, *, database: str, required: bool
) -> tuple[InstallationIdentity | None, int, int]:
    prefix = "PROPERTY_CATALOG_LIFECYCLE_"
    epoch = getattr(settings_object, prefix + "CATALOG_EPOCH", 0)
    projection = getattr(settings_object, prefix + "PROJECTION_VERSION", 0)
    if any(
        type(v) is not int or not 0 <= v < (1 << 16) for v in (epoch, projection)
    ) or bool(epoch) != bool(projection):
        raise ProductionActivationCommandError(
            "catalog epoch/projection must both be managed zero or positive UInt16"
        )
    identity = None
    if (
        required
        or not epoch
        or getattr(settings_object, prefix + "REVISION_FENCE_FILE", "")
    ):
        path = _installation_directory(settings_object) / IDENTITY_FILENAME
        if required or not epoch or path.exists() or path.is_symlink():
            # Deliberately load only: a one-shot selector must not initialize or
            # migrate installation identity, even when its target is qualified.
            identity = load_identity(path)
            identity.require_destination(
                environment="production",
                target_database=database,
                candidate_topic=getattr(
                    settings_object, "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC", ""
                ),
                ordered_topic=getattr(
                    settings_object, "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC", ""
                ),
            )
            if epoch and (epoch, projection) != (
                identity.catalog_epoch,
                identity.projection_version,
            ):
                raise ProductionActivationCommandError(
                    "configured epoch/projection conflicts with persisted identity"
                )
            producer = getattr(settings_object, prefix + "PRODUCER_STREAM_ID", "")
            if producer and producer != identity.producer_stream_id:
                raise ProductionActivationCommandError(
                    "configured producer conflicts with persisted identity"
                )
            epoch, projection = identity.catalog_epoch, identity.projection_version
    return identity, epoch, projection


def _control_driver(
    config: ActivationCommandConfig,
    *,
    resources: ExitStack,
    host: str | None = None,
    port: int | None = None,
) -> ClickHouseClient:
    driver = ClickHouseClient(
        host=config.host if host is None else host,
        port=config.port if port is None else port,
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
    resources.callback(driver.close)
    return driver


def _managed_control_writer(
    config: ActivationCommandConfig, *, settings_object: Any, resources: ExitStack
) -> DurableNativeCatalogWriter:
    """Reattest existing production admission and retain direct proof resources."""
    if config.installation is None:
        raise ProductionActivationCommandError(
            "execute requires persisted installation identity"
        )
    if os.environ.get("FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME") == config.user:
        raise ProductionActivationCommandError(
            "control and ledger proof principals must differ"
        )
    directory = _installation_directory(settings_object)
    # This requires BOTH existing immutable files before the discovery producer
    # can run; execute cannot create a missing admission as a side effect.
    journal = resources.enter_context(NativeWriteJournal(directory))
    if journal.identity != config.installation:
        raise ProductionActivationCommandError(
            "installed identity changed during startup"
        )
    identity, admission, connections = resources.enter_context(
        replicated_write_admission_context(
            settings_object, prefix="PROPERTY_CATALOG_LIFECYCLE_"
        )
    )
    if identity != journal.identity or admission != journal.admission:
        raise ProductionActivationCommandError(
            "installed admission changed during startup"
        )
    matching = [
        c
        for c in connections
        if (c.driver.host, c.driver.port) == (config.host, config.port)
    ]
    if len(matching) > 1 or not connections:
        raise ProductionActivationCommandError(
            "control writer has no unique admitted route"
        )
    selected = matching[0] if matching else min(connections, key=lambda c: c.name)
    driver = _control_driver(
        config,
        resources=resources,
        host=selected.driver.host,
        port=selected.driver.port,
    )
    proof = NativeWriteProof(
        directory=directory,
        identity=identity,
        admission=admission,
        connections=connections,
    )
    return DurableNativeCatalogWriter(
        driver, directory=directory, proof=proof, member_name=selected.name
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


__all__ = [
    "ACTIVATION_CONTROL_ACK",
    "ActivationCommandConfig",
    "Command",
    "ProductionActivationCommandError",
    "activation_command_config",
    "run_initial_activation",
]
