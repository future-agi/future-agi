"""Storage for customer harness inputs and generated workspaces."""

import io
import json

from django.conf import settings
from minio.error import S3Error

from tfc.utils.storage_client import _is_anonymous, get_storage_client

_MISSING = {"NoSuchBucket", "NoSuchKey"}
_VERIFIED_MINIO_BUCKETS = set()


def private_bucket() -> str:
    bucket = settings.HARNESS_PRIVATE_BUCKET
    if not bucket or bucket == settings.UPLOAD_BUCKET_NAME:
        raise RuntimeError("A separate private harness bucket is required.")
    return bucket


def put_private(key: str, body: bytes, content_type: str) -> None:
    bucket = private_bucket()
    client = get_storage_client()
    if settings.STORAGE_BACKEND != "gcs" and not client.bucket_exists(bucket):
        try:
            client.make_bucket(bucket)
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
    if settings.STORAGE_BACKEND == "minio" and bucket not in _VERIFIED_MINIO_BUCKETS:
        try:
            policy = json.loads(client.get_bucket_policy(bucket))
        except S3Error as exc:
            if exc.code != "NoSuchBucketPolicy":
                raise
            policy = {}
        statements = policy.get("Statement") or []
        if isinstance(statements, dict):
            statements = [statements]
        if any(
            item.get("Effect") == "Allow" and _is_anonymous(item.get("Principal"))
            for item in statements
        ):
            raise RuntimeError("The private harness bucket grants public access.")
        _VERIFIED_MINIO_BUCKETS.add(bucket)
    client.put_object(
        bucket, key, io.BytesIO(body), len(body), content_type=content_type
    )


def open_private(key: str, *, legacy_archive: bool = False):
    client = get_storage_client()
    try:
        return client.get_object(private_bucket(), key)
    except S3Error as exc:
        if not legacy_archive or exc.code not in _MISSING:
            raise
        return client.get_object(settings.UPLOAD_BUCKET_NAME, key)


def read_private(key: str, *, legacy_archive: bool = False) -> bytes:
    response = open_private(key, legacy_archive=legacy_archive)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def stat_customer_archive(key: str):
    client = get_storage_client()
    try:
        return client.stat_object(private_bucket(), key)
    except S3Error as exc:
        if exc.code not in _MISSING:
            raise
        return client.stat_object(settings.UPLOAD_BUCKET_NAME, key)


def remove_private(key: str) -> None:
    get_storage_client().remove_object(private_bucket(), key)
