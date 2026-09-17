from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_activation import (
    CATALOG_ACTIVATION_ACK,
    CATALOG_ACTIVATION_ENVIRONMENT,
    CATALOG_ACTIVATION_SUPERSESSION_ACK,
    CATALOG_PROJECTION_VERSION,
    CatalogActivationConfig,
    CatalogActivationError,
    CatalogFrozenEpochActivator,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
SINCE = datetime(2026, 8, 13, 0, tzinfo=UTC)
UNTIL = SINCE + timedelta(hours=2)


def _checkpoint(start, **overrides):
    row = {
        "window_start": start,
        "window_end": start + timedelta(hours=1),
        "source_version_fence": 91,
        "status": "complete",
        "source_rows": 10,
        "processed_rows": 10,
        "key_rows": 6,
        "value_rows": 4,
        "gap_count": 0,
        "gap_reasons": [],
        "projection_version": CATALOG_PROJECTION_VERSION,
        "state_version": 100,
        "latest_state_variants": 1,
    }
    row.update(overrides)
    return row


def _config(**overrides):
    values = {
        "environment": CATALOG_ACTIVATION_ENVIRONMENT,
        "acknowledgement": CATALOG_ACTIVATION_ACK,
        "project_id": PROJECT_ID,
        "catalog_epoch": 7,
        "since": SINCE,
        "until": UNTIL,
        "target_database": "property_catalog_dev",
    }
    values.update(overrides)
    return CatalogActivationConfig(**values)


class _IO:
    def __init__(
        self,
        *,
        checkpoints=None,
        key_bounds=None,
        value_bounds=None,
        streams=None,
        delivery=None,
        activation=None,
    ):
        self.checkpoints = checkpoints or [
            _checkpoint(SINCE),
            _checkpoint(SINCE + timedelta(hours=1)),
        ]
        self.streams = streams or []
        self.key_bounds = key_bounds or [{"row_count": 6, "out_of_window_count": 0}]
        self.value_bounds = value_bounds or [{"row_count": 4, "out_of_window_count": 0}]
        self.delivery = delivery or [
            {
                "delivery_count": 0,
                "gap_count": 0,
                "gap_reason_count": 0,
                "version_conflict_count": 0,
            }
        ]
        self.activation = activation or []
        self.selects = []
        self.inserts = []

    def select(self, sql, params, *, settings):
        self.selects.append((sql, params, settings))
        if "span_attribute_catalog_checkpoints" in sql:
            return self.checkpoints
        if "span_attribute_key_catalog" in sql:
            return self.key_bounds
        if "span_attribute_value_catalog" in sql:
            return self.value_bounds
        if "span_attribute_catalog_source_streams" in sql:
            return self.streams
        if "span_attribute_catalog_deliveries" in sql:
            return self.delivery
        if "span_attribute_catalog_activations" in sql:
            return self.activation
        raise AssertionError(sql)

    def insert(self, table, rows, columns, *, settings):
        self.inserts.append((table, rows, columns, settings))


def test_frozen_epoch_activation_audits_then_writes_only_new_state_tables():
    io = _IO()
    now = datetime(2026, 8, 13, 3, tzinfo=UTC)

    summary = CatalogFrozenEpochActivator(
        io,
        _config(),
        now=lambda: now,
    ).run()

    assert summary.checkpoint_count == 2
    assert summary.source_rows == 20
    assert summary.key_rows == 12
    assert summary.value_rows == 8
    assert summary.rows_written == 2
    assert len(summary.source_fence_digest) == 64
    assert [call[0] for call in io.inserts] == [
        "`property_catalog_dev`.`span_attribute_catalog_source_streams`",
        "`property_catalog_dev`.`span_attribute_catalog_activations`",
    ]
    assert all("spans" not in sql.lower().split() for sql, _, _ in io.selects)
    assert all(settings["readonly"] == 1 for _, _, settings in io.selects)
    assert io.inserts[0][1][0][9] == "frozen"
    assert io.inserts[1][1][0][5] == "active"


def test_dry_run_performs_all_select_audits_and_no_insert():
    io = _IO()

    summary = CatalogFrozenEpochActivator(
        io,
        _config(dry_run=True),
    ).run()

    assert len(io.selects) == 6
    assert io.inserts == []
    assert summary.dry_run is True
    assert summary.rows_written == 0


@pytest.mark.parametrize(
    ("checkpoints", "match"),
    (
        ([_checkpoint(SINCE)], "incomplete"),
        (
            [_checkpoint(SINCE), _checkpoint(SINCE + timedelta(hours=2))],
            "gap or overlap",
        ),
        (
            [
                _checkpoint(SINCE, status="running"),
                _checkpoint(SINCE + timedelta(hours=1)),
            ],
            "not complete",
        ),
        (
            [
                _checkpoint(SINCE, gap_count=1, gap_reasons=["bad"]),
                _checkpoint(SINCE + timedelta(hours=1)),
            ],
            "declares a gap",
        ),
        (
            [
                _checkpoint(SINCE, processed_rows=9),
                _checkpoint(SINCE + timedelta(hours=1)),
            ],
            "disagree",
        ),
        (
            [
                _checkpoint(SINCE, latest_state_variants=2),
                _checkpoint(SINCE + timedelta(hours=1)),
            ],
            "conflicts",
        ),
    ),
)
def test_checkpoint_gaps_and_conflicts_fail_before_writes(checkpoints, match):
    io = _IO(checkpoints=checkpoints)

    with pytest.raises(CatalogActivationError, match=match):
        CatalogFrozenEpochActivator(io, _config()).run()

    assert io.inserts == []


def test_checkpoint_audit_rejects_rows_outside_the_exact_epoch_window():
    io = _IO(
        checkpoints=[
            _checkpoint(SINCE - timedelta(hours=1)),
            _checkpoint(SINCE),
            _checkpoint(SINCE + timedelta(hours=1)),
        ]
    )

    with pytest.raises(CatalogActivationError, match="coverage is incomplete"):
        CatalogFrozenEpochActivator(io, _config()).run()

    assert io.inserts == []


@pytest.mark.parametrize("family", ("key", "value"))
def test_catalog_rows_outside_the_exact_epoch_window_fail_before_writes(family):
    kwargs = {
        f"{family}_bounds": [
            {"row_count": 1, "out_of_window_count": 1},
        ]
    }
    io = _IO(**kwargs)

    with pytest.raises(CatalogActivationError, match="outside the activation window"):
        CatalogFrozenEpochActivator(io, _config()).run()

    assert io.inserts == []


def test_open_writer_stream_and_any_live_delivery_fail_closed():
    open_stream = {
        "producer_stream_id": "00000000-0000-4000-8000-000000000002",
        "envelope_version": 1,
        "first_sequence": 1,
        "last_sequence": 1,
        "frozen_sequence": 0,
        "terminal_payload_sha256": "0" * 64,
        "source_fence_digest": "1" * 64,
        "status": "open",
        "gap_count": 0,
        "gap_reasons": [],
        "latest_state_variants": 1,
    }
    io = _IO(streams=[open_stream])
    with pytest.raises(CatalogActivationError, match="open writer"):
        CatalogFrozenEpochActivator(io, _config()).run()
    assert io.inserts == []

    io = _IO(
        delivery=[
            {
                "delivery_count": 1,
                "gap_count": 0,
                "gap_reason_count": 0,
                "version_conflict_count": 0,
            }
        ]
    )
    with pytest.raises(CatalogActivationError, match="backfill-only"):
        CatalogFrozenEpochActivator(io, _config()).run()
    assert io.inserts == []


@pytest.mark.parametrize(
    "config",
    (
        _config(environment="production"),
        _config(acknowledgement="wrong"),
        _config(target_database="futureagi"),
        _config(target_database="catalog_prod"),
        _config(catalog_epoch=0),
        _config(until=SINCE + timedelta(hours=366 * 24 + 1)),
    ),
)
def test_activation_guard_rejects_non_dev_or_unsafe_scope(config):
    with pytest.raises(CatalogActivationError):
        config.validated()


def test_activation_is_idempotent_only_for_identical_frozen_evidence():
    first_io = _IO()
    first = CatalogFrozenEpochActivator(first_io, _config()).run()
    stream_values = first_io.inserts[0][1][0]
    stream_columns = first_io.inserts[0][2]
    stream = dict(zip(stream_columns, stream_values, strict=True))
    stream["latest_state_variants"] = 1
    activation_values = first_io.inserts[1][1][0]
    activation_columns = first_io.inserts[1][2]
    activation = dict(zip(activation_columns, activation_values, strict=True))
    activation["state_version"] = activation.pop("_version")
    activation["latest_state_variants"] = 1

    second_io = _IO(streams=[stream], activation=[activation])
    second = CatalogFrozenEpochActivator(second_io, _config()).run()

    assert first.source_fence_digest == second.source_fence_digest
    assert second.already_active is True
    assert second.rows_written == 0
    assert second_io.inserts == []

    conflict_io = _IO(streams=[{**stream, "source_fence_digest": "f" * 64}])
    with pytest.raises(CatalogActivationError, match="does not match"):
        CatalogFrozenEpochActivator(conflict_io, _config()).run()


def _active_v1(**overrides):
    row = {
        "catalog_epoch": 6,
        "projection_version": 1,
        "handoff_start": SINCE,
        "handoff_end": UNTIL,
        "writer_watermark": UNTIL,
        "status": "active",
        "state_version": 9_999_999_999_999_999,
        "latest_state_variants": 1,
    }
    row.update(overrides)
    return row


def _supersession_config(**overrides):
    return _config(
        allow_projection_supersession=True,
        supersession_acknowledgement=CATALOG_ACTIVATION_SUPERSESSION_ACK,
        **overrides,
    )


def test_projection_supersession_is_explicit_and_version_monotonic():
    old = _active_v1()
    io = _IO(activation=[old])

    summary = CatalogFrozenEpochActivator(
        io,
        _supersession_config(),
        now=lambda: datetime(2026, 8, 13, 3, tzinfo=UTC),
    ).run()

    assert summary.superseded_epoch == 6
    assert summary.already_active is False
    activation_call = io.inserts[1]
    activation = dict(zip(activation_call[2], activation_call[1][0], strict=True))
    assert activation["catalog_epoch"] == 7
    assert activation["projection_version"] == CATALOG_PROJECTION_VERSION
    assert activation["_version"] == old["state_version"] + 1


def test_projection_supersession_defaults_to_refusal_and_requires_exact_ack():
    io = _IO(activation=[_active_v1()])
    with pytest.raises(CatalogActivationError, match="different activation"):
        CatalogFrozenEpochActivator(io, _config()).run()
    assert io.inserts == []

    with pytest.raises(CatalogActivationError, match="acknowledgement missing"):
        _config(
            allow_projection_supersession=True,
            supersession_acknowledgement="wrong",
        ).validated()
    with pytest.raises(CatalogActivationError, match="requires its explicit flag"):
        _config(
            supersession_acknowledgement=CATALOG_ACTIVATION_SUPERSESSION_ACK,
        ).validated()


@pytest.mark.parametrize(
    "activation",
    (
        _active_v1(catalog_epoch=7),
        _active_v1(projection_version=2),
        _active_v1(handoff_start=SINCE - timedelta(hours=1)),
        _active_v1(handoff_end=UNTIL + timedelta(hours=1)),
        _active_v1(writer_watermark=UNTIL - timedelta(hours=1)),
        _active_v1(status="disabled"),
    ),
)
def test_projection_supersession_rejects_any_nonexact_v1_snapshot(activation):
    io = _IO(activation=[activation])

    with pytest.raises(CatalogActivationError, match="not an exact v1 snapshot"):
        CatalogFrozenEpochActivator(io, _supersession_config()).run()

    assert io.inserts == []


@pytest.mark.parametrize(
    ("activation", "match"),
    (
        (_active_v1(latest_state_variants=2), "latest state conflicts"),
        (_active_v1(state_version=0), "state version is missing"),
        (
            {
                key: value
                for key, value in _active_v1().items()
                if key != "projection_version"
            },
            "projection_version must be a non-negative integer",
        ),
    ),
)
def test_projection_supersession_fails_closed_on_ambiguous_or_incomplete_state(
    activation,
    match,
):
    io = _IO(activation=[activation])

    with pytest.raises(CatalogActivationError, match=match):
        CatalogFrozenEpochActivator(io, _supersession_config()).run()

    assert io.inserts == []


def test_checkpoint_projection_version_is_required_and_must_be_current():
    missing = _checkpoint(SINCE)
    missing.pop("projection_version")
    for projection in (missing, _checkpoint(SINCE, projection_version=1)):
        io = _IO(checkpoints=[projection, _checkpoint(SINCE + timedelta(hours=1))])
        with pytest.raises(CatalogActivationError, match="projection"):
            CatalogFrozenEpochActivator(io, _config()).run()
        assert io.inserts == []
