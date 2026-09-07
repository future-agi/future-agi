from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
    InstallationIdentity,
    InstallationIdentityError,
    identity_path,
    load_identity,
    load_or_initialize_identity,
)


def identity() -> InstallationIdentity:
    return InstallationIdentity(
        environment="development",
        target_database="property_catalog_dev_oss",
        candidate_topic="futureagi.oss.property-catalog.candidates.v1",
        ordered_topic="futureagi.oss.property-catalog.ordered.v1",
        catalog_epoch=1,
        projection_version=1,
        producer_stream_id="9e43cac3-65f0-50b1-991d-4e582aa2cc84",
    )


def signed(document: dict) -> bytes:
    document.pop("identity_sha256", None)
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    document["identity_sha256"] = hashlib.sha256(raw).hexdigest()
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def test_identity_roundtrip_and_destination_binding():
    value = identity()
    assert InstallationIdentity.decode(value.encode()) == value
    value.require_destination(
        environment=value.environment,
        target_database=value.target_database,
        candidate_topic=value.candidate_topic,
        ordered_topic=value.ordered_topic,
    )
    with pytest.raises(InstallationIdentityError, match="target_database"):
        value.require_destination(
            environment=value.environment,
            target_database="property_catalog_dev_green",
            candidate_topic=value.candidate_topic,
            ordered_topic=value.ordered_topic,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"version": True},
        {"projection_format": "unknown.v2"},
        {"catalog_epoch": 0},
        {"catalog_epoch": True},
        {"projection_version": 65536},
        {"producer_stream_id": "00000000-0000-0000-0000-000000000000"},
        {"unexpected": 1},
        {"target_database": "default"},
        {"ordered_topic": ".."},
        {"environment": "dev"},
    ],
)
def test_unknown_or_invalid_identity_fails_even_with_matching_digest(changes):
    document = {**json.loads(identity().encode()), **changes}
    with pytest.raises(InstallationIdentityError):
        InstallationIdentity.decode(signed(document))


def test_missing_field_cannot_silently_use_a_default():
    document = json.loads(identity().encode())
    del document["projection_format"]
    with pytest.raises(InstallationIdentityError):
        InstallationIdentity.decode(signed(document))


def test_digest_and_canonical_json_are_mandatory():
    raw = identity().encode()
    for changed in (
        raw.replace(b'"catalog_epoch":1', b'"catalog_epoch":2'),
        raw[:-1],
        b" " + raw,
        raw + b"\n",
        raw * 30,
    ):
        with pytest.raises(InstallationIdentityError):
            InstallationIdentity.decode(changed)


def test_initializer_runs_once_and_restart_reuses_identity(tmp_path):
    path = identity_path(str(tmp_path / "revision-fence-v2.json"))
    assert path.name == IDENTITY_FILENAME
    calls = []

    def initialize():
        calls.append(True)
        return identity()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = tuple(
            pool.map(
                lambda _: load_or_initialize_identity(path, initialize=initialize),
                range(12),
            )
        )
    assert results == (identity(),) * 12
    assert len(calls) == 1
    assert load_identity(path) == identity()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not tuple(tmp_path.glob(".catalog-identity-*"))


def test_failed_initialization_does_not_publish_metadata(tmp_path):
    path = tmp_path / IDENTITY_FILENAME

    def initialize():
        raise InstallationIdentityError("catalog is not empty")

    with pytest.raises(InstallationIdentityError, match="not empty"):
        load_or_initialize_identity(path, initialize=initialize)
    assert not path.exists()


def test_corrupt_identity_is_never_replaced(tmp_path):
    path = tmp_path / IDENTITY_FILENAME
    path.write_bytes(b"corrupt\n")
    with pytest.raises(InstallationIdentityError):
        load_or_initialize_identity(path, initialize=identity)
    assert path.read_bytes() == b"corrupt\n"


def test_symlink_is_not_followed_or_replaced(tmp_path):
    target = tmp_path / "other.json"
    target.write_bytes(identity().encode())
    path = tmp_path / IDENTITY_FILENAME
    path.symlink_to(target)
    with pytest.raises(InstallationIdentityError):
        load_or_initialize_identity(path, initialize=identity)
    assert path.is_symlink()
    assert target.read_bytes() == identity().encode()


def test_adopted_numbers_are_not_rewritten_on_restart(tmp_path):
    path = tmp_path / IDENTITY_FILENAME
    adopted = replace(identity(), catalog_epoch=7, projection_version=3)
    assert load_or_initialize_identity(path, initialize=lambda: adopted) == adopted
    assert load_or_initialize_identity(path, initialize=identity) == adopted


def test_cross_language_fixture_is_byte_identical():
    root = Path(__file__).resolve().parents[3]
    fixture = (
        root / "fi-collector/pkg/propertycatalog/testdata/runtime_identity_v1.json"
    )
    decoded = InstallationIdentity.decode(fixture.read_bytes())
    assert decoded.encode() == fixture.read_bytes()
