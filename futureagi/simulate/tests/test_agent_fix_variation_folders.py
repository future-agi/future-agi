"""A fix's variations are told apart by the folder the builder wrote each one in."""

import json

import pytest

from simulate.services.harness_versioning import archives
from simulate.tests.test_agent_fix_variations import (  # noqa: F401 - fixtures
    PARENT,
    _builder_save,
    _land,
    _named,
    _private,
    builder,
    environment,
    fix_run,
    floor,
    source_run,
    storage,
    temporal,
)

pytestmark = pytest.mark.django_db


def test_two_folders_carrying_one_variation_s_key_are_two_variations(
    environment, fix_run, storage
):
    # The builder copied the first variation's document into the second's folder and
    # changed only what it asks.
    files = archives.read_archive(_builder_save(environment))
    documents = json.loads(files["scenarios.json"])
    for one in documents:
        if one.get("folder") == f"scenarios/{PARENT}-var2":
            one["scenario_key"] = one["name"] = f"{PARENT}-var1"
    files["scenarios.json"] = json.dumps(documents).encode()

    _land(fix_run, storage, archives.pack_archive(files))

    version = _private(fix_run)
    assert set(version.documents["variations"]) == {
        _named(fix_run, n) for n in (1, 2, 3)
    }
    stored = archives.read_archive(archives.get_archive(version.archive_object_key))
    assert json.loads(stored[f"scenarios/{_named(fix_run, 2)}/scenario.json"])[
        "instruction"
    ] == (f"another caller, {PARENT}-var2")
