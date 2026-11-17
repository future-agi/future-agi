"""Empty and remove the Hub lane's MinIO bucket (dev-down.sh).

The Hub provisions its bucket with versioning and object lock (omega_hub/services/storage_provision.py),
so a delete by name only adds a delete marker and the bucket never empties. This removes every version
and delete marker by id, bypassing the GOVERNANCE retention bundles carry (the dev MinIO's root
credential may; the Hub's own credential holds no s3:BypassGovernanceRetention), then the bucket.

    python teardown_bucket.py [bucket]      exit 0: removed, or nothing to remove; exit 1: it is still there

A MinIO that is not running is reported and is not an error: there is no bucket to reach.
"""

import sys

ENDPOINT = "localhost:39005"


def empty_and_remove(client, bucket: str, delete_object=None) -> tuple[int, list[str]]:
    """Remove every version of every object in ``bucket`` and then the bucket. Returns the number of
    versions and markers it asked to delete, and every error (empty when the bucket is gone)."""
    if delete_object is None:
        from minio.deleteobjects import DeleteObject as delete_object
    if not client.bucket_exists(bucket):
        return 0, []
    targets = [delete_object(item.object_name, item.version_id)
               for item in client.list_objects(bucket, recursive=True, include_version=True)]
    errors = [f"{error.name} (version {error.version_id}): {error.code} {error.message}"
              for error in client.remove_objects(bucket, targets, bypass_governance_mode=True)]
    if not errors:
        try:
            client.remove_bucket(bucket)
        except Exception as exc:  # the bucket's own error, reported with its class
            errors.append(f"remove_bucket: {exc.__class__.__name__}: {exc}")
    return len(targets), errors


def main(argv: list[str]) -> int:
    from minio import Minio

    bucket = argv[1] if len(argv) > 1 else "omega-hub-lane"
    client = Minio(ENDPOINT, access_key="minioadmin", secret_key="minioadmin", secure=False)
    try:
        client.list_buckets()
    except Exception as exc:
        print(f"MinIO on {ENDPOINT} is not reachable ({exc.__class__.__name__}); bucket {bucket} not touched")
        return 0
    count, errors = empty_and_remove(client, bucket)
    if errors:
        print(f"bucket {bucket} is still there: {len(errors)} error(s)", file=sys.stderr)
        for line in errors[:20]:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"removed bucket {bucket} ({count} versions and delete markers)" if count else f"no bucket {bucket} left")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
