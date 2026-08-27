"""Typed, versioned data exchanged by the slot orchestration boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .catalog import CATALOG, ISOLATABLE_INFRA

STATE_COUPLED_GROUPS = frozenset(("simulation", "collector", "peerdb"))

REGISTRY_VERSION = 1


@dataclass(frozen=True)
class ResourceEstimate:
    memory_mib: int
    services: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"memory_mib": self.memory_mib, "services": list(self.services)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResourceEstimate:
        return cls(int(value["memory_mib"]), tuple(value["services"]))


@dataclass(frozen=True)
class StateIdentity:
    """A serialisable snapshot of the backend provider's state identity."""

    slot: int
    postgres_database: str
    clickhouse_database: str
    temporal_namespace: str
    rabbitmq_vhost: str
    minio_bucket: str
    redis_databases: tuple[int, int, int]

    @classmethod
    def for_slot(cls, slot: int) -> StateIdentity:
        if not 0 <= slot <= 20:
            raise ValueError("state slot must be an integer from 0 through 20")
        padded = f"{slot:02d}"
        return cls(
            slot=slot,
            postgres_database=f"futureagi_slot_{padded}",
            clickhouse_database=f"futureagi_slot_{padded}",
            temporal_namespace=f"futureagi-slot-{padded}",
            rabbitmq_vhost=f"futureagi-slot-{padded}",
            minio_bucket=f"futureagi-slot-{padded}",
            redis_databases=(slot * 3, slot * 3 + 1, slot * 3 + 2),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "redis_databases": list(self.redis_databases)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StateIdentity:
        return cls(
            slot=int(value["slot"]),
            postgres_database=str(value["postgres_database"]),
            clickhouse_database=str(value["clickhouse_database"]),
            temporal_namespace=str(value["temporal_namespace"]),
            rabbitmq_vhost=str(value["rabbitmq_vhost"]),
            minio_bucket=str(value["minio_bucket"]),
            redis_databases=tuple(int(item) for item in value["redis_databases"]),
        )


@dataclass(frozen=True)
class ProviderRecord:
    name: str
    group: str
    worktree: str
    state: StateIdentity
    references: tuple[int, ...] = ()
    private: bool = False
    base_ref: str = "origin/dev"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "state": self.state.to_dict(),
            "references": list(self.references),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProviderRecord:
        return cls(
            name=str(value["name"]),
            group=str(value["group"]),
            worktree=str(value["worktree"]),
            state=StateIdentity.from_dict(value["state"]),
            references=tuple(int(item) for item in value.get("references", [])),
            private=bool(value.get("private", False)),
            base_ref=str(value.get("base_ref", "origin/dev")),
        )


@dataclass(frozen=True)
class SlotRecord:
    slot: int
    worktree: str
    requested_services: tuple[str, ...]
    services: tuple[str, ...]
    providers: dict[str, str]
    state: StateIdentity
    routes: dict[str, str]
    ports: dict[str, int]
    resources: ResourceEstimate
    environment_fingerprint: str = ""
    revision: str = ""
    isolated_infra: tuple[str, ...] = ()
    http_port: str = "80"
    retired_isolated_infra: tuple[str, ...] = ()
    retained_private_backend_state: bool = False
    shared_base_ref: str = "origin/dev"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "requested_services": list(self.requested_services),
            "services": list(self.services),
            "state": self.state.to_dict(),
            "resources": self.resources.to_dict(),
            "isolated_infra": list(self.isolated_infra),
            "retired_isolated_infra": list(self.retired_isolated_infra),
            "retained_private_backend_state": self.retained_private_backend_state,
            "shared_base_ref": self.shared_base_ref,
            "http_port": self.http_port,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SlotRecord:
        return cls(
            slot=int(value["slot"]),
            worktree=str(value["worktree"]),
            requested_services=tuple(value.get("requested_services", [])),
            services=tuple(value["services"]),
            providers={
                str(key): str(item) for key, item in value.get("providers", {}).items()
            },
            state=StateIdentity.from_dict(value["state"]),
            routes={str(key): str(item) for key, item in value["routes"].items()},
            ports={str(key): int(item) for key, item in value["ports"].items()},
            resources=ResourceEstimate.from_dict(value["resources"]),
            environment_fingerprint=str(value.get("environment_fingerprint", "")),
            revision=str(value.get("revision", "")),
            isolated_infra=tuple(value.get("isolated_infra", [])),
            http_port=str(value.get("http_port", "80")),
            retired_isolated_infra=tuple(value.get("retired_isolated_infra", [])),
            retained_private_backend_state=bool(
                value.get("retained_private_backend_state", False)
            ),
            shared_base_ref=str(value.get("shared_base_ref", "origin/dev")),
        )


@dataclass(frozen=True)
class Registry:
    version: int = REGISTRY_VERSION
    slots: dict[str, SlotRecord] = field(default_factory=dict)
    providers: dict[str, ProviderRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "version": self.version,
            "slots": {key: value.to_dict() for key, value in self.slots.items()},
            "providers": {
                key: value.to_dict() for key, value in self.providers.items()
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Registry:
        if value.get("version") != REGISTRY_VERSION:
            raise ValueError(
                f"unsupported slots registry version: {value.get('version')!r}"
            )
        registry = cls(
            version=REGISTRY_VERSION,
            slots={
                str(key): SlotRecord.from_dict(item)
                for key, item in value.get("slots", {}).items()
            },
            providers={
                str(key): ProviderRecord.from_dict(item)
                for key, item in value.get("providers", {}).items()
            },
        )
        registry.validate()
        return registry

    def validate(self) -> None:
        """Reject corrupted registry relationships before they reach lifecycle code."""
        for key, record in self.slots.items():
            if key != str(record.slot) or not 1 <= record.slot <= 20:
                raise ValueError(f"invalid slot registry key: {key!r}")
            if "frontend" not in record.providers or "backend" not in record.providers:
                raise ValueError(f"slot {record.slot} is missing mandatory providers")
            if not record.worktree:
                raise ValueError(f"slot {record.slot} has no worktree")
            if set(record.services) - set(CATALOG):
                raise ValueError(f"slot {record.slot} has unsupported services")
            if set(record.providers) - {"frontend", *CATALOG}:
                raise ValueError(f"slot {record.slot} has unsupported providers")
            if set(record.isolated_infra) - ISOLATABLE_INFRA:
                raise ValueError(
                    f"slot {record.slot} has unsupported isolated infrastructure"
                )
            if len(set(record.isolated_infra)) != len(record.isolated_infra):
                raise ValueError(f"slot {record.slot} repeats isolated infrastructure")
            if set(record.retired_isolated_infra) & set(record.isolated_infra):
                raise ValueError(
                    f"slot {record.slot} retains active isolated infrastructure"
                )
            if set(record.retired_isolated_infra) - ISOLATABLE_INFRA:
                raise ValueError(
                    f"slot {record.slot} has invalid retired infrastructure"
                )
            try:
                http_port = int(record.http_port)
            except ValueError as error:
                raise ValueError(f"slot {record.slot} has invalid HTTP port") from error
            if not 1 <= http_port <= 65535:
                raise ValueError(f"slot {record.slot} has invalid HTTP port")
            band_start = 20000 + record.slot * 100
            if any(
                port < band_start or port >= band_start + 100
                for port in record.ports.values()
            ):
                raise ValueError(f"slot {record.slot} has a port outside slot bands")
            if record.state != StateIdentity.for_slot(record.state.slot):
                raise ValueError(f"slot {record.slot} has an invalid state identity")
            if not record.shared_base_ref:
                raise ValueError(f"slot {record.slot} has an empty shared base ref")
        expected_references: dict[str, set[int]] = {}
        for record in self.slots.values():
            for group, name in record.providers.items():
                expected_references.setdefault(name, set()).add(record.slot)
                provider = self.providers.get(name)
                if provider is None:
                    raise ValueError(
                        f"slot {record.slot} references missing provider {name!r}"
                    )
                if provider.group != group:
                    raise ValueError(f"provider {name!r} is mapped to the wrong group")
                expected_name = (
                    f"slot-{record.slot:02d}-{group}"
                    if provider.private
                    else f"shared-{group}"
                )
                if name != expected_name:
                    raise ValueError(f"provider {name!r} has an invalid ownership name")
            backend = self.providers[record.providers["backend"]]
            if backend.state != record.state:
                raise ValueError(
                    f"slot {record.slot} backend state does not match provider"
                )
            for group in STATE_COUPLED_GROUPS:
                provider = self.providers[record.providers[group]]
                if provider.state != backend.state:
                    raise ValueError(
                        f"slot {record.slot} state-coupled {group} does not match backend"
                    )
                if backend.private and not provider.private:
                    raise ValueError(
                        f"slot {record.slot} private backend requires private {group}"
                    )
        for name, provider in self.providers.items():
            if provider.name != name or not provider.worktree:
                raise ValueError(f"provider {name!r} has invalid identity metadata")
            if not provider.base_ref:
                raise ValueError(f"provider {name!r} has an empty base ref")
            if provider.group not in {"frontend", *CATALOG}:
                raise ValueError(f"provider {name!r} has unsupported group")
            if provider.state != StateIdentity.for_slot(provider.state.slot):
                raise ValueError(f"provider {name!r} has invalid state identity")
            if not provider.private and provider.state != StateIdentity.for_slot(0):
                raise ValueError(f"shared provider {name!r} must use shared state")
            if tuple(sorted(set(provider.references))) != provider.references:
                raise ValueError(f"provider {name!r} has non-canonical references")
            # A zero-reference shared provider is loadable solely for recovery
            # cleanup by `slots-prune`; normal down removes it atomically.
            if provider.references and set(
                provider.references
            ) != expected_references.get(name, set()):
                raise ValueError(f"provider {name!r} has invalid references")
            if not provider.references and name in expected_references:
                raise ValueError(f"provider {name!r} lost active references")
            if not provider.references and provider.private:
                raise ValueError(f"private provider {name!r} cannot be orphaned")
