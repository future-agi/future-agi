"""Real coordinator/locks/journal with latest-state SQL executed in SQLite."""

from dataclasses import replace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    PropertyCatalogCoordinatorError,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    ClickHouseCatalogStateStore,
    _activation_row,
    activation_latest_rows_sql,
)
from tracer.tests.test_property_catalog_activation_latest_status import (
    DATABASE,
    _record,
    _RelationalClient,
)
from tracer.tests.test_property_catalog_supersession import _Harness


class HeadHarness:
    def __init__(self, directory):
        self.base = _Harness(directory)
        self.relation = _RelationalClient()
        self.base.client.catalog_database = DATABASE
        source_query = self.base.client.query

        def query(sql, params, *, timeout_ms):
            if "property_catalog_activations" in sql:
                return self.relation.query(sql, params, timeout_ms=timeout_ms)
            return source_query(sql, params, timeout_ms=timeout_ms)

        self.base.client.query = query
        self.base.restart()
        self.lease = self.base.build.lease
        self.scope = {
            "organization_id": self.lease.organization_id,
            "workspace_id": self.lease.workspace_id,
            "catalog_epoch": self.lease.catalog_epoch,
        }
        prior = self.base.reader.active
        self.active = self.record(
            revision=prior.catalog_revision,
            sequence=prior.activation_sequence,
            build_token=prior.build_token,
            activation_sha256=prior.activation_sha256,
        )
        self.store = ClickHouseCatalogStateStore(
            self.base.client,
            database=DATABASE,
            serializer=self.base.coordinator._serializer,
        )

    def record(self, *, revision=1, sequence=1, **changes):
        return replace(
            _record(revision=revision, sequence=sequence),
            **self.scope,
            projection_version=self.lease.projection_version,
            **changes,
        )

    def check(self, expected):
        coordinator = self.base.coordinator
        coordinator._serializer.serialize(
            coordinator._revision_key_for_lease(self.lease),
            lambda: coordinator._assert_active_head(self.lease, expected),
        )

    def marker(self, **changes):
        coordinator = self.base.coordinator
        key = coordinator._revision_key_for_lease(self.lease) + ":activation"
        journal = coordinator._recovery_journal
        journal.save_record(
            key,
            {
                "catalog_revision": self.lease.catalog_revision,
                "build_token": self.lease.build_token,
                "build_lease_sha256": self.lease.build_lease_sha256,
                **changes,
            },
        )
        return journal._path(key)


@pytest.fixture
def h(tmp_path):
    harness = HeadHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.relation.connection.close()


def _head(record):
    return [
        record.catalog_revision,
        record.build_token,
        record.projection_version,
        record.activation_sequence,
        record.activation_sha256,
        "active",
    ]


def _inactive(h, *, status="disabled", revision=2, sequence=7):
    return {
        **_activation_row(h.record(revision=revision, sequence=sequence)),
        "status": status,
        "_version": sequence + 1,
    }


@pytest.mark.parametrize("status", ["disabled", "building"])
@pytest.mark.parametrize("physical_order", ["active_first", "inactive_first", "merged"])
def test_inactive_latest_slot_is_not_the_active_predecessor(h, status, physical_order):
    obsolete = _activation_row(h.record(revision=2, sequence=7))
    inactive = {**obsolete, "status": status, "_version": 8}
    physical = {
        "active_first": (obsolete, inactive),
        "inactive_first": (inactive, obsolete),
        "merged": (inactive,),
    }[physical_order]
    h.relation.seed(_activation_row(h.active), *physical)

    h.check(_head(h.active))
    assert h.relation.queries == [activation_latest_rows_sql(DATABASE)]
    history = h.store.load_activation_history(**h.scope)
    assert history.active_records == (h.active,)
    assert (history.maximum_revision, history.maximum_sequence) == (2, 7)
    assert not h.base.client.inserts


@pytest.mark.parametrize("status", ["disabled", "building"])
def test_all_inactive_history_has_no_trustworthy_active_head(h, status):
    old = h.record(revision=2, sequence=7)
    h.relation.seed(_activation_row(old), _inactive(h, status=status))
    h.check(None)
    with pytest.raises(PropertyCatalogCoordinatorError, match="active lineage changed"):
        h.check(_head(old))
    assert not h.base.client.inserts


def test_empty_history_requires_empty_predecessor(h):
    h.check(None)
    with pytest.raises(PropertyCatalogCoordinatorError, match="active lineage changed"):
        h.check(_head(h.active))


def test_newer_active_head_still_rejects_stale_predecessor(h):
    newer = h.record(revision=2, sequence=2)
    h.relation.seed(_activation_row(h.active), _activation_row(newer))
    with pytest.raises(PropertyCatalogCoordinatorError, match="active lineage changed"):
        h.check(_head(h.active))
    h.check(_head(newer))


def test_exact_physical_replays_do_not_make_a_head_ambiguous(h):
    h.relation.seed(*([_activation_row(h.active)] * 40), *([_inactive(h)] * 40))
    h.check(_head(h.active))
    assert len(h.relation.queries) == 1


@pytest.mark.parametrize("status", ["active", "building", "disabled"])
@pytest.mark.parametrize("change", [{"value_rows": 999}, {"status": "building"}])
def test_equal_version_conflicts_are_checked_before_status_filtering(h, status, change):
    row = {**_activation_row(h.active), "status": status}
    if change == {"status": "building"} and status == "building":
        change = {"status": "disabled"}
    # The conflicting key is below the newest sequence, so a max-slot SELECT
    # would miss it even when the newest ACTIVE head itself is unambiguous.
    h.relation.seed(
        row, {**row, **change}, _activation_row(h.record(revision=2, sequence=2))
    )
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="ambiguous or invalid"
    ) as error:
        h.check(_head(h.record(revision=2, sequence=2)))
    assert "different rows" in str(error.value.__cause__)
    assert not h.base.client.inserts


def test_unique_dominant_invalidation_supersedes_obsolete_conflicts(h):
    obsolete = _activation_row(h.record(revision=2, sequence=7))
    h.relation.seed(
        _activation_row(h.active),
        obsolete,
        {**obsolete, "value_rows": 999},
        {**obsolete, "status": "disabled", "_version": 8},
    )
    h.check(_head(h.active))


@pytest.mark.parametrize("left_status", ["active", "building", "disabled"])
@pytest.mark.parametrize("right_status", ["active", "building", "disabled"])
@pytest.mark.parametrize("owner_field", ["catalog_revision", "activation_sequence"])
def test_all_status_duplicate_positive_owners_remain_conflicts(
    h, left_status, right_status, owner_field
):
    right = h.record(
        revision=1 if owner_field == "catalog_revision" else 2,
        sequence=1 if owner_field == "activation_sequence" else 2,
        build_token=str(UUID(int=500)),
    )
    h.relation.seed(
        {**_activation_row(h.active), "status": left_status},
        {**_activation_row(right), "status": right_status},
    )
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="ambiguous or invalid"
    ) as error:
        h.check(None)
    assert owner_field in str(error.value.__cause__)


def test_unknown_latest_status_is_not_a_retirement_receipt(h):
    h.relation.seed(_activation_row(h.active), _inactive(h, status="unknown"))
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="ambiguous or invalid"
    ) as error:
        h.check(_head(h.active))
    assert "unsupported latest status" in str(error.value.__cause__)


def test_incomplete_unversioned_transport_rows_are_rejected(h, monkeypatch):
    query = h.relation.query

    def incomplete(*args, **kwargs):
        return tuple(
            {key: value for key, value in row.items() if key != "_version"}
            for row in query(*args, **kwargs)
        )

    h.relation.seed(_activation_row(h.active))
    monkeypatch.setattr(h.relation, "query", incomplete)
    with pytest.raises(PropertyCatalogCoordinatorError, match="ambiguous or invalid"):
        h.check(_head(h.active))


def test_native_uuid_transport_values_preserve_exact_predecessor(h, monkeypatch):
    query = h.relation.query

    def native(*args, **kwargs):
        return tuple(
            {
                key: UUID(value)
                if key in {"organization_id", "workspace_id", "build_token"}
                else value
                for key, value in row.items()
            }
            for row in query(*args, **kwargs)
        )

    h.relation.seed(_activation_row(h.active), _inactive(h))
    monkeypatch.setattr(h.relation, "query", native)
    h.check(_head(h.active))


@pytest.mark.parametrize("field", ["organization_id", "workspace_id", "catalog_epoch"])
def test_other_scope_invalidations_do_not_hide_this_predecessor(h, field):
    value = 99 if field == "catalog_epoch" else str(UUID(int=999))
    h.relation.seed(_activation_row(h.active), {**_inactive(h), field: value})
    h.check(_head(h.active))


@pytest.mark.parametrize("status", ["disabled", "building"])
def test_consumed_revision_above_reservation_cannot_be_reallocated(h, status):
    h.relation.seed(_activation_row(h.active), _inactive(h, revision=3, status=status))
    with pytest.raises(PropertyCatalogCoordinatorError, match="newer revision exists"):
        h.base.prepare()
    assert not h.base.client.inserts
    assert not list(h.base.directory.glob("*.supersession.json"))


@pytest.mark.parametrize("status", ["disabled", "building"])
def test_real_replacement_retains_active_predecessor_and_consumed_slot(h, status):
    h.relation.seed(_activation_row(h.active), _inactive(h, status=status))
    replacement = h.base.prepare()
    assert replacement.lease.catalog_revision == 3
    assert replacement.lease.build_token not in {
        h.active.build_token,
        h.lease.build_token,
    }
    intent = h.base.coordinator._recovery_journal.load(
        h.base.coordinator._revision_key_for_lease(h.lease)
    )
    assert intent["active_head"] == _head(h.active)
    assert intent["complete"]
    assert [batch[0]["status"] for batch in h.base.client.inserts] == ["failed", "open"]
    history = h.store.load_activation_history(**h.scope)
    assert history.active_records == (h.active,)
    assert (history.maximum_revision, history.maximum_sequence) == (2, 7)


@pytest.mark.parametrize("expected_active", [False, True])
def test_disabled_latest_publication_marker_still_blocks_before_read(
    h, expected_active
):
    if expected_active:
        h.relation.seed(_activation_row(h.active))
    h.relation.seed(_inactive(h))
    path = h.marker()
    original = path.read_bytes()
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="activation outcome is unresolved"
    ):
        h.check(_head(h.active) if expected_active else None)
    assert path.read_bytes() == original
    assert not h.relation.queries and not h.base.client.inserts


def test_same_revision_other_token_marker_is_not_bypassed(h):
    h.relation.seed(_activation_row(h.active))
    path = h.marker(
        catalog_revision=h.active.catalog_revision, build_token=str(UUID(int=999))
    )
    original = path.read_bytes()
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="activation outcome is unresolved"
    ):
        h.check(_head(h.active))
    assert path.read_bytes() == original and not h.relation.queries


def test_matching_active_marker_is_preserved_while_ignoring_disabled_head(h):
    h.relation.seed(_activation_row(h.active), _inactive(h))
    path = h.marker(
        catalog_revision=h.active.catalog_revision, build_token=h.active.build_token
    )
    original = path.read_bytes()
    h.check(_head(h.active))
    assert path.read_bytes() == original
    assert h.relation.queries == [activation_latest_rows_sql(DATABASE)]
