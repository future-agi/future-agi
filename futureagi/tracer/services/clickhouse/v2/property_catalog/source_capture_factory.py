"""Default managed source wiring; no operator revision or source-table knobs.

Fresh work resolves the configured source into an admitted direct member before
discovery. Resume uses the original persisted member, not a new LB sample. The
live watcher and captured scanner share credentials, never their table binding.
Construction is lazy: a fenced build can finish without scanning live spans.
"""

from dataclasses import replace

from .source_capture import SourceCaptureError


class PinnedSourceDriver:
    """Read-only native adapter that proves the actual socket on every read."""

    def __init__(self, driver, member):
        if getattr(driver, "server_enforced_readonly", None) is not True:
            raise SourceCaptureError("captured reads require the source readonly role")
        self._driver, self._member = driver, member
        self.database, self.user = driver.database, driver.user
        self.host, self.port = driver.host, driver.port
        self.server_enforced_readonly = True

    def execute_read(self, sql, params, *, timeout_ms, settings=None):
        from .native_read_transport import read_once

        # readonly=1 locks settings at the server. Do not turn it into readonly=2
        # or forward mutable per-query settings to get around that profile.
        if (
            self._driver.database != self.database
            or self._driver.user != self.user
            or self._driver.host != self.host
            or self._driver.port != self.port
            or self._driver.server_enforced_readonly is not True
        ):
            raise SourceCaptureError("pinned source driver identity changed")
        rows, columns = read_once(
            self._driver,
            member=self._member,
            database=self.database,
            user=self.user,
            sql=sql,
            params=params,
            timeout_ms=timeout_ms,
        )
        return rows, columns, None


class _LazyLiveSourceDriver:
    def __init__(self, owner):
        self._owner = owner
        config = owner.config.source
        self.database, self.user = config.database, config.user
        self.server_enforced_readonly = True

    def execute_read(self, *args, **kwargs):
        return self._owner.live_driver().execute_read(*args, **kwargs)


class ManagedSourceCaptureFactory:
    """Factory-owned handles; all late-created connections join the exit stack."""

    def __init__(
        self, *, config, writer, source_driver, native_client_factory, resources
    ):
        self.config, self.writer = config, writer
        self._original = source_driver
        self._native = native_client_factory
        self._resources = resources
        self._live = None
        self._metadata = None
        self._readers = {}
        self._writers = {}
        self.source_driver = _LazyLiveSourceDriver(self)

    def _new(self, connection):
        driver = self._native(connection)
        self._resources.callback(driver.close)
        return driver

    def _route(self, server_uuid):
        proof = self.writer.proof
        members = [m for m in proof.admission.members if m.server_uuid == server_uuid]
        if len(members) != 1:
            raise SourceCaptureError("original captured source member is not admitted")
        member = members[0]
        connections = [c for c in proof.connections if c.name == member.name]
        if len(connections) != 1:
            raise SourceCaptureError(
                "original source member has no unique direct route"
            )
        return member, connections[0].driver

    def live_driver(self):
        if self._live is None:
            from .source_capture_route import resolve_capture_source

            def source_connection(connection):
                return self._new(
                    replace(
                        self.config.source,
                        host=connection.driver.host,
                        port=connection.driver.port,
                    )
                )

            member, driver, metadata = resolve_capture_source(
                self._original,
                source_database=self.config.source.database,
                admission=self.writer.proof.admission,
                connections=self.writer.proof.connections,
                driver_factory=source_connection,
            )
            self._live = PinnedSourceDriver(driver, member)
            self._readers[(member.server_uuid, self.config.source.database)] = (
                self._live
            )
            self._metadata = metadata
        return self._live

    def metadata(self):
        self.live_driver()
        return dict(self._metadata)

    def source_for(self, spec, *, captured=False):
        member, route = self._route(spec.source_server_uuid)
        database = spec.capture_database if captured else spec.source_database
        key = (spec.source_server_uuid, database)
        if key not in self._readers:
            raw = self._new(
                replace(
                    self.config.source,
                    host=route.host,
                    port=route.port,
                    database=database,
                )
            )
            self._readers[key] = PinnedSourceDriver(raw, member)
        return self._readers[key]

    def backend(self, spec, schema):
        from .source_capture_capacity import SourceCaptureCapacity
        from .source_capture_native import NativeSourceCaptureBackend

        member, route = self._route(spec.source_server_uuid)
        key = (spec.source_server_uuid, spec.source_database)
        if key not in self._writers:
            # Catalog writer has narrow derived-capture DDL plus SELECT spans;
            # the SOURCE reader never gets write credentials or permissions.
            self._writers[key] = self._new(
                replace(
                    self.config.catalog,
                    host=route.host,
                    port=route.port,
                    database=spec.source_database,
                )
            )
        source = self.source_for(spec)
        capacity = SourceCaptureCapacity(spec, source)
        return NativeSourceCaptureBackend(
            self._writers[key],
            source_reader=source,
            member=member,
            spec=spec,
            schema=schema,
            budget=capacity.resource_budget(),
            capacity_reservation=capacity,
        )

    def build(self, *, live_reader, deadline, now):
        from .dev_runtime import NativeSourceClient
        from .durable_lifecycle import FreshSpanLifecycleCutoffFreezer
        from .source_capture_runtime import (
            CaptureAwareCutoffFreezer,
            LifecycleSourceCapture,
        )
        from .span_source import CanonicalSpanSourceReader

        def captured_reader(spec):
            source_client = NativeSourceClient(
                self.source_for(spec, captured=True),
                source_database=spec.capture_database,
                source_table=spec.capture_table,
                catalog_database=self.config.catalog.database,
                explicit_initial_backfill=self.config.explicit_initial_backfill_wall,
            )
            return CanonicalSpanSourceReader(
                source_client,
                source_database=spec.capture_database,
                source_table=spec.capture_table,
                catalog_database=self.config.catalog.database,
                deadline=deadline,
                timeout_ms=self.config.span_query_timeout_ms,
                page_rows=self.config.span_page_rows,
                explicit_initial_backfill=self.config.explicit_initial_backfill_wall,
            )

        freezer = CaptureAwareCutoffFreezer(
            FreshSpanLifecycleCutoffFreezer(live_reader, now=now), live_reader
        )
        capture = LifecycleSourceCapture(
            directory=self.config.mutation_lock_directory,
            installation_id=self.writer.proof.identity.producer_stream_id,
            source_database=self.config.source.database,
            catalog_database=self.config.catalog.database,
            metadata_loader=self.metadata,
            live_reader=live_reader,
            backend_factory=self.backend,
            reader_factory=captured_reader,
            can_retire=lambda spec: False,
            cutoff_freezer=freezer,
        )
        return capture, freezer
