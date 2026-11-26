"""Move immutable source objects off the legacy public upload bucket."""

import hashlib
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from minio.error import S3Error

from simulate.services.harness_storage import ensure_private_bucket, private_bucket
from tfc.utils.storage_client import get_storage_client

PREFIXES = ("environment-source-snapshots/", "harness-design-documents/")


@contextmanager
def _object(client, bucket, key):
    stream = client.get_object(bucket, key)
    try:
        yield stream
    finally:
        stream.close()
        stream.release_conn()


def _sha256(client, bucket, key):
    with _object(client, bucket, key) as stream:
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def _copy_verified(client, source, destination, key, remove):
    old = client.stat_object(source, key)
    try:
        client.stat_object(destination, key)
    except S3Error as exc:
        if exc.code != "NoSuchKey":
            raise
        with _object(client, source, key) as stream:
            client.put_object(
                destination,
                key,
                stream,
                old.size,
                content_type=old.content_type or "application/octet-stream",
            )
    if _sha256(client, source, key) != _sha256(client, destination, key):
        raise CommandError(
            "Private object verification failed. The legacy object was retained."
        )
    if remove:
        if client.stat_object(source, key).etag != old.etag:
            raise CommandError("The legacy object changed during migration.")
        client.remove_object(source, key)


class Command(BaseCommand):
    help = "Inventory legacy source objects. --copy verifies a private copy. Add --remove-verified-legacy after rolling out private reads everywhere."

    def add_arguments(self, parser):
        parser.add_argument("--copy", action="store_true")
        parser.add_argument("--remove-verified-legacy", action="store_true")

    def handle(self, *args, **options):
        if options["remove_verified_legacy"] and not options["copy"]:
            raise CommandError("--remove-verified-legacy requires --copy")
        client = get_storage_client()
        source = settings.UPLOAD_BUCKET_NAME
        destination = ensure_private_bucket() if options["copy"] else private_bucket()
        count = 0
        for prefix in PREFIXES:
            for item in client.list_objects(source, prefix=prefix, recursive=True):
                if options["copy"]:
                    _copy_verified(
                        client,
                        source,
                        destination,
                        item.object_name,
                        options["remove_verified_legacy"],
                    )
                count += 1
        self.stdout.write(f"Source objects processed: {count}")
