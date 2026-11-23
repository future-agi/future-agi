import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management.base import CommandError
from minio.error import S3Error

from simulate.management.commands.migrate_harness_source_storage import _copy_verified
from simulate.services import harness_storage


def test_verified_copy_retains_conflicting_objects_and_can_resume():
    objects = {("public", "uploads/one"): b"data"}
    client = Mock()

    def stat(bucket, key):
        if (bucket, key) not in objects:
            raise S3Error(None, "NoSuchKey", "missing", key, "req", "host")
        data = objects[bucket, key]
        return SimpleNamespace(
            size=len(data),
            etag=hashlib.sha256(data).hexdigest(),
            content_type="text/plain",
        )

    def get(bucket, key):
        stream = io.BytesIO(objects[bucket, key])
        stream.release_conn = lambda: None
        return stream

    def put(bucket, key, stream, size, content_type):
        objects[bucket, key] = stream.read(size)

    client.stat_object.side_effect = stat
    client.get_object.side_effect = get
    client.put_object.side_effect = put
    client.remove_object.side_effect = lambda bucket, key: objects.pop((bucket, key))
    _copy_verified(client, "public", "private", "uploads/one", False)
    assert objects["private", "uploads/one"] == objects["public", "uploads/one"]
    objects["private", "uploads/one"] = b"mismatch"
    with pytest.raises(CommandError, match="retained"):
        _copy_verified(client, "public", "private", "uploads/one", True)
    assert ("public", "uploads/one") in objects
    objects["private", "uploads/one"] = b"data"
    _copy_verified(client, "public", "private", "uploads/one", True)
    assert objects == {("private", "uploads/one"): b"data"}


def test_forget_deletes_only_the_conversation_prefix_in_both_buckets(
    settings, monkeypatch
):
    settings.HARNESS_PRIVATE_BUCKET = "private"
    settings.UPLOAD_BUCKET_NAME = "public"
    prefix = "harness-conversations/org/chat/"
    keys = {
        (bucket, key)
        for bucket in ("private", "public")
        for key in (prefix + "data", "other/data")
    }
    client = Mock()
    client.list_objects.side_effect = lambda bucket, prefix, recursive: [
        SimpleNamespace(object_name=key)
        for store, key in keys
        if store == bucket and key.startswith(prefix)
    ]
    client.remove_object.side_effect = lambda bucket, key: keys.remove((bucket, key))
    monkeypatch.setattr(harness_storage, "get_storage_client", lambda: client)
    harness_storage.remove_customer_prefix(prefix)
    assert keys == {("private", "other/data"), ("public", "other/data")}
