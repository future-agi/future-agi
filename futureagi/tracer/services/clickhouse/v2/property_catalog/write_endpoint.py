"""Discover a candidate HTTP route from an already selected native member.

This is SELECT-only discovery, not write admission. The admission caller must
still directly attest the HTTP route's same server/database/table identity and
the full serving topology before publishing a WriteAdmission descriptor. A
service response cannot turn a load balancer into a directly admitted member.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII)
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z", re.ASCII)
_OBSERVE_SQL = """SELECT
    hostName() AS hostname,
    currentDatabase() AS connected_database,
    toString(uuid) AS database_uuid,
    getServerPort(%(port_name)s) AS http_port
FROM system.databases
WHERE name = %(database)s
LIMIT 2
"""


class CatalogEndpointError(ValueError):
    """No complete, destination-bound native route observation was returned."""


@dataclass(frozen=True, slots=True)
class CatalogHTTPRouteCandidate:
    origin: str
    hostname: str
    database: str
    database_uuid: str

    def require_same_http_identity(
        self, *, hostname: str, database: str, database_uuid: str
    ) -> None:
        if (hostname, database, database_uuid) != (
            self.hostname,
            self.database,
            self.database_uuid,
        ):
            raise CatalogEndpointError("HTTP and native catalog identities differ")


def _uri_host(host: str) -> str:
    if not isinstance(host, str) or not host or len(host) > 253:
        raise CatalogEndpointError("native member must have a concrete host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        if not _HOST.fullmatch(host) or any(not part for part in host.split(".")):
            raise CatalogEndpointError(
                "native member host is not a hostname or IP"
            ) from exc
        return host
    if getattr(address, "scope_id", None) is not None:
        raise CatalogEndpointError(
            "scoped IPv6 requires infrastructure route resolution"
        )
    return f"[{address}]" if address.version == 6 else str(address)


def discover_catalog_http_route(
    driver: Any,
    *,
    native_member_host: str,
    expected_hostname: str,
    database: str,
    scheme: str,
    timeout_ms: int,
) -> CatalogHTTPRouteCandidate:
    """Use the configured protocol's actual listener, never a default port.

    ``native_member_host`` comes from the selected infrastructure/native-cluster
    member, not from one response received through a service. Proxies/NAT may
    need an infrastructure-owned route resolver instead; this helper does not
    guess a public mapping, follow redirects or downgrade HTTPS to HTTP.
    """
    host = _uri_host(native_member_host)
    if scheme not in {"http", "https"}:
        raise CatalogEndpointError("catalog HTTP protocol must be http or https")
    if not isinstance(database, str) or not _IDENTIFIER.fullmatch(database):
        raise CatalogEndpointError("catalog database must be an exact identifier")
    if not isinstance(expected_hostname, str) or not expected_hostname:
        raise CatalogEndpointError("native observation needs an expected hostname")
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30_000:
        raise CatalogEndpointError("native route query requires a bounded timeout")
    if getattr(driver, "database", None) != database:
        raise CatalogEndpointError("native client is connected to another database")
    try:
        rows, columns, _ = driver.execute_read(
            _OBSERVE_SQL,
            {"port_name": f"{scheme}_port", "database": database},
            timeout_ms=timeout_ms,
            settings={
                "readonly": 2,
                "max_threads": 1,
                "max_result_rows": 2,
                "max_result_bytes": 4096,
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
            },
        )
        names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
        if names != ("hostname", "connected_database", "database_uuid", "http_port"):
            raise CatalogEndpointError("native route metadata columns changed")
        if len(rows) != 1 or len(rows[0]) != 4:
            raise CatalogEndpointError("native route metadata is absent or ambiguous")
        observed_hostname, connected_database, database_uuid, port = rows[0]
        parsed_uuid = UUID(database_uuid)
        if not parsed_uuid.int or str(parsed_uuid) != database_uuid:
            raise CatalogEndpointError("catalog database incarnation is not canonical")
        if (observed_hostname, connected_database) != (expected_hostname, database):
            raise CatalogEndpointError("native catalog server identity changed")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise CatalogEndpointError("native HTTP listener port is invalid")
    except CatalogEndpointError:
        raise
    except Exception as exc:
        raise CatalogEndpointError(
            "native catalog HTTP route could not be proven"
        ) from exc
    return CatalogHTTPRouteCandidate(
        origin=f"{scheme}://{host}:{port}",
        hostname=observed_hostname,
        database=database,
        database_uuid=database_uuid,
    )
