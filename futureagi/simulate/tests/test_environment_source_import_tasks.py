from types import SimpleNamespace

import pytest

from simulate.tasks import environment_source_imports
from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES

pytestmark = pytest.mark.django_db


def test_expiry_removes_only_staging_objects(monkeypatch):
    removed = []
    monkeypatch.setattr(
        environment_source_imports,
        "get_storage_client",
        lambda: SimpleNamespace(
            remove_object=lambda bucket, key: removed.append((bucket, key))
        ),
    )
    key = f"environment-source-imports/org/env/{'a' * 64}.json"

    expire = environment_source_imports.expire_environment_source_import
    assert expire(key) == {"object_key": key}
    assert expire(key) == {"object_key": key}
    assert removed == [
        (environment_source_imports.UPLOAD_BUCKET_NAME, key),
        (environment_source_imports.UPLOAD_BUCKET_NAME, key),
    ]


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
    client = SimpleNamespace(
        remove_object=lambda bucket, object_key: pytest.fail("deleted an unsafe key")
    )
    monkeypatch.setattr(
        environment_source_imports, "get_storage_client", lambda: client
    )

    with pytest.raises(ValueError, match="invalid environment source import key"):
        environment_source_imports.expire_environment_source_import(key)


def test_expiry_activity_is_loaded_by_temporal_workers():
    assert "simulate.tasks.environment_source_imports" in TEMPORAL_ACTIVITY_MODULES
