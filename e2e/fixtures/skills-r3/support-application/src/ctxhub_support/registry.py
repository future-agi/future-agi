"""Real SDK consumption with session-bound manifests, never mutable-label resume."""

from __future__ import annotations

import re
from dataclasses import asdict
from uuid import UUID, uuid4

from fi.context.client import ContextSnapshot
from fi.context.sessions import ContextSessionClient
from fi.context.snapshot import PAGE_SIZE, ManifestCollector, SnapshotIntegrityError
from fi.context.source import ConsumptionSource, validate_access
from fi.platform import RequestOptions

from .store import digest


class Registry:
    def __init__(self, client, binding_id, runtime_digest, *, context_id=None):
        self.sessions = (ContextSessionClient(client, context_id=context_id, binding_id=binding_id)
                         if context_id else None)
        self.live_snapshot = None
        self.client = client
        self.binding_id = str(UUID(binding_id))
        self.runtime_digest = runtime_digest
        self.authority = {
            "binding_id": self.binding_id,
            "origin": client._transport.config.base_url,
        }
        self.source = ConsumptionSource(client, kind="agent_context")
        self.prefix = f"/model-hub/skills/contexts/bindings/{self.binding_id}"

    def open(self, thread, incarnation, prior=None, operation_id=None, on_session=None):
        response = self.client.request(
            "POST",
            self.prefix + "/sessions/",
            body={
                "client_session_key": thread,
                "incarnation_id": incarnation,
                "resume_session_id": prior["session_id"] if prior else None,
                "adapter_version": "ctxhub-support/0.1.0",
                "runtime_version": "python/3.11",
                "runtime_config_digest": self.runtime_digest,
            },
            options=RequestOptions(
                headers={"Idempotency-Key": operation_id or str(uuid4())}
            ),
        )
        if prior and any(
            response[key] != prior[key]
            for key in ("session_id", "version_id", "version", "digest")
        ):
            raise SnapshotIntegrityError("Resume changed the original session identity")
        path = response["manifest_url"]
        # Do not let a returned URL redirect the caller's context credential elsewhere.
        match = re.fullmatch(
            r"/model-hub/skills/contexts/([a-f0-9-]{36})/versions/([0-9]+)/manifest/",
            path,
        )
        if not match or int(match[2]) != response["version"]:
            raise SnapshotIntegrityError("Invalid session manifest path")
        if on_session is not None:
            # Persist the mapping before file reads can fail or the lease can expire.
            on_session(response)
        if self.sessions:
            if match[1] != self.sessions.context_id:
                raise SnapshotIntegrityError("Unexpected bound context")
            self.live_snapshot = self.sessions.snapshot(response)
            return response, self.live_snapshot.read_all()
        options = RequestOptions(resolution_id=response["resolution_id"])
        manifest = self.client.request(
            "GET",
            path,
            query={"file_offset": 0, "file_limit": PAGE_SIZE},
            options=options,
        )
        collector = ManifestCollector(manifest, kind="agent_context")
        if (
            collector.identity.resource_id != match[1]
            or collector.identity.version_id != response["version_id"]
            or collector.identity.digest != response["digest"]
            or collector.lease.resolution_id != response["resolution_id"]
        ):
            raise SnapshotIntegrityError("Session and manifest identities differ")
        while collector.next_offset is not None:
            collector.add(
                self.source.manifest(
                    collector.identity, offset=collector.next_offset, options=options
                )
            )
        snapshot = ContextSnapshot(collector.finish(), self.source).read_all()
        self.authorize(response, snapshot)
        return response, snapshot

    def read_skill_file(self, snapshot, path):
        if path not in snapshot.files:
            return None
        if self.live_snapshot is not None:
            return self.live_snapshot.read_file(path)
        return snapshot.files[path]

    def authorize(self, session, snapshot):
        snapshot.manifest.lease.require_live()
        response = self.client.request(
            "POST",
            self.prefix + f"/sessions/{UUID(session['session_id'])}/access-check/",
            body={},
        )
        validate_access(response, snapshot.identity)
        snapshot.manifest.lease.require_live()


def snapshot_lock(snapshot):
    """SDK verified complete immutable closure. Not an install or delivery claim."""
    return {
        "schema": 1,
        "identity": asdict(snapshot.identity),
        "files": [asdict(file) for file in snapshot.manifest.files],
        "closure_digest": digest(dict(snapshot.files)),
    }
