"""No replicated admission from matching names/paths or copied static metadata."""

from copy import deepcopy
from dataclasses import replace

import pytest

from tracer.services.clickhouse.v2.property_catalog.keeper_membership import (
    PROPERTY_CATALOG_TABLES,
    KeeperMember,
    KeeperMembershipError,
    attest_keeper_membership,
)


def members():
    paths = tuple(
        (name, "/catalog/1/" + name) for name in sorted(PROPERTY_CATALOG_TABLES)
    )
    return tuple(
        KeeperMember(
            f"replica{i}", f"host{i}", f"00000000-0000-4000-8000-{i:012d}", paths
        )
        for i in (1, 2)
    )


class Probe:
    def __init__(self, nodes=None, *, sessions=(101, 202), alter=None):
        self.nodes = nodes or members()
        self.sessions = dict(zip((m.name for m in self.nodes), sessions, strict=True))
        self.calls = []
        self.alter = alter

    def __call__(self, name, sql, params, limit, timeout):
        assert sql.startswith("SELECT ") and 0 < timeout <= 30_000
        self.calls.append((name, sql, params, limit))
        node = next(m for m in self.nodes if m.name == name)
        if "system.zookeeper_connection" in sql:
            rows = [
                {
                    "hostname": node.hostname,
                    "server_uuid": node.server_uuid,
                    "name": "default",
                    "client_id": self.sessions[name],
                    "is_expired": 0,
                }
            ]
        else:
            assert params["row_limit"] == len(params["paths"]) + 1 == limit
            rows = [
                {
                    "path": f"{path}/replicas/{m.name}",
                    "value": f"UUID_'{m.server_uuid}'",
                    "owner": self.sessions[m.name],
                }
                for m in self.nodes
                for _, path in m.table_paths
            ]
        return (
            self.alter(name, sql, deepcopy(rows), len(self.calls))
            if self.alter
            else rows
        )


def test_full_cross_node_live_membership_and_normal_restart_have_same_identity():
    first, restarted = Probe(), Probe(sessions=(303, 404))
    assert attest_keeper_membership(members(), first) == attest_keeper_membership(
        members(), restarted
    )
    assert len(first.calls) == 6
    assert [name for name, sql, _, _ in first.calls if "path IN" in sql] == [
        "replica1",
        "replica2",
    ]


def test_membership_digest_matches_go_json_marshal_for_non_ascii_paths():
    nodes = tuple(
        replace(
            member,
            table_paths=tuple(
                (name, path.replace("/catalog/", "/café<>&\u2028\u2029/"))
                for name, path in member.table_paths
            ),
        )
        for member in members()
    )
    # Golden produced with Go encoding/json.Marshal + crypto/sha256, not Python.
    assert attest_keeper_membership(nodes, Probe(nodes)) == (
        "697027b9425de9b699c39fa71dbcd2ba18112eb4fd95f543a48c47281ed7bf6e"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "absent",
        "duplicate",
        "foreign_uuid",
        "foreign_owner",
        "persistent",
        "extra",
        "bad_owner",
    ],
)
def test_independent_or_stale_keeper_with_identical_paths_fails(bad):
    def alter(name, sql, rows, _):
        if name != "replica2" or "path IN" not in sql:
            return rows
        if bad == "absent":
            return rows[:-1]
        if bad == "duplicate":
            return rows + [rows[0]]
        if bad == "foreign_uuid":
            rows[0]["value"] = "UUID_'00000000-0000-4000-8000-000000000999'"
        elif bad == "foreign_owner":
            rows[0]["owner"] += 1
        elif bad == "persistent":
            rows[0]["owner"] = 0
        elif bad == "extra":
            rows[0]["extra"] = 1
        elif bad == "bad_owner":
            rows[0]["owner"] = True
        return rows

    with pytest.raises(KeeperMembershipError):
        attest_keeper_membership(members(), Probe(alter=alter))


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", 0),
        ("client_id", True),
        ("client_id", "101"),
        ("is_expired", 1),
        ("is_expired", False),
        ("hostname", "other"),
        ("server_uuid", "00000000-0000-4000-8000-000000000999"),
        ("name", "auxiliary"),
    ],
)
def test_each_direct_connection_must_own_its_reported_active_session(field, value):
    def alter(_name, sql, rows, _):
        if "system.zookeeper_connection" in sql:
            rows[0][field] = value
        return rows

    with pytest.raises(KeeperMembershipError):
        attest_keeper_membership(members(), Probe(alter=alter))


def test_reconnect_during_cross_read_does_not_leave_a_stale_success():
    def alter(_name, sql, rows, call):
        if call >= 5 and "system.zookeeper_connection" in sql:
            rows[0]["client_id"] += 10
        return rows

    with pytest.raises(KeeperMembershipError, match="changed during"):
        attest_keeper_membership(members(), Probe(alter=alter))


def test_same_session_ids_from_separate_keepers_are_rejected():
    with pytest.raises(KeeperMembershipError, match="share a Keeper session"):
        attest_keeper_membership(members(), Probe(sessions=(1, 1)))


@pytest.mark.parametrize(
    "kind", ["one", "duplicate_uuid", "duplicate_host", "reversed", "different_paths"]
)
def test_incomplete_or_aliased_topology_is_rejected_before_query(kind):
    nodes = members()
    if kind == "one":
        nodes = nodes[:1]
    elif kind == "duplicate_uuid":
        nodes = (nodes[0], replace(nodes[1], server_uuid=nodes[0].server_uuid))
    elif kind == "duplicate_host":
        nodes = (nodes[0], replace(nodes[1], hostname=nodes[0].hostname))
    elif kind == "reversed":
        nodes = nodes[::-1]
    else:
        nodes = (
            nodes[0],
            replace(
                nodes[1],
                table_paths=tuple((n, p + "_other") for n, p in nodes[1].table_paths),
            ),
        )
    probe = Probe()
    with pytest.raises(KeeperMembershipError):
        attest_keeper_membership(nodes, probe)
    assert not probe.calls


def test_timeout_and_transport_errors_cannot_be_membership_proof():
    ticks = iter((0, 0, 31))
    with pytest.raises(KeeperMembershipError, match="bounded"):
        attest_keeper_membership(members(), Probe(), clock=lambda: next(ticks))

    def failed(*_):
        raise RuntimeError("unreachable")

    with pytest.raises(KeeperMembershipError, match="could not be observed"):
        attest_keeper_membership(members(), failed)
