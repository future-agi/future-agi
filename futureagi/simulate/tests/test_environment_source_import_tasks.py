from uuid import uuid4

import pytest

from simulate.tasks import environment_source_imports
from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES

pytestmark = pytest.mark.django_db


def test_expiry_removes_only_staging_objects(monkeypatch):
    removed = []
    monkeypatch.setattr(
        environment_source_imports,
        "remove_private",
        removed.append,
    )
    key = f"environment-source-imports/{uuid4()}/{uuid4()}/{'a' * 64}.json"

    expire = environment_source_imports.expire_environment_source_import._original_func
    assert expire(key) == {"expired": True}
    assert expire(key) == {"expired": True}
    assert removed == [key, key]


@pytest.mark.parametrize(
    "key",
    [
        "environment-source-snapshots/org/env/" + "a" * 64 + ".json",
        "environment-source-imports/org/env/not-a-digest.json",
        "environment-source-imports/org/env/" + "A" * 64 + ".json",
        "unrelated/object.json",
    ],
)
def test_expiry_rejects_keys_outside_its_staging_boundary(monkeypatch, key):
    monkeypatch.setattr(
        environment_source_imports,
        "remove_private",
        lambda _: pytest.fail("deleted an unsafe key"),
    )

    with pytest.raises(ValueError, match="invalid environment source import key"):
        environment_source_imports.expire_environment_source_import(key)


def test_expiry_activity_is_loaded_by_temporal_workers():
    assert "simulate.tasks.environment_source_imports" in TEMPORAL_ACTIVITY_MODULES
