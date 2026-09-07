"""Live identity SQL checks with an actual bounded coordinator reservation."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from run import save


def _prepare_write_admission(settings_object, fence_file, manifest, *, client_factory):
    from tracer.services.clickhouse.v2.property_catalog.oss_write_startup import (
        prepare_oss_write_admission,
    )

    # The normal producer reads the identity in this owned runtime and proves
    # both mapped endpoints. Never manufacture or copy its admission descriptor.
    return prepare_oss_write_admission(
        SimpleNamespace(
            **vars(settings_object),
            ENV_TYPE="development",
            CLOUD_DEPLOYMENT="",
            PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE=fence_file,
            PROPERTY_CATALOG_DEV_TARGET_DATABASE=manifest["database"],
            PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE=manifest["database"],
            PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC=manifest["candidate_topic"],
            PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC=manifest["ordered_topic"],
        ),
        environ={
            "FI_PROPERTY_CATALOG_LEDGER_CH_URL": f"http://127.0.0.1:{manifest['ports']['http']}",
            "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": manifest["database"],
            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "property_catalog_oss_ledger",
            "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "oss-catalog-ledger-local-only",
        },
        client_factory=client_factory,
    )


def run_checks(directory: Path, manifest: dict) -> dict:
    # Called after source_parity configured library-only settings and verified
    # the container's ownership and native port. No Django app/ORM startup.
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog.activation import (
        BuildPlanSourceScope,
    )
    from tracer.services.clickhouse.v2.property_catalog.coordinator import (
        ClickHouseRevisionCoordinator,
    )
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        NativeCatalogClient,
        _planned_streams,
    )
    from tracer.services.clickhouse.v2.property_catalog.installation_bootstrap import (
        inspect_installation,
        resolve_installation,
    )
    from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
        InstallationIdentityError,
        identity_path,
    )
    from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
        FileCatalogMutationSerializer,
    )
    from tracer.services.clickhouse.v2.property_catalog.publisher import (
        PROPERTY_CATALOG_TABLES,
        SharedCatalogDeadline,
    )
    from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
        AtomicMultiTenantFenceFile,
    )

    queries = []

    class ReadClient(ClickHouseClient):
        def execute_read(self, sql, params=None, *, timeout_ms=None, settings=None):
            if not sql.lstrip().upper().startswith("SELECT"):
                raise RuntimeError("identity inspection tried a non-SELECT statement")
            if (
                settings is None
                or settings.get("readonly") != 2
                or not 0 < timeout_ms <= 30_000
                or not 0 < settings.get("max_bytes_to_read", 0) <= 512 << 20
                or not 0 < settings.get("max_result_rows", 0) <= 32
            ):
                raise RuntimeError("identity SQL lost readonly/deadline bounds")
            queries.append(sql)
            return super().execute_read(
                sql, params, timeout_ms=timeout_ms, settings=settings
            )

    cfg = {
        "host": "127.0.0.1",
        "port": manifest["ports"]["native"],
        "user": "property_catalog_oss_control",
        "password": "oss-catalog-control-local-only",
        "database": manifest["database"],
        "pool_size": 1,
        "connect_timeout": 3,
        "send_timeout": 10,
        "receive_timeout": 10,
    }
    kwargs = {
        "environment": "development",
        "target_database": manifest["database"],
        "candidate_topic": manifest["candidate_topic"],
        "ordered_topic": manifest["ordered_topic"],
    }
    probe = ReadClient(
        **cfg,
        server_enforced_readonly=True,
        allow_query_settings_with_server_readonly=True,
    )
    writer = ClickHouseClient(**cfg, server_enforced_readonly=False)
    # Deliberately no epoch, projection or producer-id settings, nor project .env.
    settings_object = SimpleNamespace(
        PROPERTY_CATALOG_DEV_WRITE_CH_HOST="127.0.0.1",
        PROPERTY_CATALOG_DEV_WRITE_CH_PORT=manifest["ports"]["native"],
        PROPERTY_CATALOG_DEV_WRITE_CH_USER=cfg["user"],
        PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD=cfg["password"],
    )

    def counts():
        return {
            table: writer.execute_read(
                f"SELECT count() FROM {manifest['database']}.{table}",
                {},
                timeout_ms=8000,
                settings={"readonly": 2},
            )[0][0][0]
            for table in sorted(PROPERTY_CATALOG_TABLES)
        }

    try:
        empty_counts = counts()
        if any(empty_counts.values()):
            raise RuntimeError("identity fresh test requires six empty tables")
        fresh = inspect_installation(probe, **kwargs)
        if (fresh.catalog_epoch, fresh.projection_version) != (
            1,
            1,
        ) or counts() != empty_counts:
            raise RuntimeError(
                "empty identity inspection changed data or selected wrong initial coordinates"
            )
        fresh_queries = list(queries)
        fresh_runtime = directory / "fresh-identity-runtime"
        fresh_runtime.mkdir(mode=0o700)
        fresh_fence = str(fresh_runtime / "revision-fence-v2.json")
        fresh_path = identity_path(fresh_fence)
        no_version_args = dict(
            settings_object=settings_object,
            prefix="PROPERTY_CATALOG_DEV_",
            revision_fence_file=fresh_fence,
            client_factory=ReadClient,
            **kwargs,
        )
        query_count = len(queries)
        try:
            resolve_installation(readonly=True, **no_version_args)
        except InstallationIdentityError:
            pass
        else:
            raise RuntimeError("missing-file read-only resolution initialized identity")
        if fresh_path.exists() or len(queries) != query_count:
            raise RuntimeError("missing-file read-only resolution performed IO")
        initialized = resolve_installation(**no_version_args)
        initial_bytes = fresh_path.read_bytes()
        query_count = len(queries)
        for readonly in (False, True):
            if (
                resolve_installation(readonly=readonly, **no_version_args)
                != initialized
            ):
                raise RuntimeError("fresh no-version resolver restart changed identity")
        if (
            (initialized.catalog_epoch, initialized.projection_version) != (1, 1)
            or fresh_path.read_bytes() != initial_bytes
            or len(queries) != query_count
            or counts() != empty_counts
        ):
            raise RuntimeError("fresh no-version initialization changed catalog data")
        runtime = directory / "identity-runtime"
        runtime.mkdir(mode=0o700)
        fence_file = str(runtime / "revision-fence-v2.json")
        now = datetime.now(UTC)
        fixture = json.loads((directory / "source-fixture.json").read_text())
        organization = fixture["organization_id"]
        workspace = fixture["workspace_id"]
        project = fixture["project_id"]
        since = datetime.fromisoformat(fixture["since"])
        until = datetime.fromisoformat(fixture["until"])
        token, hot = (str(uuid4()) for _ in range(2))
        streams = _planned_streams(
            build_token=token,
            hot_producer_stream_id=hot,
            postgres_source_fence=int(now.timestamp() * 1_000_000),
            span_audit_generation=int(now.timestamp() * 1_000_000),
        )
        coordinator = ClickHouseRevisionCoordinator(
            NativeCatalogClient(writer, database=manifest["database"]),
            database=manifest["database"],
            serializer=FileCatalogMutationSerializer(str(runtime)),
            producer_fence_sink=AtomicMultiTenantFenceFile(fence_file),
            hot_producer_stream_id=hot,
            deadline=SharedCatalogDeadline(wall_ms=60_000),
            lease_seconds=120,
        )
        # This is a real finite reservation, never a fabricated active build.
        # Non-default coordinates make an accidental fresh/default fallback visible.
        lease = coordinator.allocate(
            organization_id=organization,
            workspace_id=workspace,
            catalog_epoch=7,
            projection_version=3,
            build_token=token,
            source_scope=BuildPlanSourceScope(
                (project,),
                int(since.timestamp() * 1_000_000),
                int(until.timestamp() * 1_000_000),
            ),
            planned_streams=streams,
            now=now,
        )
        before = counts()
        queries.clear()
        adopted = inspect_installation(probe, **kwargs)
        if (
            adopted.catalog_epoch,
            adopted.projection_version,
            adopted.producer_stream_id,
        ) != (7, 3, hot):
            raise RuntimeError(
                "nonempty control-plan inspection did not adopt exact identity"
            )
        if not any("ARRAY JOIN JSONExtractArrayRaw" in sql for sql in queries):
            raise RuntimeError(
                "real ARRAY JOIN/JSONExtract identity query was not exercised"
            )
        if counts() != before or before["property_catalog_activations"] != 0:
            raise RuntimeError(
                "identity inspection mutated metadata or activated a build"
            )
        resolved = resolve_installation(
            settings_object=settings_object,
            prefix="PROPERTY_CATALOG_DEV_",
            revision_fence_file=fence_file,
            client_factory=ReadClient,
            **kwargs,
        )
        path = identity_path(fence_file)
        persisted = path.read_bytes()
        query_count = len(queries)
        restarted = resolve_installation(
            settings_object=settings_object,
            prefix="PROPERTY_CATALOG_DEV_",
            revision_fence_file=fence_file,
            client_factory=ReadClient,
            readonly=True,
            **kwargs,
        )
        if (
            resolved != adopted
            or restarted != adopted
            or path.read_bytes() != persisted
            or len(queries) != query_count
        ):
            raise RuntimeError(
                "persisted/read-only identity restart changed identity or queried/wrote storage"
            )
        report = {
            "status": "passed",
            "fresh_table_counts": empty_counts,
            "fresh_inspection_queries": fresh_queries,
            "fresh_no_version_resolver_initialization": True,
            "fresh_identity_producer_stream_id": initialized.producer_stream_id,
            "fresh_identity_restart_byte_stable": True,
            "missing_identity_readonly_refused_without_sql": True,
            "adoption_queries": queries,
            "adopted_epoch": adopted.catalog_epoch,
            "adopted_projection": adopted.projection_version,
            "adopted_producer_stream_id": adopted.producer_stream_id,
            "reserved_revision": lease.catalog_revision,
            "build_token": token,
            "build_plan_sha256": lease.build_lease_sha256,
            "lease_expires_at": lease.expires_at.isoformat(),
            "planned_stream_count": len(streams),
            "source_scope": fixture,
            "revision_fence_file": fence_file,
            "identity_file": str(path),
            "reservation_lease_seconds": int(
                (lease.expires_at - lease.issued_at).total_seconds()
            ),
            "post_reservation_table_counts": before,
            "identity_restart_byte_stable": True,
            "inspection_select_only": True,
            "activation_rows": 0,
            "lifecycle_qualified": False,
        }
        if manifest.get("ordered_chain"):
            # Admission is initialized only after the installed identity and
            # its read-only restart have been checked, before either child runs.
            _prepare_write_admission(
                settings_object, fence_file, manifest, client_factory=ClickHouseClient
            )
            for stream in lease.build_plan.streams:
                coordinator.open_stream(
                    lease=lease,
                    source_adapter=stream.source_adapter,
                    producer_stream_id=stream.producer_stream_id,
                )
            coordinator.publish_building_assignment(lease=lease)
            report["opened_stream_count"] = len(lease.build_plan.streams)
            report["building_fence_published"] = True
            report["post_open_table_counts"] = counts()
        save(directory / "identity-evidence.json", report)
        return report
    finally:
        probe.close()
        writer.close()
