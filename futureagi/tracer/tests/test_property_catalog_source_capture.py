"""Real durable journal/locks with bounded, explicit fake native outcomes."""

from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    DurableSourceCapture,
    SourceCaptureError,
    SourceCaptureObservation,
    SourceCaptureRetired,
    SourceCaptureSpec,
    SourceCaptureUncertain,
)


def specification(**changes):
    values = {
        "installation_id": "227be047-0ec0-49f0-ab11-c303b179cfa1",
        "organization_id": "93a394b9-d791-45d1-8a3a-3c4598043300",
        "workspace_id": "8e40661e-0bf7-491f-8f7e-d87df94c4940",
        "build_token": "1821e3b0-7eb4-4f1b-ad1c-ce53ff0b657d",
        "source_server_uuid": "ce5cae0f-6880-42bf-93b6-176e25898611",
        "source_database": "default",
        "catalog_database": "property_catalog_dev",
        "source_table_uuid": "5a207528-aeb8-4c15-93b7-61554560da40",
        "source_schema_sha256": "a" * 64,
        "capture_schema_sha256": "b" * 64,
    }
    return SourceCaptureSpec(**(values | changes))


class Backend:
    def __init__(self):
        self.observation = None
        self.events = []
        self.ensure_fault = False
        self.attach_fault = False
        self.drop_fault = False
        self.source_fault = False
        self.capacity_fault = False
        self.manager = None

    def verify_source(self, spec):
        self.events.append("verify_source")
        if self.source_fault:
            raise SourceCaptureError("source identity changed")

    def check_capacity(self, spec):
        self.events.append("capacity")
        if self.capacity_fault:
            raise SourceCaptureError("capacity unavailable")

    def create_empty_once(self, spec, *, before_send):
        self.events.append("ensure")
        assert self.manager._load(spec)["phase"] == "create_prepared"
        before_send()
        assert self.manager._load(spec)["phase"] == "creating"
        if self.observation is None:
            self.observation = SourceCaptureObservation(
                spec.source_server_uuid,
                spec.capture_database,
                spec.capture_table,
                spec.capture_table_uuid,
                spec.capture_schema_sha256,
                "c" * 64,
                0,
                0,
                0,
            )
        if self.ensure_fault:
            self.ensure_fault = False
            raise OSError("CREATE reply was lost")

    def inspect(self, spec):
        self.events.append("inspect")
        return self.observation

    def attach_once(self, spec, *, query_id):
        self.events.append("attach")
        assert self.manager._load(spec)["phase"] == "attaching"
        assert query_id == spec.query_id
        self.observation = replace(
            self.observation, parts=2, rows=6, bytes_on_disk=256, parts_sha256="d" * 64
        )
        if self.attach_fault:
            raise OSError("ATTACH reply was lost")

    def drop_owned(self, spec, *, require_empty=False):
        assert self.manager._load(spec)["phase"] in {"retiring", "abandoned_create"}
        if self.observation is None:
            return
        self.observation.bind(spec)
        if require_empty and (
            self.observation.parts
            or self.observation.rows
            or self.observation.bytes_on_disk
        ):
            raise SourceCaptureError(
                "unacknowledged CREATE unexpectedly contains parts"
            )
        self.events.append("drop")
        self.observation = None
        if self.drop_fault:
            raise OSError("DROP reply was lost")


@pytest.fixture
def case(tmp_path):
    backend = Backend()
    finished = set()
    manager = DurableSourceCapture(
        str(tmp_path), backend, can_retire=lambda spec: spec.build_token in finished
    )
    backend.manager = manager
    return manager, backend, specification(), finished


def restart(case, tmp_path):
    _, backend, spec, finished = case
    manager = DurableSourceCapture(
        str(tmp_path), backend, can_retire=lambda value: value.build_token in finished
    )
    backend.manager = manager
    return manager, backend, spec, finished


def test_durable_completion_reuses_exact_capture_after_restart(case, tmp_path):
    manager, backend, spec, _ = case
    original = manager.acquire(spec)
    assert backend.events.count("attach") == 1
    manager, _, _, _ = restart(case, tmp_path)
    backend.events.clear()
    assert manager.acquire(spec) == original
    assert backend.events == ["inspect"]  # No source re-scan or repeated ATTACH.


def test_lost_create_response_never_creates_or_attaches_again(case, tmp_path):
    manager, backend, spec, _ = case
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert backend.events.count("attach") == 0
    manager, _, _, _ = restart(case, tmp_path)
    with pytest.raises(SourceCaptureUncertain, match="CREATE"):
        manager.acquire(spec)
    assert backend.events.count("ensure") == 1
    assert backend.events.count("attach") == 0
    manager.retire(spec)
    assert manager._load(spec)["phase"] == "abandoned_create"
    assert backend.observation is None


def test_uncertain_attach_is_not_inferred_complete_from_nonempty_table(case, tmp_path):
    manager, backend, spec, _ = case
    backend.attach_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert backend.observation.rows == 6
    manager, _, _, _ = restart(case, tmp_path)
    with pytest.raises(SourceCaptureUncertain):
        manager.acquire(spec)
    assert backend.events.count("attach") == 1
    manager.retire(spec)  # Never exposed; synchronous exact-owned discard only.
    assert backend.observation is None
    with pytest.raises(SourceCaptureRetired):
        manager.acquire(spec)


def test_drop_reply_loss_is_resolved_without_recreating_target(case, tmp_path):
    manager, backend, spec, finished = case
    manager.acquire(spec)
    finished.add(spec.build_token)
    backend.drop_fault = True
    with pytest.raises(OSError):
        manager.retire(spec)
    assert manager._load(spec)["phase"] == "retiring"
    manager, _, _, _ = restart(case, tmp_path)
    manager.retire(spec)
    assert backend.events.count("drop") == 1
    assert backend.events.count("attach") == 1
    assert manager._load(spec)["phase"] == "retired"


def test_unfinished_capture_is_not_reclaimed(case):
    manager, backend, spec, _ = case
    manager.acquire(spec)
    with pytest.raises(SourceCaptureError, match="unfinished"):
        manager.retire(spec)
    assert "drop" not in backend.events


@pytest.mark.parametrize(
    "field,value",
    [
        ("parts_sha256", "e" * 64),
        ("rows", 7),
        ("bytes_on_disk", 300),
        ("table_uuid", "f298399c-0b66-4239-a198-b097da9be5dc"),
        ("server_uuid", "f298399c-0b66-4239-a198-b097da9be5dc"),
        ("schema_sha256", "e" * 64),
        ("database", "other"),
        ("table", "other"),
    ],
)
def test_changed_capture_fails_without_repairing_or_replaying(case, field, value):
    manager, backend, spec, _ = case
    manager.acquire(spec)
    backend.observation = replace(backend.observation, **{field: value})
    with pytest.raises(SourceCaptureError):
        manager.acquire(spec)
    assert backend.events.count("attach") == 1
    assert "drop" not in backend.events


def test_missing_capture_after_restart_is_not_rebuilt_under_old_build(case):
    manager, backend, spec, _ = case
    manager.acquire(spec)
    backend.observation = None
    with pytest.raises(SourceCaptureError, match="absent"):
        manager.acquire(spec)
    assert backend.events.count("ensure") == 1


@pytest.mark.parametrize(
    "change",
    [
        {"source_schema_sha256": "e" * 64},
        {"capture_schema_sha256": "e" * 64},
        {"source_database": "other"},
        {"source_server_uuid": "f298399c-0b66-4239-a198-b097da9be5dc"},
    ],
)
def test_contract_change_cannot_hide_prior_uncertain_attach(case, change):
    manager, backend, spec, _ = case
    backend.attach_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    with pytest.raises(SourceCaptureError, match="journal identity"):
        manager.acquire(replace(spec, **change))
    assert backend.events.count("attach") == 1


def test_wrong_target_cannot_be_deleted_on_uncertain_recovery(case):
    manager, backend, spec, _ = case
    backend.attach_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    backend.observation = replace(backend.observation, table_uuid=str(uuid4()))
    with pytest.raises(SourceCaptureError, match="binding"):
        manager.retire(spec)
    assert "drop" not in backend.events


@pytest.mark.parametrize("fault", ["capacity_fault", "source_fault"])
def test_preflight_failure_never_creates_or_attaches(case, fault):
    manager, backend, spec, _ = case
    setattr(backend, fault, True)
    with pytest.raises(SourceCaptureError):
        manager.acquire(spec)
    assert manager._load(spec) is None
    assert "ensure" not in backend.events and "attach" not in backend.events


def test_completion_journal_failure_retains_uncertain_attach(case, monkeypatch):
    manager, backend, spec, _ = case
    save = manager._save

    def fail_completion(spec, phase, observation=None):
        if phase == "captured":
            raise OSError("journal fsync failed")
        save(spec, phase, observation)

    monkeypatch.setattr(manager, "_save", fail_completion)
    with pytest.raises(OSError):
        manager.acquire(spec)
    with pytest.raises(SourceCaptureUncertain):
        manager.acquire(spec)
    assert backend.events.count("attach") == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("rows", True),
        ("parts", -1),
        ("bytes_on_disk", 2**64),
        ("server_uuid", "00000000-0000-0000-0000-000000000000"),
        ("parts_sha256", "not-a-digest"),
        ("table", "spans; DROP TABLE spans"),
    ],
)
def test_invalid_observations_are_rejected(case, field, value):
    manager, _, spec, _ = case
    observation = manager.acquire(spec)
    with pytest.raises(ValueError):
        SourceCaptureObservation(**(asdict(observation) | {field: value}))


def test_spec_derives_stable_isolated_namespace_and_unique_table():
    first = specification()
    second = specification(build_token=str(uuid4()))
    assert first.capture_database == second.capture_database != first.source_database
    assert first.capture_table != second.capture_table
    assert first.capture_table_uuid != second.capture_table_uuid
    assert first == specification()


def test_late_empty_create_is_cleaned_without_ever_reusing_the_build(case):
    manager, backend, spec, _ = case
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    empty = backend.observation
    manager.retire(spec)
    backend.observation = empty  # Original CREATE completes after an absent check.
    with pytest.raises(SourceCaptureRetired):
        manager.acquire(spec)
    manager.retire(spec)
    assert backend.observation is None
    assert backend.events.count("ensure") == 1
    assert backend.events.count("attach") == 0


def test_failed_dispatch_intent_never_sends_create_and_can_retire(case, monkeypatch):
    manager, backend, spec, _ = case
    original = manager._save

    def fail_creating(value, phase, observation=None):
        if phase == "creating":
            raise OSError("dispatch journal could not be persisted")
        return original(value, phase, observation)

    monkeypatch.setattr(manager, "_save", fail_creating)
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert manager._load(spec)["phase"] == "create_prepared"
    assert backend.observation is None
    manager.retire(spec)
    assert manager._load(spec)["phase"] == "retired"
    assert "attach" not in backend.events and "drop" not in backend.events


def test_lost_created_journal_ack_never_permits_attach(case, monkeypatch):
    manager, backend, spec, _ = case
    original = manager._save

    def fail_created(value, phase, observation=None):
        if phase == "created":
            raise OSError("CREATE completion journal could not be persisted")
        return original(value, phase, observation)

    monkeypatch.setattr(manager, "_save", fail_created)
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert backend.observation is not None
    with pytest.raises(SourceCaptureUncertain):
        manager.acquire(spec)
    manager.retire(spec)
    assert backend.observation is None
    assert backend.events.count("ensure") == 1
    assert "attach" not in backend.events


def test_uncertain_create_cleanup_does_not_require_live_source_to_survive(case):
    manager, backend, spec, _ = case
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    backend.source_fault = True
    manager.retire(spec)
    assert backend.observation is None
    assert manager._load(spec)["phase"] == "abandoned_create"


def test_nonempty_abandoned_create_cannot_be_garbage_collected(case):
    manager, backend, spec, _ = case
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    backend.observation = replace(
        backend.observation, parts=1, rows=1, bytes_on_disk=64
    )
    with pytest.raises(SourceCaptureError, match="unexpectedly contains parts"):
        manager.retire(spec)
    assert backend.observation.rows == 1
    assert "drop" not in backend.events
