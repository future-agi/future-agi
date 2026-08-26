from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    activation as activation_module,
)
from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationControlRequest,
    ActivationHead,
    ActivationInventory,
    ActivationRecord,
    ActivationRejected,
    ActivationStatus,
    ActivationTarget,
    CatalogLifecycleMode,
    PropertyCatalogActivationControlPlane,
    current_active_activation,
)
from tracer.services.clickhouse.v2.property_catalog.codec import canonical_json
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    RevisionQualification,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER_WORKSPACE = "33333333-3333-4333-8333-333333333333"
AT = datetime(2026, 8, 25, 20, tzinfo=UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token(index: int) -> str:
    return str(uuid.UUID(int=1000 + index))


def _record(
    *,
    revision: int,
    token: str,
    mode: CatalogLifecycleMode,
    anchor: int,
    sequence: int,
    status: ActivationStatus = ActivationStatus.ACTIVE,
    activation_sha256: str | None = None,
) -> ActivationRecord:
    manifest = canonical_json(
        {
            "lifecycle_mode": mode,
            "lineage_anchor_revision": anchor,
        }
    )
    timestamp = AT + timedelta(minutes=sequence)
    return ActivationRecord(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=revision,
        build_token=token,
        projection_version=1,
        lifecycle_mode=mode,
        lineage_anchor_revision=anchor,
        activation_sequence=sequence,
        source_manifest_json=manifest,
        source_manifest_sha256=_sha(manifest),
        revision_fence_sha256=_sha(f"fence:{revision}:{token}"),
        activation_sha256=activation_sha256 or _sha(f"activation:{revision}:{token}"),
        status=status,
        live_definition_rows=revision * 10,
        tombstone_rows=revision,
        value_rows=revision * 100,
        qualified_at=timestamp,
        updated_at=timestamp,
        version=sequence,
    )


def _target(record: ActivationRecord) -> ActivationTarget:
    return ActivationTarget(
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        catalog_epoch=record.catalog_epoch,
        catalog_revision=record.catalog_revision,
        build_token=record.build_token,
        expected_activation_sha256=record.activation_sha256,
    )


class _Store:
    def __init__(self, events: tuple[ActivationRecord, ...] = ()) -> None:
        self.events = list(events)
        self.append_calls: list[tuple[ActivationRecord, ...]] = []
        self.concurrent_event: ActivationRecord | None = None

    def list_activation_events(self, **_scope: Any) -> tuple[ActivationRecord, ...]:
        return tuple(self.events)

    def append_activation_events(
        self,
        records: tuple[ActivationRecord, ...],
        *,
        expected_head: ActivationHead | None,
    ) -> tuple[ActivationRecord, ...]:
        actual_head = (
            ActivationHead.from_record(self.events[-1]) if self.events else None
        )
        if actual_head != expected_head:
            raise ActivationRejected(("activation_control_concurrent",))
        if self.concurrent_event is not None:
            self.events.append(self.concurrent_event)
            self.concurrent_event = None
            raise ActivationRejected(("activation_control_concurrent",))
        self.append_calls.append(records)
        self.events.extend(records)
        return records

    def audit_build_plan(self, **_kwargs: Any) -> None:
        raise AssertionError("qualification is stubbed in these control tests")

    def load_checkpoints(self, _requirement: Any) -> tuple[()]:
        raise AssertionError("qualification is stubbed in these control tests")


def _manifest(
    *,
    revision: int,
    token: str,
    mode: CatalogLifecycleMode,
    anchor: int,
) -> SimpleNamespace:
    manifest_json = canonical_json(
        {
            "lifecycle_mode": mode,
            "lineage_anchor_revision": anchor,
        }
    )
    return SimpleNamespace(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=revision,
        build_token=token,
        projection_version=1,
        lifecycle_mode=mode,
        lineage_anchor_revision=anchor,
        canonical_json=manifest_json,
        sha256=_sha(manifest_json),
    )


def test_qualification_does_not_append_activation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualification = RevisionQualification(True, (), _sha("qualified"))
    checkpoint = SimpleNamespace(
        source_adapter=SourceAdapter.SYSTEM_MANIFEST,
        producer_stream_id=_token(91),
        state_sha256=_sha("checkpoint"),
    )
    store = _Store()
    store.audit_build_plan = lambda **_kwargs: None
    store.load_checkpoints = lambda _requirement: (checkpoint,)
    monkeypatch.setattr(
        activation_module,
        "RevisionBuildPlan",
        SimpleNamespace(from_json=lambda _value: object()),
    )
    monkeypatch.setattr(
        activation_module,
        "qualify_revision",
        lambda _requirement, _checkpoints: qualification,
    )
    monkeypatch.setattr(
        activation_module,
        "_validate_fence",
        lambda *_args, **_kwargs: None,
    )

    result = PropertyCatalogActivationControlPlane(store).qualify(
        manifest=SimpleNamespace(revision_requirement=object()),
        fence=SimpleNamespace(build_plan_json="{}"),
    )

    assert result == qualification
    assert store.events == []
    assert store.append_calls == []


def test_explicit_activation_binds_exact_target_and_digest_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _sha("qualified-build")
    manifest = _manifest(
        revision=1,
        token=_token(1),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
    )
    target = ActivationTarget(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=1,
        build_token=_token(1),
        expected_activation_sha256=digest,
    )
    request = ActivationControlRequest(target=target, expected_head=None)
    store = _Store()
    control = PropertyCatalogActivationControlPlane(store)
    monkeypatch.setattr(
        activation_module,
        "_qualify_control_activation",
        lambda *_args, **_kwargs: RevisionQualification(True, (), digest),
    )

    first = control.activate(
        request=request,
        manifest=manifest,
        fence=SimpleNamespace(fence_sha256=_sha("fence")),
        inventory=ActivationInventory(10, 1, 100),
        now=AT,
    )
    replay = control.activate(
        request=request,
        manifest=manifest,
        fence=SimpleNamespace(fence_sha256=_sha("fence")),
        inventory=ActivationInventory(10, 1, 100),
        now=AT + timedelta(minutes=5),
    )

    assert not first.idempotent
    assert first.active is not None and target.matches(first.active)
    assert replay.idempotent
    assert replay.records == first.records
    assert len(store.append_calls) == 1

    mismatched_targets = (
        replace(target, workspace_id=OTHER_WORKSPACE),
        replace(target, catalog_epoch=2),
        replace(target, catalog_revision=2),
        replace(target, build_token=_token(2)),
    )
    for mismatched_target in mismatched_targets:
        with pytest.raises(ActivationRejected) as mismatch:
            control.activate(
                request=replace(request, target=mismatched_target),
                manifest=manifest,
                fence=SimpleNamespace(fence_sha256=_sha("fence")),
                inventory=ActivationInventory(10, 1, 100),
                now=AT,
            )
        assert mismatch.value.issues == ("activation_target_mismatch",)

    monkeypatch.setattr(
        activation_module,
        "_qualify_control_activation",
        lambda *_args, **_kwargs: RevisionQualification(
            True, (), _sha("different-qualified-build")
        ),
    )
    with pytest.raises(ActivationRejected) as digest_mismatch:
        PropertyCatalogActivationControlPlane(_Store()).activate(
            request=request,
            manifest=manifest,
            fence=SimpleNamespace(fence_sha256=_sha("fence")),
            inventory=ActivationInventory(10, 1, 100),
            now=AT,
        )
    assert digest_mismatch.value.issues == ("activation_digest_mismatch",)


def test_disable_appends_disabled_states_for_every_active_build_without_fallback() -> (
    None
):
    anchor = _record(
        revision=1,
        token=_token(1),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
        sequence=1,
    )
    current = _record(
        revision=2,
        token=_token(2),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=1,
        sequence=2,
    )
    store = _Store((anchor, current))
    request = ActivationControlRequest(
        target=_target(current),
        expected_head=ActivationHead.from_record(current),
    )
    control = PropertyCatalogActivationControlPlane(store)

    first = control.disable(request=request, now=AT + timedelta(hours=1))
    replay = control.disable(request=request, now=AT + timedelta(hours=2))

    assert [record.status for record in first.records] == [
        ActivationStatus.DISABLED,
        ActivationStatus.DISABLED,
    ]
    assert [record.activation_sequence for record in first.records] == [3, 4]
    assert first.active is None
    assert current_active_activation(store.events) is None
    assert anchor.status is ActivationStatus.ACTIVE
    assert current.status is ActivationStatus.ACTIVE
    assert replay.idempotent
    assert replay.records == first.records
    assert len(store.append_calls) == 1


def test_rollback_appends_prior_qualified_lineage_and_disables_newer_build() -> None:
    anchor = _record(
        revision=1,
        token=_token(1),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
        sequence=1,
    )
    target = _record(
        revision=2,
        token=_token(2),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=1,
        sequence=2,
    )
    newer = _record(
        revision=3,
        token=_token(3),
        mode=CatalogLifecycleMode.FULL_REPAIR,
        anchor=3,
        sequence=3,
    )
    store = _Store((anchor, target, newer))
    request = ActivationControlRequest(
        target=_target(target),
        expected_head=ActivationHead.from_record(newer),
    )
    control = PropertyCatalogActivationControlPlane(store)

    first = control.rollback(request=request, now=AT + timedelta(hours=1))
    replay = control.rollback(request=request, now=AT + timedelta(hours=2))

    assert [(record.catalog_revision, record.status) for record in first.records] == [
        (3, ActivationStatus.DISABLED),
        (2, ActivationStatus.ACTIVE),
    ]
    assert first.active is not None and request.target.matches(first.active)
    assert current_active_activation(store.events) == first.active
    assert replay.idempotent
    assert replay.records == first.records
    assert len(store.append_calls) == 1


def test_rollback_after_disable_reactivates_the_complete_prior_lineage() -> None:
    anchor = _record(
        revision=1,
        token=_token(1),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
        sequence=1,
    )
    target = _record(
        revision=2,
        token=_token(2),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=1,
        sequence=2,
    )
    store = _Store((anchor, target))
    control = PropertyCatalogActivationControlPlane(store)
    disabled = control.disable(
        request=ActivationControlRequest(
            target=_target(target),
            expected_head=ActivationHead.from_record(target),
        ),
        now=AT + timedelta(hours=1),
    )
    assert disabled.active is None

    rolled_back = control.rollback(
        request=ActivationControlRequest(
            target=_target(target),
            expected_head=ActivationHead.from_record(disabled.records[-1]),
        ),
        now=AT + timedelta(hours=2),
    )

    assert [record.catalog_revision for record in rolled_back.records] == [1, 2]
    assert all(
        record.status is ActivationStatus.ACTIVE for record in rolled_back.records
    )
    assert rolled_back.active is not None and _target(rolled_back.active) == _target(
        target
    )


@pytest.mark.parametrize(
    ("events", "target", "expected_issue"),
    [
        (
            (
                _record(
                    revision=1,
                    token=_token(1),
                    mode=CatalogLifecycleMode.INITIAL_BACKFILL,
                    anchor=1,
                    sequence=1,
                ),
            ),
            ActivationTarget(ORG, WORKSPACE, 1, 9, _token(9), _sha("unknown")),
            "rollback_target_unknown",
        ),
        (
            (
                _record(
                    revision=1,
                    token=_token(1),
                    mode=CatalogLifecycleMode.INITIAL_BACKFILL,
                    anchor=1,
                    sequence=1,
                    status=ActivationStatus.DISABLED,
                ),
            ),
            ActivationTarget(
                ORG,
                WORKSPACE,
                1,
                1,
                _token(1),
                _sha(f"activation:1:{_token(1)}"),
            ),
            "rollback_target_not_qualified",
        ),
    ],
)
def test_rollback_rejects_unknown_and_unqualified_builds(
    events: tuple[ActivationRecord, ...],
    target: ActivationTarget,
    expected_issue: str,
) -> None:
    request = ActivationControlRequest(
        target=target,
        expected_head=ActivationHead.from_record(events[-1]),
    )

    with pytest.raises(ActivationRejected) as rejected:
        PropertyCatalogActivationControlPlane(_Store(events)).rollback(
            request=request,
            now=AT + timedelta(hours=1),
        )

    assert rejected.value.issues == (expected_issue,)


def test_control_plane_rejects_conflicting_stale_and_concurrent_requests() -> None:
    anchor = _record(
        revision=1,
        token=_token(1),
        mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        anchor=1,
        sequence=1,
    )
    current = _record(
        revision=2,
        token=_token(2),
        mode=CatalogLifecycleMode.INCREMENTAL,
        anchor=1,
        sequence=2,
    )
    concurrent = _record(
        revision=3,
        token=_token(3),
        mode=CatalogLifecycleMode.FULL_REPAIR,
        anchor=3,
        sequence=3,
    )
    request = ActivationControlRequest(
        target=_target(current),
        expected_head=ActivationHead.from_record(current),
    )

    stale_store = _Store((anchor, current, concurrent))
    with pytest.raises(ActivationRejected) as stale:
        PropertyCatalogActivationControlPlane(stale_store).disable(
            request=request,
            now=AT + timedelta(hours=1),
        )
    assert stale.value.issues == ("activation_control_stale",)
    assert len(stale_store.events) == 3

    concurrent_store = _Store((anchor, current))
    concurrent_store.concurrent_event = concurrent
    with pytest.raises(ActivationRejected) as raced:
        PropertyCatalogActivationControlPlane(concurrent_store).disable(
            request=request,
            now=AT + timedelta(hours=1),
        )
    assert raced.value.issues == ("activation_control_concurrent",)
    assert all(
        record.status is ActivationStatus.ACTIVE for record in concurrent_store.events
    )
    assert concurrent_store.append_calls == []

    conflicting = replace(
        current,
        build_token=_token(22),
        activation_sequence=3,
        version=3,
        revision_fence_sha256=_sha("conflicting-fence"),
        activation_sha256=_sha("conflicting-activation"),
        qualified_at=AT + timedelta(minutes=3),
        updated_at=AT + timedelta(minutes=3),
    )
    conflict_store = _Store((anchor, current, conflicting))
    with pytest.raises(ActivationRejected) as conflict:
        PropertyCatalogActivationControlPlane(conflict_store).rollback(
            request=ActivationControlRequest(
                target=_target(current),
                expected_head=ActivationHead.from_record(conflicting),
            ),
            now=AT + timedelta(hours=1),
        )
    assert conflict.value.issues == ("activation_revision_conflict",)
