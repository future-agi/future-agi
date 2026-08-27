"""Validated provider-bound state identities for development slots.

All functions in this module are deterministic and have no I/O.  A provider is
either the shared default (``None``) or the private backend belonging to a slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable, Protocol

MIN_SLOT: Final = 1
MAX_SLOT: Final = 20
REDIS_DATABASES_PER_PROVIDER: Final = 3
REDIS_DATABASE_COUNT: Final = 64
INFRA_ENGINES: Final = frozenset(
    {"postgres", "clickhouse", "redis", "rabbitmq", "minio", "temporal"}
)


class StateValidationError(ValueError):
    """Raised before a state plan can name an infrastructure resource."""


def validate_slot(slot: int) -> int:
    """Return ``slot`` after enforcing the public 1--20 slot contract."""
    if (
        isinstance(slot, bool)
        or not isinstance(slot, int)
        or not MIN_SLOT <= slot <= MAX_SLOT
    ):
        raise StateValidationError(
            f"SLOT must be an integer from {MIN_SLOT} to {MAX_SLOT}"
        )
    return slot


def _provider_suffix(provider_slot: int | None) -> str:
    return f"slot_{0 if provider_slot is None else provider_slot:02d}"


def _provider_dash_suffix(provider_slot: int | None) -> str:
    return f"slot-{0 if provider_slot is None else provider_slot:02d}"


def validate_isolated_infra(value: str | Iterable[str] | None) -> frozenset[str]:
    """Parse ``ISOLATE_INFRA`` without accepting aliases or unknown engines."""
    if value is None:
        return frozenset()
    items = value.split(",") if isinstance(value, str) else list(value)
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise StateValidationError("ISOLATE_INFRA entries must be strings")
        engine = item.strip()
        if not engine:
            raise StateValidationError("ISOLATE_INFRA cannot contain an empty engine")
        if engine not in INFRA_ENGINES:
            supported = ", ".join(sorted(INFRA_ENGINES))
            raise StateValidationError(
                f"unsupported ISOLATE_INFRA engine {engine!r}; supported: {supported}"
            )
        if engine in normalized:
            raise StateValidationError(f"ISOLATE_INFRA lists {engine!r} more than once")
        normalized.append(engine)
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class ProvisioningStateIdentity:
    """Logical state owned by one selected backend provider."""

    provider_slot: int | None

    def __post_init__(self) -> None:
        if self.provider_slot is not None:
            validate_slot(self.provider_slot)
        if max(self.redis_databases, default=-1) >= REDIS_DATABASE_COUNT:
            raise StateValidationError(
                "Redis database allocation exceeds configured database count"
            )

    @property
    def is_shared_default(self) -> bool:
        return self.provider_slot is None

    @property
    def postgres_database(self) -> str:
        return f"futureagi_{_provider_suffix(self.provider_slot)}"

    @property
    def clickhouse_database(self) -> str:
        return f"futureagi_{_provider_suffix(self.provider_slot)}"

    @property
    def temporal_namespace(self) -> str:
        return f"futureagi-{_provider_dash_suffix(self.provider_slot)}"

    @property
    def rabbitmq_vhost(self) -> str:
        return f"futureagi-{_provider_dash_suffix(self.provider_slot)}"

    @property
    def minio_bucket(self) -> str:
        return f"futureagi-{_provider_dash_suffix(self.provider_slot)}"

    @property
    def redis_databases(self) -> tuple[int, int, int]:
        # The shared default owns 0--2; private slots 1--20 own 3--62.
        start = (
            0 if self.provider_slot is None else self.provider_slot
        ) * REDIS_DATABASES_PER_PROVIDER
        return (start, start + 1, start + 2)

    @property
    def label(self) -> str:
        return (
            "default"
            if self.provider_slot is None
            else f"slot-{self.provider_slot:02d}"
        )


class OrchestratorStateIdentity(Protocol):
    """The subset of ``slots.models.StateIdentity`` provisioning requires."""

    slot: int
    postgres_database: str
    clickhouse_database: str
    temporal_namespace: str
    rabbitmq_vhost: str
    minio_bucket: str
    redis_databases: tuple[int, int, int]


def adapt_orchestrator_state(
    identity: OrchestratorStateIdentity,
) -> ProvisioningStateIdentity:
    """Adapt and validate the orchestration state model without importing it."""
    provider_slot = None if identity.slot == 0 else identity.slot
    result = ProvisioningStateIdentity(provider_slot)
    expected = (
        result.postgres_database,
        result.clickhouse_database,
        result.temporal_namespace,
        result.rabbitmq_vhost,
        result.minio_bucket,
        result.redis_databases,
    )
    actual = (
        identity.postgres_database,
        identity.clickhouse_database,
        identity.temporal_namespace,
        identity.rabbitmq_vhost,
        identity.minio_bucket,
        identity.redis_databases,
    )
    if actual != expected:
        raise StateValidationError(
            "orchestrator state identity does not match deterministic provisioning names"
        )
    return result


def volume_name(provider_slot: int, engine: str) -> str:
    """Return an isolated engine's one and only persistent Docker volume name."""
    validate_slot(provider_slot)
    if engine not in INFRA_ENGINES:
        raise StateValidationError(f"unsupported infrastructure engine: {engine!r}")
    return f"futureagi_slot_{provider_slot:02d}_{engine}_data"


def project_name(provider_slot: int, engine: str) -> str:
    """Return an isolated engine's dedicated Compose project name."""
    validate_slot(provider_slot)
    if engine not in INFRA_ENGINES:
        raise StateValidationError(f"unsupported infrastructure engine: {engine!r}")
    return f"futureagi-slot-{provider_slot:02d}-{engine}"


@dataclass(frozen=True, slots=True)
class StatePlan:
    """The complete state topology selected for one slot before any mutation."""

    slot: int
    identity: ProvisioningStateIdentity
    isolated_infra: frozenset[str]

    def __post_init__(self) -> None:
        validate_slot(self.slot)
        if self.isolated_infra and self.identity.provider_slot is None:
            raise StateValidationError(
                "ISOLATE_INFRA requires a private backend provider"
            )

    def is_isolated(self, engine: str) -> bool:
        if engine not in INFRA_ENGINES:
            raise StateValidationError(f"unsupported infrastructure engine: {engine!r}")
        return engine in self.isolated_infra

    def volume_for(self, engine: str) -> str | None:
        if not self.is_isolated(engine):
            return None
        assert self.identity.provider_slot is not None
        return volume_name(self.identity.provider_slot, engine)

    def project_for(self, engine: str) -> str | None:
        if not self.is_isolated(engine):
            return None
        assert self.identity.provider_slot is not None
        return project_name(self.identity.provider_slot, engine)


def build_state_plan(
    slot: int,
    *,
    provider_slot: int | None = None,
    isolate_infra: str | Iterable[str] | None = None,
) -> StatePlan:
    """Build a state plan bound to the chosen backend provider.

    ``provider_slot=None`` represents the shared default provider.  It cannot
    receive a slot-private physical engine because a shared backend could not
    safely use that isolated dependency.
    """
    validate_slot(slot)
    if provider_slot is not None:
        validate_slot(provider_slot)
    return StatePlan(
        slot=slot,
        identity=ProvisioningStateIdentity(provider_slot),
        isolated_infra=validate_isolated_infra(isolate_infra),
    )
