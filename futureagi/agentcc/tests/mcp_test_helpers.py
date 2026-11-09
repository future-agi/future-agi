"""Shared, offline-validating helpers for MCP contract goldens."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

EXAMPLES_DIR = (
    Path(__file__).resolve().parents[3] / "api_contracts" / "gateway" / "examples"
)


def golden(name):
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(
            f"{path.name} is M2's golden (W2b) and is not in this lane's tree; "
            "the W2b gate runs this test after M2 merges"
        )
    value = json.loads(path.read_text())
    schema_path = EXAMPLES_DIR / f"{name}.schema.json"
    if schema_path.exists():
        golden_validator(json.loads(schema_path.read_text())).validate(value)
    return value


def golden_validator(schema):
    """Resolve local admin-contract references without any network fetch."""
    admin = json.loads((EXAMPLES_DIR.parent / "agentcc-admin.schema.json").read_text())
    registry = Registry().with_resource(admin["$id"], Resource.from_contents(admin))
    return Draft202012Validator(schema, registry=registry)
