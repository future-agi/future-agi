"""wire_reference.toml must describe exactly what the sender puts on the wire.

The telemetry page of the docs site promises operators an exact list of what
leaves their install, with example payloads; scripts/docs_site.py renders both
from wire_reference.toml. A field added to schema.py without a line there, an
example that the receiver would reject, or an opt-out payload that differs
from the one described would quietly break that promise.

The FUTURE_AGI_TELEMETRY_* settings are rows of deploy/env-reference.toml;
that part is skipped where the repository around futureagi/ is not present
(for example, in a backend-only image).
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
import uuid
from pathlib import Path

import pytest

from tfc.deployment_telemetry import config
from tfc.deployment_telemetry.payloads import (
    build_minimal_registration_payload,
    serialized_size,
)
from tfc.deployment_telemetry.schema import (
    COUNT_FIELDS,
    HEARTBEAT_FIELDS,
    MAX_PAYLOAD_BYTES,
    REGISTRATION_FIELDS,
    USER_FIELDS,
    derive_domain,
    expected_total_evaluations,
)

_WIRE = Path(__file__).resolve().parents[1] / "wire_reference.toml"
_ENV_REFERENCE = Path(__file__).resolve().parents[4] / "deploy" / "env-reference.toml"


@pytest.fixture(scope="module")
def wire() -> dict:
    with _WIRE.open("rb") as handle:
        return tomllib.load(handle)


@pytest.fixture(scope="module")
def telemetry_rows() -> list[dict]:
    if not _ENV_REFERENCE.is_file():
        pytest.skip(f"{_ENV_REFERENCE} not present in this checkout")
    with _ENV_REFERENCE.open("rb") as handle:
        reference = tomllib.load(handle)
    return [
        row
        for part in reference["part"]
        for section in part.get("section", [])
        for row in section.get("row", [])
        if row.get("tag") == "telemetry"
    ]


def _names(entries: list[dict]) -> list[str]:
    return [entry["name"] for entry in entries]


def _registration_example(wire: dict) -> dict:
    example = {}
    for field in wire["registration"]["field"]:
        if field["name"] == "users":
            example["users"] = [{f["name"]: f["example"] for f in wire["user_field"]}]
        else:
            example[field["name"]] = field["example"]
    return example


def _heartbeat_example(wire: dict) -> dict:
    example = {f["name"]: f["example"] for f in wire["heartbeat"]["field"]}
    example.update({c["name"]: c["example"] for c in wire["heartbeat"]["count"]})
    return example


def test_every_wire_field_is_described_once(wire):
    registration = _names(wire["registration"]["field"])
    users = _names(wire["user_field"])
    heartbeat = _names(wire["heartbeat"]["field"]) + _names(wire["heartbeat"]["count"])
    for names in (registration, users, heartbeat):
        assert len(names) == len(set(names)), f"described twice: {names}"
    assert set(registration) == REGISTRATION_FIELDS
    assert set(users) == USER_FIELDS
    assert set(heartbeat) == HEARTBEAT_FIELDS
    # The page lists the counts in the order the payload carries them.
    assert tuple(_names(wire["heartbeat"]["count"])) == COUNT_FIELDS


def test_every_field_has_a_meaning(wire):
    entries = (
        wire["registration"]["field"] + wire["user_field"] + wire["heartbeat"]["field"]
    )
    for entry in entries:
        assert entry.get("meaning", "").strip(), f"{entry['name']} has no meaning"
    for entry in wire["heartbeat"]["count"]:
        assert entry.get("counts", "").strip(), f"{entry['name']} says nothing"


def test_the_example_registration_is_a_valid_payload(wire):
    example = _registration_example(wire)
    assert set(example) == REGISTRATION_FIELDS
    assert example["telemetry_disabled"] is False
    uuid.UUID(example["instance_id"])
    for user in example["users"]:
        assert set(user) == USER_FIELDS
        assert user["domain"] == derive_domain(user["email"])
    assert serialized_size(example) <= MAX_PAYLOAD_BYTES


def test_the_example_heartbeat_is_a_valid_payload(wire):
    example = _heartbeat_example(wire)
    assert set(example) == HEARTBEAT_FIELDS
    assert example["instance_id"] == _registration_example(wire)["instance_id"]
    assert example["window_start"] < example["window_end"]
    assert example["total_evaluations_count"] == expected_total_evaluations(example)
    for name in COUNT_FIELDS:
        assert isinstance(example[name], int) and example[name] >= 0, name
    assert serialized_size(example) <= MAX_PAYLOAD_BYTES


def test_the_opt_out_fields_are_what_the_opt_out_registration_sends(wire):
    payload = build_minimal_registration_payload(uuid.uuid4())
    assert wire["opt_out"]["fields"] == list(payload)
    assert payload["telemetry_disabled"] is True


def test_every_telemetry_setting_has_a_reference_row(telemetry_rows):
    source = Path(config.__file__).read_text(encoding="utf-8")
    settings = set(re.findall(r'"(FUTURE_AGI_TELEMETRY_[A-Z_]+)"', source))
    assert settings, "no FUTURE_AGI_TELEMETRY_* settings found in config.py"
    documented = {key for row in telemetry_rows for key in row["keys"]}
    missing = sorted(settings - documented)
    assert not missing, (
        f"give these a row tagged telemetry in deploy/env-reference.toml: {missing}"
    )


def test_the_opt_out_is_documented(telemetry_rows):
    (row,) = [
        r for r in telemetry_rows if r["keys"] == ["FUTURE_AGI_TELEMETRY_DISABLED"]
    ]
    assert row["default"] == "`false`"
    assert row["text"].startswith("`true` opts out")
    # The values config.telemetry_is_disabled() accepts.
    for value in ("`1`", "`yes`", "`on`"):
        assert value in row["text"], value


def test_the_telemetry_blocks_render_as_mdx(wire):
    script = _ENV_REFERENCE.parents[1] / "scripts" / "docs_site.py"
    if not script.is_file():
        pytest.skip(f"{script} not present in this checkout")
    spec = importlib.util.spec_from_file_location("docs_site", script)
    docs_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_site)
    problems = docs_site.data_problems(docs_site.wire_texts(wire))
    assert not problems, problems
    registration = docs_site.render_telemetry_registration(wire)
    heartbeat = docs_site.render_telemetry_heartbeat(wire)
    for name in REGISTRATION_FIELDS | HEARTBEAT_FIELDS:
        assert f"`{name}`" in registration + heartbeat, name
