"""Bound source disk policy with fake metadata, real manager/native composition."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

import pytest

from tracer.services.clickhouse.v2.property_catalog import native_write_transport
from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    DurableSourceCapture,
    SourceCaptureError,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_capacity import (
    MAX_CAPTURE_POLICY_DISKS,
    MIN_CAPTURE_HEADROOM_BYTES,
    SourceCaptureCapacity,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_native import (
    CaptureResourceBudget,
    NativeSourceCaptureBackend,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_reservations import (
    SourceCaptureBackpressure,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import WriteMember
from tracer.tests.test_property_catalog_source_capture import specification
from tracer.tests.test_property_catalog_source_capture_native import Driver, Schema

_MIB = 1 << 20
_FIELDS = "`name`, `path`, `total_space`, `unreserved_space`, `keep_free_space`, `type`, `is_read_only`"


class Reader:
    def __init__(self, spec, native=None):
        self.database = spec.source_database
        self.user = "source_reader"
        self.server_enforced_readonly = True
        self.server_uuid = spec.source_server_uuid
        self.native, self.calls = native, []
        self.disks = [
            ["default", "/var/lib/clickhouse/", 1000 * _MIB, 500 * _MIB, 0, "Local", 0]
        ]
        self.source = [(spec.source_table_uuid, "tiered")]
        self.policy_disks = ["default"]
        self.part_disks = ["default"]
        self.unrelated_disks = []
        self.columns = [
            ("capture_server_uuid", "String"),
            ("capture_source", "Array(Tuple)"),
            ("capture_policy_disks", "Array(String)"),
            ("capture_part_disks", "Array(String)"),
            ("capture_disks", "Array(Tuple)"),
        ]
        self.result_override = None

    def execute_read(self, sql, params, **options):
        if "FROM system.disks" not in sql:
            assert self.native is not None
            return self.native.execute_read(sql, params, **options)
        self.calls.append((sql, params, options))
        # Locked readonly=1 accepts no settings overrides. Assert the executed
        # statement's exact read/field/bound contract, not source-file text.
        assert options == {"timeout_ms": 30_000}
        assert params == {"database": self.database}
        assert sql.startswith("WITH capture_source_table AS (")
        assert "SELECT toString(serverUUID()) AS capture_server_uuid," in sql
        assert (
            "SELECT toString(uuid) AS table_uuid, storage_policy FROM system.tables"
            in sql
        )
        assert "WHERE database=%(database)s AND name='spans' LIMIT 2" in sql
        assert (
            "SELECT DISTINCT arrayJoin(disks) AS disk_name FROM system.storage_policies"
            in sql
        )
        assert (
            "WHERE policy_name IN (SELECT storage_policy FROM capture_source_table)"
            in sql
        )
        assert "SELECT DISTINCT disk_name FROM system.parts" in sql
        assert "WHERE database=%(database)s AND table='spans' AND active" in sql
        assert f"SELECT {_FIELDS} FROM system.disks" in sql
        assert "WHERE name IN (SELECT disk_name FROM capture_policy_disk_names)" in sql
        assert sql.count(f"LIMIT {MAX_CAPTURE_POLICY_DISKS + 1}") == 3
        if self.result_override is not None:
            return self.result_override
        disks = self.disks
        if self.unrelated_disks:
            disks = list(disks) + [
                d for d in self.unrelated_disks if d[0] in self.policy_disks
            ]
        return (
            [
                (
                    self.server_uuid,
                    self.source,
                    self.policy_disks,
                    self.part_disks,
                    disks,
                )
            ],
            self.columns,
            0,
        )


def capacity_case():
    spec = specification()
    reader = Reader(spec)
    return spec, reader, SourceCaptureCapacity(spec, reader)


def test_fresh_source_metadata_and_exact_percentage_headroom_boundary():
    spec, reader, capacity = capacity_case()
    reader.disks[0][3] = 100 * _MIB
    capacity(spec, 900 * _MIB)  # Hardlinks do not allocate a second copy.
    reader.disks[0][3] -= 1
    with pytest.raises(SourceCaptureBackpressure, match="headroom"):
        capacity(spec, 900 * _MIB)
    assert len(reader.calls) == 2


def test_minimum_headroom_and_empty_capture_still_need_spare_space():
    spec, reader, capacity = capacity_case()
    reader.disks[0][2:4] = [128 * _MIB, MIN_CAPTURE_HEADROOM_BYTES]
    capacity(spec, 0)
    capacity(spec, 128 * _MIB)
    reader.disks[0][3] -= 1
    with pytest.raises(SourceCaptureBackpressure):
        capacity(spec, 0)


def test_keep_free_space_already_excluded_by_clickhouse_local_disk():
    spec, reader, capacity = capacity_case()
    # 25.3 reports total/unreserved after subtracting configured keep-free.
    # It is valid for keep-free itself to exceed the remaining reported total.
    reader.disks[0][4] = 2000 * _MIB
    capacity(spec, 400 * _MIB)


def test_percentage_headroom_rounds_up():
    spec, reader, capacity = capacity_case()
    reader.disks[0][2:4] = [1000 * _MIB + 1, 100 * _MIB]
    with pytest.raises(SourceCaptureBackpressure):
        capacity(spec, 0)
    reader.disks[0][3] += 1
    capacity(spec, 0)


@pytest.mark.parametrize("disks", [[], [None], "unknown", [[0] * 7, [0] * 7]])
def test_no_disk_or_ambiguous_disks_never_invent_target_capacity(disks):
    spec, reader, capacity = capacity_case()
    reader.disks = disks
    with pytest.raises(SourceCaptureError):
        capacity(spec, 0)


@pytest.mark.parametrize("disks", [[], [[0] * 7]])
def test_empty_and_nonempty_disk_results_bind_same_query_server(disks):
    spec, reader, capacity = capacity_case()
    reader.disks, reader.server_uuid = disks, str(uuid4())
    with pytest.raises(SourceCaptureError, match="another source member"):
        capacity(spec, 0)


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (1, "relative/path"),
        (1, "/" + "x" * 4096),
        (1, "/bad\x00path"),
        (1, None),
        (5, "S3"),
        (5, "Memory"),
        (5, "local"),
        (6, 1),
        (6, False),
        (6, "0"),
    ],
)
def test_policy_disk_must_be_writable_local_with_exact_path(index, value):
    spec, reader, capacity = capacity_case()
    reader.disks[0][index] = value
    with pytest.raises(SourceCaptureError, match="writable Local"):
        capacity(spec, 0)


@pytest.mark.parametrize("index", [2, 3, 4])
@pytest.mark.parametrize("value", [None, False, -1, "100", 2**64 - 1, 2**64])
def test_disk_sizes_are_exact_finite_unsigned_values(index, value):
    spec, reader, capacity = capacity_case()
    reader.disks[0][index] = value
    with pytest.raises(SourceCaptureError, match="finite UInt64"):
        capacity(spec, 0)


@pytest.mark.parametrize("sizes", [(0, 0), (100, 101)])
def test_inconsistent_or_unknown_total_fails(sizes):
    spec, reader, capacity = capacity_case()
    reader.disks[0][2:4] = sizes
    with pytest.raises(SourceCaptureError, match="inconsistent or unavailable"):
        capacity(spec, 0)


@pytest.mark.parametrize("proposed", [None, False, -1, "100", 2**64 - 1, 2**64])
def test_invalid_proposal_is_rejected_before_read(proposed):
    spec, reader, capacity = capacity_case()
    with pytest.raises(SourceCaptureError, match="finite UInt64"):
        capacity(spec, proposed)
    assert not reader.calls


@pytest.mark.parametrize(
    "field",
    [
        "installation_id",
        "organization_id",
        "workspace_id",
        "build_token",
        "source_server_uuid",
        "source_table_uuid",
        "source_database",
        "catalog_database",
        "source_schema_sha256",
        "capture_schema_sha256",
    ],
)
def test_capacity_cannot_be_rebound_to_another_exact_spec(field):
    spec, reader, capacity = capacity_case()
    value = "other_database" if field.endswith("database") else str(uuid4())
    if field.endswith("sha256"):
        value = "f" * 64
    with pytest.raises(SourceCaptureError, match="bound spec"):
        capacity(replace(spec, **{field: value}), 0)
    assert not reader.calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database", "catalog_db"),
        ("user", ""),
        ("server_enforced_readonly", False),
        ("server_enforced_readonly", 1),
        ("execute_read", None),
    ],
)
def test_capacity_rejects_non_source_readonly_identity(field, value):
    spec, reader, capacity = capacity_case()
    setattr(reader, field, value)
    with pytest.raises(SourceCaptureError, match="read-only identity"):
        capacity(spec, 0)
    with pytest.raises(SourceCaptureError, match="read-only identity"):
        SourceCaptureCapacity(spec, reader)
    assert not reader.calls


def test_source_user_cannot_change_after_binding():
    spec, reader, capacity = capacity_case()
    reader.user = "different_reader"
    with pytest.raises(SourceCaptureError, match="read-only identity"):
        capacity(spec, 0)
    assert not reader.calls


@pytest.mark.parametrize(
    "result",
    [
        (),
        (None, None, 0),
        ([], [], 0),
        ([()], ["capture_server_uuid", "capture_disks"], 0),
        ([("a", [], 1)], ["capture_server_uuid", "capture_disks"], 0),
    ],
)
def test_malformed_native_envelope_fails_closed(result):
    spec, reader, capacity = capacity_case()
    reader.result_override = result
    with pytest.raises(SourceCaptureError, match="malformed"):
        capacity(spec, 0)


def test_column_reordering_or_wrong_disk_tuple_width_is_rejected():
    spec, reader, capacity = capacity_case()
    reader.columns.reverse()
    with pytest.raises(SourceCaptureError, match="malformed"):
        capacity(spec, 0)
    reader.columns.reverse()
    reader.disks[0].append("extra")
    with pytest.raises(SourceCaptureError, match="malformed"):
        capacity(spec, 0)


def test_real_native_manager_blocks_before_create_then_admits_fresh_capacity(
    tmp_path, monkeypatch
):
    spec, reader, capacity = tiered_case()
    schema = Schema(spec)
    driver = Driver(spec, schema)
    reader.native = driver
    member = WriteMember(
        "source-db-id", "node", "node", spec.source_server_uuid, (), "http://node:8123"
    )
    backend = NativeSourceCaptureBackend(
        driver,
        source_reader=reader,
        member=member,
        spec=spec,
        schema=schema,
        budget=CaptureResourceBudget(2, 10, 1024),
        capacity_reservation=capacity,
    )
    monkeypatch.setattr(
        native_write_transport,
        "ordinary_once",
        lambda d, **options: d.command(**options),
    )
    manager = DurableSourceCapture(str(tmp_path), backend, can_retire=lambda _: False)
    reader.disks[0][3] = (reader.disks[0][2] + 9) // 10 - 1
    with pytest.raises(SourceCaptureBackpressure, match="headroom"):
        manager.acquire(spec)
    assert driver.commands == []
    assert manager._load(spec) is None
    assert manager.reservation_snapshot(spec) == ()
    reader.disks[0][3] += 1
    observed = manager.acquire(spec)
    assert observed.bytes_on_disk == 160
    assert len(driver.commands) == 2  # the real CREATE then ATTACH code paths
    assert driver.commands[0] == schema.create_sql
    assert driver.commands[1].startswith("ALTER TABLE")
    assert len(reader.calls) == 2
    assert manager.reservation_snapshot(spec)[0].phase == "captured"


def tiered_case():
    spec, reader, capacity = capacity_case()
    # Use the actual OSS policy shape, not a newly invented fixture topology.
    root = Path(__file__).resolve().parents[2]
    policy = ElementTree.parse(root / ".ci/clickhouse-storage-policy.xml")
    reader.policy_disks = [
        v.text for v in policy.findall(".//policies/tiered/volumes/*/disk")
    ]
    assert set(reader.policy_disks) == {"default", "cold_local"}
    cold = policy.find(".//disks/cold_local")
    reader.disks = [
        ["default", "/var/lib/clickhouse/", 2048 * _MIB, 1024 * _MIB, 0, "Local", 0],
        [
            "cold_local",
            cold.findtext("path"),
            1536 * _MIB,
            512 * _MIB,
            int(cold.findtext("keep_free_space_bytes")),
            "Local",
            0,
        ],
    ]
    reader.part_disks = list(reader.policy_disks)
    return spec, reader, capacity


def test_checked_in_tiered_local_paths_allow_hardlinks_without_copy_sized_spare():
    spec, _, capacity = tiered_case()
    capacity(spec, 1500 * _MIB)  # Larger than spare on either disk, below retained cap.
    budget = capacity.resource_budget()
    assert budget == CaptureResourceBudget(2, 10_000, 3072 * _MIB)


@pytest.mark.parametrize("index", [0, 1])
def test_each_policy_disk_needs_its_own_headroom_never_sum_free_space(index):
    spec, reader, capacity = tiered_case()
    disk = reader.disks[index]
    disk[3] = (disk[2] + 9) // 10 - 1
    # Budget qualification is not new-allocation admission: low spare alone
    # does not prevent reopening/verifying/retiring an already owned capture.
    assert capacity.resource_budget().max_tables == 2
    with pytest.raises(SourceCaptureBackpressure, match="headroom"):
        capacity(spec, 0)


def test_unrelated_remote_server_disk_is_not_in_source_policy():
    spec, reader, capacity = tiered_case()
    reader.unrelated_disks = [
        ["unrelated_s3", "", 2**64 - 1, 2**64 - 1, 0, "ObjectStorage", 0]
    ]
    capacity(spec, 1500 * _MIB)


@pytest.mark.parametrize("index", [0, 1])
def test_remote_disk_in_source_policy_fails_even_when_no_active_parts_use_it(index):
    spec, reader, capacity = tiered_case()
    reader.part_disks = []
    reader.disks[index][5] = "ObjectStorage"
    with pytest.raises(SourceCaptureError, match="writable Local") as error:
        capacity(spec, 0)
    assert not isinstance(error.value, SourceCaptureBackpressure)


def test_empty_source_still_qualifies_complete_storage_policy():
    spec, reader, capacity = tiered_case()
    reader.part_disks = []
    capacity(spec, 0)
    reader.policy_disks = []
    with pytest.raises(SourceCaptureError):
        capacity(spec, 0)


@pytest.mark.parametrize("field", ["policy_disks", "part_disks"])
@pytest.mark.parametrize(
    "value", [[], [""], ["default", "default"], [f"disk_{i}" for i in range(33)], None]
)
def test_policy_and_part_disk_names_are_complete_bounded_unique(field, value):
    spec, reader, capacity = capacity_case()
    setattr(reader, field, value)
    if field == "part_disks" and value == []:
        capacity(spec, 0)
        assert len(reader.calls) == 1
        return
    with pytest.raises(SourceCaptureError):
        capacity(spec, 0)


def test_part_disk_outside_policy_never_silently_infers_hardlink_placement():
    spec, reader, capacity = capacity_case()
    reader.part_disks = ["unconfigured_disk"]
    with pytest.raises(SourceCaptureError, match="outside the capture policy"):
        capacity(spec, 0)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "oversized", "unknown"])
def test_every_relevant_disk_requires_exact_bounded_metadata(mutation):
    spec, reader, capacity = tiered_case()
    if mutation == "missing":
        reader.disks.pop()
    elif mutation == "duplicate":
        reader.disks.append(reader.disks[0])
    elif mutation == "oversized":
        reader.disks *= 17
    else:
        reader.disks[0][0] = "unknown"
    with pytest.raises(SourceCaptureError):
        capacity(spec, 0)


@pytest.mark.parametrize(
    "source",
    [
        [],
        [(str(uuid4()), "tiered")],
        [("uuid", "")],
        [("uuid", "tiered"), ("uuid", "tiered")],
        None,
    ],
)
def test_source_incarnation_and_policy_cannot_be_missing_or_rebound(source):
    spec, reader, capacity = capacity_case()
    reader.source = source
    with pytest.raises(SourceCaptureError, match="table UUID or storage policy"):
        capacity(spec, 0)


def test_composed_retained_byte_exhaustion_remains_backpressure_with_abundant_spare(
    tmp_path, monkeypatch
):
    spec, reader, capacity = capacity_case()
    schema, finished = Schema(spec), []
    driver = Driver(spec, schema)
    reader.native = driver
    backend = NativeSourceCaptureBackend(
        driver,
        source_reader=reader,
        member=WriteMember(
            "source-db-id",
            "node",
            "node",
            spec.source_server_uuid,
            (),
            "http://node:8123",
        ),
        spec=spec,
        schema=schema,
        budget=CaptureResourceBudget(2, 10, 159),
        capacity_reservation=capacity,
    )
    monkeypatch.setattr(
        native_write_transport,
        "ordinary_once",
        lambda d, **options: d.command(**options),
    )
    manager = DurableSourceCapture(
        str(tmp_path), backend, can_retire=lambda _: bool(finished)
    )
    with pytest.raises(SourceCaptureBackpressure, match="retained bytes"):
        manager.acquire(spec)
    assert driver.commands == [] and reader.calls == []
    assert manager._load(spec) is None
