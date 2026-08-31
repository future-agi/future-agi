"""The fixed provider catalog and selection validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    dependencies: tuple[str, ...] = ()
    memory_mib: int = 0
    port_offset: int = 0


# `frontend` is intentionally absent: it is always slot-private and cannot be selected.
CATALOG: dict[str, ServiceDefinition] = {
    "backend": ServiceDefinition("backend", memory_mib=1300, port_offset=11),
    "simulation": ServiceDefinition("simulation", ("backend",), 700, 20),
    "gateway": ServiceDefinition("gateway", ("backend",), 350, 30),
    "collector": ServiceDefinition("collector", ("backend",), 350, 40),
    "serving": ServiceDefinition("serving", ("backend",), 600, 50),
    "executor": ServiceDefinition("executor", ("backend",), 600, 60),
    "peerdb": ServiceDefinition("peerdb", ("backend",), 700, 70),
    "observability": ServiceDefinition("observability", (), 500, 80),
}
SUPPORTED_SERVICES = frozenset(("none", "all", *CATALOG))
FRONTEND_MEMORY_MIB = 350
FRONTEND_PORT_OFFSET = 10
ISOLATABLE_INFRA = frozenset(
    {"postgres", "clickhouse", "redis", "rabbitmq", "minio", "temporal"}
)
ISOLATED_PORT_OFFSETS = {
    "postgres": 1,
    "clickhouse_http": 2,
    "clickhouse_native": 3,
    "redis": 4,
    "rabbitmq": 5,
    "rabbitmq_management": 6,
    "minio_api": 7,
    "minio_console": 8,
    "temporal": 9,
}


def parse_services(value: str | None) -> tuple[str, ...]:
    """Parse the public SERVICES value without silently accepting typos."""
    tokens = tuple(
        token.strip().lower() for token in (value or "none").split(",") if token.strip()
    )
    if not tokens:
        return ("none",)
    unknown = set(tokens) - SUPPORTED_SERVICES
    if unknown:
        raise ValueError(f"unsupported SERVICES value(s): {', '.join(sorted(unknown))}")
    if "none" in tokens and len(tokens) > 1:
        raise ValueError("SERVICES=none cannot be combined with other services")
    if "all" in tokens and len(tokens) > 1:
        raise ValueError("SERVICES=all cannot be combined with other services")
    if len(set(tokens)) != len(tokens):
        raise ValueError("SERVICES cannot list the same service more than once")
    return tuple(dict.fromkeys(tokens))


def expand_services(selection: tuple[str, ...] | str | None) -> tuple[str, ...]:
    requested = (
        parse_services(selection)
        if isinstance(selection, str) or selection is None
        else selection
    )
    roots = (
        tuple(CATALOG)
        if requested == ("all",)
        else (() if requested == ("none",) else requested)
    )
    expanded: set[str] = set()

    def include(name: str) -> None:
        if name in expanded:
            return
        expanded.add(name)
        for dependency in CATALOG[name].dependencies:
            include(dependency)

    for service in roots:
        include(service)
    return tuple(name for name in CATALOG if name in expanded)


def parse_isolated_infra(value: str | None) -> tuple[str, ...]:
    tokens = tuple(
        token.strip().lower() for token in (value or "").split(",") if token.strip()
    )
    unknown = set(tokens) - ISOLATABLE_INFRA
    if unknown:
        raise ValueError(
            f"unsupported ISOLATE_INFRA value(s): {', '.join(sorted(unknown))}"
        )
    return tuple(dict.fromkeys(tokens))


def private_ports(
    slot: int, services: tuple[str, ...], isolated_infra: tuple[str, ...] = ()
) -> dict[str, int]:
    if not 1 <= slot <= 20:
        raise ValueError("SLOT must be an integer from 1 through 20")
    band = 20000 + slot * 100
    ports = {"frontend": band + FRONTEND_PORT_OFFSET}
    ports.update({service: band + CATALOG[service].port_offset for service in services})
    for engine in isolated_infra:
        if engine == "clickhouse":
            ports["clickhouse_http"] = band + ISOLATED_PORT_OFFSETS["clickhouse_http"]
            ports["clickhouse_native"] = (
                band + ISOLATED_PORT_OFFSETS["clickhouse_native"]
            )
        elif engine == "rabbitmq":
            ports["rabbitmq"] = band + ISOLATED_PORT_OFFSETS["rabbitmq"]
            ports["rabbitmq_management"] = (
                band + ISOLATED_PORT_OFFSETS["rabbitmq_management"]
            )
        elif engine == "minio":
            ports["minio_api"] = band + ISOLATED_PORT_OFFSETS["minio_api"]
            ports["minio_console"] = band + ISOLATED_PORT_OFFSETS["minio_console"]
        else:
            ports[engine] = band + ISOLATED_PORT_OFFSETS[engine]
    return ports


def estimate_resources(services: tuple[str, ...]) -> int:
    return FRONTEND_MEMORY_MIB + sum(
        CATALOG[service].memory_mib for service in services
    )
