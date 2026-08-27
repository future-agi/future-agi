from __future__ import annotations

import pytest

from slots.models import StateIdentity as RegistryStateIdentity
from slots.state import (
    StateValidationError,
    adapt_orchestrator_state,
    build_state_plan,
    project_name,
    validate_isolated_infra,
    volume_name,
)


def test_private_provider_has_deterministic_provider_bound_identities() -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres,redis")

    assert state.identity.postgres_database == "futureagi_slot_03"
    assert state.identity.clickhouse_database == "futureagi_slot_03"
    assert state.identity.temporal_namespace == "futureagi-slot-03"
    assert state.identity.rabbitmq_vhost == "futureagi-slot-03"
    assert state.identity.minio_bucket == "futureagi-slot-03"
    assert state.identity.redis_databases == (9, 10, 11)
    assert state.volume_for("postgres") == "futureagi_slot_03_postgres_data"
    assert state.project_for("postgres") == "futureagi-slot-03-postgres"
    assert state.volume_for("minio") is None


def test_slot_twenty_uses_the_last_reserved_redis_triplet() -> None:
    assert build_state_plan(20, provider_slot=20).identity.redis_databases == (
        60,
        61,
        62,
    )


def test_shared_default_reserves_a_non_overlapping_redis_triplet() -> None:
    state = build_state_plan(1)

    assert state.identity.postgres_database == "futureagi_slot_00"
    assert state.identity.redis_databases == (0, 1, 2)


def test_orchestration_identity_can_be_adapted_without_importing_registry_models() -> (
    None
):
    identity = adapt_orchestrator_state(RegistryStateIdentity.for_slot(3))

    assert identity.provider_slot == 3
    assert identity.redis_databases == (9, 10, 11)


def test_orchestration_identity_adapter_rejects_non_deterministic_record() -> None:
    class BadModelIdentity:
        slot = 3
        postgres_database = "postgres"
        clickhouse_database = "futureagi_slot_03"
        temporal_namespace = "futureagi-slot-03"
        rabbitmq_vhost = "futureagi-slot-03"
        minio_bucket = "futureagi-slot-03"
        redis_databases = (9, 10, 11)

    with pytest.raises(StateValidationError, match="does not match"):
        adapt_orchestrator_state(BadModelIdentity())


@pytest.mark.parametrize("slot", [0, 21, True, "3"])
def test_slots_are_strictly_limited_to_public_range(slot: object) -> None:
    with pytest.raises(StateValidationError):
        build_state_plan(slot)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value", ["postgres,unknown", "postgres,postgres", "postgres,", "all"]
)
def test_isolation_validation_rejects_ambiguous_or_unknown_values(value: str) -> None:
    with pytest.raises(StateValidationError):
        validate_isolated_infra(value)


def test_isolation_cannot_be_attached_to_shared_backend_state() -> None:
    with pytest.raises(StateValidationError, match="private backend"):
        build_state_plan(3, isolate_infra="postgres")


def test_deterministic_physical_names_validate_every_component() -> None:
    assert volume_name(20, "temporal") == "futureagi_slot_20_temporal_data"
    assert project_name(20, "temporal") == "futureagi-slot-20-temporal"
    with pytest.raises(StateValidationError):
        volume_name(1, "postgres; rm -rf /")
