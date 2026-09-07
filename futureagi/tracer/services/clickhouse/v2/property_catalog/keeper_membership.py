"""SELECT-only proof that admitted catalog nodes share live Keeper membership.

ClickHouse 25.3 writes its persistent server UUID to each replica's ephemeral
is_active node. Cross-reading those UUIDs AND the owning live sessions binds
membership to the actual servers; identical path/config strings are not proof.
This does not prove INSERT completion or permit replay of an uncertain write.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID

from .publisher import PROPERTY_CATALOG_TABLES as _DATA_TABLES

PROPERTY_CATALOG_TABLES = _DATA_TABLES | {"property_catalog_activation_control_events"}

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,253}\Z", re.ASCII)
_MAX_ROWS = 1024
_CONNECTION = """SELECT hostName() AS hostname, toString(serverUUID()) AS server_uuid,
name, client_id, is_expired
FROM system.zookeeper_connection WHERE name = 'default' LIMIT 2"""
_MEMBERSHIP = """SELECT path, value, ephemeralOwner AS owner
FROM system.zookeeper WHERE path IN %(paths)s AND name = 'is_active'
ORDER BY path LIMIT %(row_limit)s"""


class KeeperMembershipError(ValueError):
    """Shared current membership was not completely and consistently observed."""


@dataclass(frozen=True, slots=True)
class KeeperMember:
    name: str
    hostname: str
    server_uuid: str
    table_paths: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not _NAME.fullmatch(self.hostname):
            raise KeeperMembershipError("invalid direct Keeper member name")
        try:
            parsed = UUID(self.server_uuid)
        except (ValueError, TypeError, AttributeError) as exc:
            raise KeeperMembershipError("invalid member server UUID") from exc
        if not parsed.int or str(parsed) != self.server_uuid:
            raise KeeperMembershipError("member server UUID must be canonical nonzero")
        if (
            not isinstance(self.table_paths, tuple)
            or tuple(sorted(self.table_paths)) != self.table_paths
            or tuple(name for name, _ in self.table_paths)
            != tuple(sorted(PROPERTY_CATALOG_TABLES))
        ):
            raise KeeperMembershipError(
                "Keeper proof requires all seven catalog tables"
            )
        for _, path in self.table_paths:
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or path.endswith("/")
                or "//" in path
                or any(char in path for char in "\x00\r\n'{}")
                or len(path.encode()) > 1024
            ):
                raise KeeperMembershipError("invalid exact catalog Keeper path")


# The caller resolves each name to its already directly attested node; no
# service/round-robin query may implement this boundary. Reads must consume
# complete bounded results, with no automatic endpoint switch or SQL retry.
KeeperRead = Callable[
    [str, str, Mapping[str, Any], int, int], Sequence[Mapping[str, Any]]
]


def attest_keeper_membership(
    members: Sequence[KeeperMember],
    read: KeeperRead,
    *,
    timeout_ms: int = 30_000,
    clock: Callable[[], float] = monotonic,
) -> str:
    """Return a stable membership digest only after cross-node session proof.

    The digest excludes ephemeral session IDs so routine restarts do not require
    editing admission. Every call still verifies the new live sessions. Current
    catalog schemas use the default Keeper; auxiliary-engine schemas are not
    silently admitted through that connection.
    """
    members = tuple(members)
    if (
        len(members) < 2
        or len(members) * len(PROPERTY_CATALOG_TABLES) >= _MAX_ROWS
        or any(not isinstance(m, KeeperMember) for m in members)
        or tuple(sorted(members, key=lambda m: m.name)) != members
        or any(
            len({getattr(m, field) for m in members}) != len(members)
            for field in ("name", "hostname", "server_uuid")
        )
        or any(m.table_paths != members[0].table_paths for m in members)
        or type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 30_000
    ):
        raise KeeperMembershipError("incomplete or inconsistent serving membership")
    deadline = clock() + timeout_ms / 1000

    def query(member: KeeperMember, sql: str, params: dict, limit: int):
        remaining = int((deadline - clock()) * 1000)
        if remaining <= 0:
            raise KeeperMembershipError("Keeper membership attestation timed out")
        try:
            rows = tuple(read(member.name, sql, params, limit, remaining))
            if clock() >= deadline or len(rows) > limit:
                raise KeeperMembershipError("incomplete bounded Keeper observation")
            if any(not isinstance(row, Mapping) for row in rows):
                raise KeeperMembershipError("invalid Keeper observation")
            return rows
        except KeeperMembershipError:
            raise
        except Exception as exc:
            raise KeeperMembershipError(
                "Keeper membership could not be observed"
            ) from exc

    def session(member: KeeperMember) -> int:
        rows = query(member, _CONNECTION, {}, 2)
        if len(rows) != 1:
            raise KeeperMembershipError("default Keeper connection absent or ambiguous")
        row = rows[0]
        if (
            set(row) != {"hostname", "server_uuid", "name", "client_id", "is_expired"}
            or (row["hostname"], row["server_uuid"], row["name"])
            != (member.hostname, member.server_uuid, "default")
            or type(row["client_id"]) is not int
            or not 0 < row["client_id"] < 1 << 63
            or type(row["is_expired"]) is not int
            or row["is_expired"] != 0
        ):
            raise KeeperMembershipError("Keeper node identity/session not current")
        return row["client_id"]

    before = {m.name: session(m) for m in members}
    if len(set(before.values())) != len(members):
        raise KeeperMembershipError("distinct admitted nodes share a Keeper session")
    expected = {
        f"{path}/replicas/{m.name}": (f"UUID_'{m.server_uuid}'", before[m.name])
        for m in members
        for _, path in m.table_paths
    }
    if len(expected) != len(members) * len(PROPERTY_CATALOG_TABLES):
        raise KeeperMembershipError("catalog tables alias a Keeper path")
    paths = tuple(sorted(expected))
    for member in members:
        rows = query(
            member,
            _MEMBERSHIP,
            {"paths": paths, "row_limit": len(paths) + 1},
            len(paths) + 1,
        )
        observed = {}
        for row in rows:
            if (
                set(row) != {"path", "value", "owner"}
                or not isinstance(row["path"], str)
                or row["path"] in observed
                or not isinstance(row["value"], str)
                or type(row["owner"]) is not int
            ):
                raise KeeperMembershipError("ambiguous or invalid active replica node")
            observed[row["path"]] = (row["value"], row["owner"])
        if observed != expected:
            raise KeeperMembershipError(
                "Keeper does not own the actual complete serving membership"
            )
    after = {m.name: session(m) for m in members}
    if before != after:
        raise KeeperMembershipError("Keeper session changed during attestation")
    document = {
        "format": "futureagi.catalog-keeper-live-membership",
        "version": 1,
        "members": [{"name": m.name, "server_uuid": m.server_uuid} for m in members],
        "tables": [
            {"name": name, "keeper_path": path} for name, path in members[0].table_paths
        ],
    }
    # Match encoding/json.Marshal, including non-ASCII paths and its HTML /
    # JavaScript separator escapes. ASCII-only fixtures must not conceal a
    # cross-language identity mismatch for a valid infrastructure path.
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    for character, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        canonical = canonical.replace(character, escaped)
    return hashlib.sha256(canonical.encode()).hexdigest()
