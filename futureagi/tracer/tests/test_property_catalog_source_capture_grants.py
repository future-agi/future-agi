"""Offline behavior tests for the exact optional source-capture grant bundle."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    PropertyCatalogDevRuntimeError,
    _validate_production_writer_grants,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PROPERTY_CATALOG_TABLES,
)

_CATALOG = tuple(
    f"GRANT SELECT, INSERT ON property_catalog.{table} TO catalog_writer"
    for table in sorted(PROPERTY_CATALOG_TABLES)
)
_ACCESS = (
    "SELECT",
    "INSERT",
    "CREATE TABLE",
    "ALTER DELETE",
    "ALTER TTL",
    "DROP TABLE",
)
_SOURCE = "GRANT SELECT ON default.spans TO catalog_writer"
_CAPTURE = (
    "GRANT "
    + ", ".join(_ACCESS)
    + " ON property_catalog_source_capture.* TO catalog_writer"
)


def _validate(grants, **overrides):
    return _validate_production_writer_grants(
        grants, database="property_catalog", user="catalog_writer", **overrides
    )


def test_existing_exact_catalog_contract_stays_valid_without_capture():
    _validate(_CATALOG)
    _validate(_CATALOG, source_database="default")


def test_complete_capture_bundle_is_accepted_in_either_grant_order():
    for grants in ((*_CATALOG, _SOURCE, _CAPTURE), (_CAPTURE, _SOURCE, *_CATALOG)):
        assert _validate(grants, source_database="default") is None


def test_split_reordered_and_quoted_exact_capture_grants_are_accepted():
    grants = tuple(
        f"GRANT {access} ON `property_catalog_source_capture`.* TO `catalog_writer`"
        for access in reversed(_ACCESS)
    )
    _validate(
        (*_CATALOG, "GRANT SELECT ON `default`.`spans` TO `catalog_writer`", *grants),
        source_database="default",
    )


def test_existing_validator_call_cannot_enable_capture_by_observed_grants():
    with pytest.raises(PropertyCatalogDevRuntimeError):
        _validate((*_CATALOG, _SOURCE, _CAPTURE))


@pytest.mark.parametrize("missing", _ACCESS)
def test_every_snapshot_privilege_is_required(missing):
    access = ", ".join(value for value in _ACCESS if value != missing)
    with pytest.raises(PropertyCatalogDevRuntimeError, match="incomplete"):
        _validate(
            (
                *_CATALOG,
                _SOURCE,
                f"GRANT {access} ON property_catalog_source_capture.* TO catalog_writer",
            ),
            source_database="default",
        )


@pytest.mark.parametrize("extra", [(_SOURCE,), (_CAPTURE,)])
def test_partial_source_or_snapshot_bundle_is_rejected(extra):
    with pytest.raises(PropertyCatalogDevRuntimeError, match="incomplete"):
        _validate((*_CATALOG, *extra), source_database="default")


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT INSERT ON default.spans TO catalog_writer",
        "GRANT ALTER DELETE ON default.spans TO catalog_writer",
        "GRANT ALTER TTL ON default.spans TO catalog_writer",
        "GRANT DROP TABLE ON default.spans TO catalog_writer",
        "GRANT SELECT ON default.* TO catalog_writer",
        "GRANT SELECT ON other_source.spans TO catalog_writer",
        "GRANT CREATE TABLE ON property_catalog.* TO catalog_writer",
        "GRANT CREATE TABLE ON property_catalog_other_source_capture.* TO catalog_writer",
        "GRANT CREATE TABLE ON property_catalog_source_capture_other.* TO catalog_writer",
        "GRANT CREATE TABLE ON *.* TO catalog_writer",
        "GRANT ALTER ON property_catalog_source_capture.* TO catalog_writer",
        "GRANT CREATE DATABASE ON property_catalog_source_capture.* TO catalog_writer",
        "GRANT SELECT ON default.spans TO somebody_else",
        "GRANT SELECT ON default.spans TO catalog_writer WITH GRANT OPTION",
        "GRANT source_capture_role TO catalog_writer",
        "GRANT SELECT ON `default.spans TO catalog_writer",
        "GRANT SELECT ON default.`spans TO catalog_writer",
        "GRANT SELECT ON default.spans TO `catalog_writer",
        "GRANT SELECT, SELECT ON default.spans TO catalog_writer",
        _SOURCE,
        _CAPTURE,
    ],
)
def test_extra_broad_wrong_source_delegated_duplicate_or_malformed_grants_fail(grant):
    with pytest.raises(PropertyCatalogDevRuntimeError):
        _validate((*_CATALOG, _SOURCE, _CAPTURE, grant), source_database="default")


@pytest.mark.parametrize(
    "source",
    ["", "default.spans", "property_catalog", "property_catalog_source_capture"],
)
def test_invalid_or_overlapping_source_namespace_fails(source):
    with pytest.raises(PropertyCatalogDevRuntimeError):
        _validate(_CATALOG, source_database=source)


def test_capture_does_not_replace_any_missing_catalog_grant():
    with pytest.raises(PropertyCatalogDevRuntimeError):
        _validate((*_CATALOG[:-1], _SOURCE, _CAPTURE), source_database="default")


def test_production_provenance_passes_exact_configured_source_to_grant_check(
    monkeypatch, tmp_path
):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime
    from tracer.tests.test_property_catalog_dev_rollout import (
        ATTESTED_AT,
        _provenance_observation,
        _unit_runtime_config,
    )

    config = _unit_runtime_config(str(tmp_path))
    config = replace(
        config,
        catalog=replace(config.catalog, database="property_catalog"),
        deployment="prod",
    )
    observation = replace(_provenance_observation(), writer_clickhouse_grants=_CATALOG)
    seen = []
    monkeypatch.setattr(
        dev_runtime,
        "_validate_production_writer_grants",
        lambda grants, **kw: seen.append(kw),
    )
    dev_runtime._validate_dev_provenance(
        config=config, observation=observation, attested_at=ATTESTED_AT
    )
    assert seen == [
        {
            "database": config.catalog.database,
            "user": config.catalog.user,
            "source_database": config.source.database,
        }
    ]
