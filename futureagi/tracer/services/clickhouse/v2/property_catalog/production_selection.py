"""Serialized production selection on the lifecycle owner's shared spool PVC.

Every production control writer must use this lane and the same mounted runtime
directory. ClickHouse's predecessor check is not atomic CAS. Never remove lock
files or abandon an unresolved intent; neither a local fallback nor a TTL is safe.
An acknowledged event remains on disk as a monotonic receipt until superseded.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .activation_control import (
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlHead,
    ActivationControlRejected,
    ActivationControlRequest,
    ActivationControlScope,
    ActivationControlTarget,
    ClickHouseActivationControlStore,
    PropertyCatalogActivationControlPlane,
    _event_from_row,
    canonical_control_events,
    canonical_qualified_activations,
)
from .codec import canonical_json, canonical_uuid
from .publisher import require_prod_catalog_database
from .wire import ZERO_SHA256


@contextmanager
def selection_lock(
    *,
    runtime_directory: str,
    database: str,
    scope: ActivationControlScope,
    timeout_seconds: float = 5.0,
) -> Iterator[Path]:
    """Hold the shared workspace mutex; yield its durable pending-intent path."""
    root = Path(runtime_directory)
    if not root.is_absolute() or not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError(
            "selection requires an existing resolved absolute runtime_directory"
        )
    if not 0 < timeout_seconds <= 5:
        raise ValueError(
            "selection lock timeout must be positive and at most five seconds"
        )
    require_prod_catalog_database(database)
    key = f"{database}:{scope.organization_id}:{scope.workspace_id}"
    stem = "production-selection-" + hashlib.sha256(key.encode()).hexdigest()
    lock = root / (stem + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        _checked_file(fd)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ActivationControlRejected("selection_lock_busy") from None
                time.sleep(min(0.05, remaining))
        info = _checked_file(fd)
        named = lock.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise ValueError("selection lock inode changed")
        yield root / (stem + ".json")
    finally:
        os.close(fd)


def _checked_file(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError(
            "selection file must be singly linked, owned by this uid, and mode 0600"
        )
    return info


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_intent(
    path: Path,
    event: ActivationControlEvent,
    head: ActivationControlHead | None,
    database: str,
) -> None:
    row = event.as_row()
    row["controlled_at"] = event.controlled_at.isoformat()
    payload = canonical_json(
        {
            "version": 1,
            "database": database,
            "event": row,
            "expected_head": asdict(head) if head else None,
        },
        max_bytes=16384,
    )
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_intent(
    path: Path, database: str
) -> tuple[ActivationControlEvent, ActivationControlHead | None] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = _checked_file(handle.fileno())
            if info.st_size > 16384:
                raise ValueError("invalid intent file")
            value = json.loads(handle.read(16385))
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "database", "event", "expected_head"}
            or value["version"] != 1
            or value["database"] != database
        ):
            raise ValueError("invalid intent envelope")
        event = _event_from_row(value["event"])
        raw_head = value["expected_head"]
        head = (
            None
            if raw_head is None
            else ActivationControlHead(
                **{
                    **raw_head,
                    "action": ActivationControlAction(raw_head["action"]),
                    "target": ActivationControlTarget(**raw_head["target"]),
                }
            )
        )
        if (
            event.control_sequence != (head.control_sequence + 1 if head else 1)
            or event.previous_control_sha256
            != (head.control_sha256 if head else ZERO_SHA256)
            or (head and head.target.scope != event.target.scope)
        ):
            raise ValueError("invalid intent predecessor")
        return event, head
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationControlRejected("selection_intent_invalid") from exc


def _result(
    status: str,
    target: ActivationControlTarget | None,
    event: ActivationControlEvent | None = None,
    *,
    idempotent: bool = False,
) -> dict[str, Any]:
    result = {
        "status": status,
        "catalog_revision": target.catalog_revision if target else None,
    }
    if event:
        result.update(
            request_id=event.request_id,
            control_sequence=event.control_sequence,
            idempotent=idempotent,
            selected_target=asdict(target) if target else None,
        )
    return result


def _publish_locked(
    *,
    store: Any,
    scope: ActivationControlScope,
    catalog_epoch: int,
    projection_version: int,
    intent_path: Path,
    database: str,
    verify_target: Callable[[], ActivationControlTarget | None],
    now: datetime,
    request_id: str | None = None,
    initial_only: bool = False,
) -> dict[str, Any]:
    """Store/test seam. Caller MUST hold selection_lock for this exact scope."""
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("selection time must be aware UTC")
    if request_id is not None:
        request_id = canonical_uuid(request_id, field="request_id")
    events = canonical_control_events(store.list_control_events(scope), scope=scope)
    head = events[-1].head if events else None
    pending = _read_intent(intent_path, database)
    acknowledged = None
    if pending:
        event, expected_head = pending
        if event.target.scope != scope:
            raise ActivationControlRejected("selection_intent_mismatch")
        replay = next(
            (item for item in events if item.request_id == event.request_id), None
        )
        if replay is not None:
            if replay != event:
                raise ActivationControlRejected("control_request_id_conflict")
            # Resolved in memory, retained on disk: a later replica must prove it
            # before a fresh UUID can ever replace this exact durable event.
            acknowledged = event
            pending = None
        else:
            if event.action is not ActivationControlAction.ACTIVATE or (
                expected_head
                and expected_head.action is not ActivationControlAction.ACTIVATE
            ):
                raise ActivationControlRejected("selection_receipt_not_visible")
            if (
                event.target.catalog_epoch != catalog_epoch
                or event.target.projection_version != projection_version
                or (request_id is not None and request_id != event.request_id)
                or (initial_only and expected_head is not None)
            ):
                raise ActivationControlRejected("selection_intent_mismatch")
            if head != expected_head:
                raise ActivationControlRejected("control_stale")
    # Adopt a pre-existing/newer observed head as well, including operator holds.
    # Otherwise observing DISABLE then switching to an older replica could undo it.
    if not pending and events and events[-1] != acknowledged:
        _write_intent(
            intent_path,
            events[-1],
            events[-2].head if len(events) > 1 else None,
            database,
        )
        acknowledged = events[-1]
    if head and head.action is not ActivationControlAction.ACTIVATE:
        return _result("operator_held", head.target)
    if initial_only and acknowledged and acknowledged.request_id == request_id:
        if acknowledged.control_sequence != 1:
            raise ActivationControlRejected("control_request_id_conflict")
        return _result("noop", head.target, acknowledged, idempotent=True)
    target = verify_target()
    if target is None:
        if pending:
            raise ActivationControlRejected("selection_pending_proof_unavailable")
        return _result("nothing", None)
    if (
        not isinstance(target, ActivationControlTarget)
        or target.scope != scope
        or target.catalog_epoch != catalog_epoch
        or target.projection_version != projection_version
    ):
        raise ActivationControlRejected("selection_target_scope_mismatch")
    qualified = canonical_qualified_activations(
        store.list_qualified_activations(scope), scope=scope
    )
    if not qualified or qualified[-1].target != target:
        raise ActivationControlRejected("activate_target_not_latest")
    if not pending and head and head.target != target:
        baseline = next(
            (item for item in qualified if item.target == head.target), None
        )
        if baseline is None:
            raise ActivationControlRejected("control_head_target_not_qualified")
        if qualified[-1].lifecycle_position <= baseline.lifecycle_position:
            raise ActivationControlRejected("activate_target_not_newer")
    if pending:
        if target != event.target:
            raise ActivationControlRejected("selection_pending_target_changed")
    else:
        replay = next((item for item in events if item.request_id == request_id), None)
        if replay is not None:
            if (
                replay.target != target
                or replay.action is not ActivationControlAction.ACTIVATE
                or (initial_only and replay.control_sequence != 1)
            ):
                raise ActivationControlRejected("control_request_id_conflict")
            return _result("noop", head.target, replay, idempotent=True)
        if initial_only and head is not None:
            raise ActivationControlRejected("control_stale")
        if head and head.target == target:
            return _result(
                "noop", target, acknowledged, idempotent=acknowledged is not None
            )
        event = ActivationControlEvent.create(
            control_sequence=head.control_sequence + 1 if head else 1,
            request_id=request_id or str(uuid4()),
            action=ActivationControlAction.ACTIVATE,
            target=target,
            previous_control_sha256=head.control_sha256 if head else ZERO_SHA256,
            controlled_at=now,
        )
        expected_head = head
        _write_intent(intent_path, event, head, database)
    # An exception leaves the exact event durable. No automatic replacement intent.
    result = PropertyCatalogActivationControlPlane(store).activate(
        request=ActivationControlRequest(event.request_id, event.target, expected_head),
        now=event.controlled_at,
    )
    after = canonical_control_events(store.list_control_events(scope), scope=scope)
    if result.event != event or not after or after[-1] != event:
        raise ActivationControlRejected("control_append_not_exact")
    # Keep the exact acknowledged bytes as the predecessor receipt. Deleting it
    # would permit a stale replica after restart to allocate a competing event.
    return _result("published", event.target, event, idempotent=result.idempotent)


def publish_with_store(
    *,
    store: Any,
    scope: ActivationControlScope,
    catalog_epoch: int,
    projection_version: int,
    runtime_directory: str,
    database: str,
    verify_target: Callable[[], ActivationControlTarget | None],
    now: datetime,
    request_id: str | None = None,
    initial_only: bool = False,
) -> dict[str, Any]:
    with selection_lock(
        runtime_directory=runtime_directory, database=database, scope=scope
    ) as path:
        return _publish_locked(
            store=store,
            scope=scope,
            catalog_epoch=catalog_epoch,
            projection_version=projection_version,
            intent_path=path,
            database=database,
            verify_target=verify_target,
            now=now,
            request_id=request_id,
            initial_only=initial_only,
        )


@contextmanager
def _open_control_store(config: Any) -> Iterator[ClickHouseActivationControlStore]:
    # Lazy import avoids controller -> service -> command -> controller cycles.
    from tracer.management.commands.ch25_property_catalog_activate_latest import (
        _ActivationControlClient,
    )
    from tracer.services.clickhouse.client import ClickHouseClient

    from .runtime_limits import RUNTIME_LIMITS

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
    try:
        client = _ActivationControlClient(
            driver,
            database=config.database,
            user=config.user,
            expected_hostnames=config.expected_hostnames,
        )
        yield ClickHouseActivationControlStore(client, database=config.database)
    finally:
        driver.close()


def publish_completed_catalog(
    *,
    settings_object: Any,
    scope: Any,
    verify_target: Callable[[], ActivationControlTarget | None],
    now: datetime,
) -> Mapping[str, Any]:
    """Publish full runtime proof; the controller must enforce status-only first."""
    from tracer.management.commands.ch25_property_catalog_activate_latest import (
        activation_command_config,
    )

    config = activation_command_config(settings_object=settings_object)
    checked_scope = ActivationControlScope(scope.organization_id, scope.workspace_id)
    if (
        config.workspace_scope_mode == "allowlist"
        and checked_scope.workspace_id not in config.workspace_ids
    ):
        raise ActivationControlRejected("selection_workspace_not_allowed")
    runtime_directory = getattr(
        settings_object, "PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY", ""
    )
    with _open_control_store(config) as store:
        return publish_with_store(
            store=store,
            scope=checked_scope,
            catalog_epoch=config.catalog_epoch,
            projection_version=config.projection_version,
            runtime_directory=runtime_directory,
            database=config.database,
            verify_target=verify_target,
            now=now,
        )
