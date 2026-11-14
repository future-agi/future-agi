"""Private storage and short-lived, authenticated annotation export downloads."""

import asyncio
import json
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from minio.error import S3Error

DOWNLOAD_MAX_AGE = 300
DOWNLOAD_SALT = "annotation-export-download"


class ExportFile:
    """FileResponse closes the storage response even on a cancelled download."""

    def __init__(self, response):
        self.response = response

    def read(self, size):
        return self.response.read(size)

    async def chunks(self):
        # Django would otherwise collect a synchronous FileResponse in memory
        # when served by ASGI, defeating background exports for large queues.
        while chunk := await asyncio.to_thread(self.read, 64 * 1024):
            yield chunk

    def close(self):
        try:
            self.response.close()
        finally:
            self.response.release_conn()


def export_bucket_name():
    # Separate from upload buckets: ensure_bucket intentionally makes those public.
    return getattr(settings, "ANNOTATION_EXPORT_BUCKET_NAME", "") or (
        f"{settings.UPLOAD_BUCKET_NAME[:44]}-annotation-exports"
    )


def _require_gcs_private_bucket(bucket):
    # HMAC's XML API cannot establish IAM/default-ACL privacy. Fail closed unless
    # the JSON API confirms both controls before any export bytes are written.
    # Operators provision these controls and read-only ADC credentials; this
    # path never changes a bucket's policy or falls back to a public upload.
    from google.cloud import storage

    configured = storage.Client().get_bucket(bucket, timeout=15, retry=None)
    iam = configured.iam_configuration
    if (
        iam.public_access_prevention != "enforced"
        or iam.uniform_bucket_level_access_enabled is not True
    ):
        raise ValueError(
            "GCS export bucket requires enforced public access prevention "
            "and uniform bucket-level access."
        )


def _require_s3_private_bucket(bucket):
    import os

    import boto3
    from botocore.config import Config

    from tfc.utils.storage_client import s3_endpoint

    host, secure = s3_endpoint()
    client = boto3.client(
        "s3",
        endpoint_url=f"{'https' if secure else 'http'}://{host}",
        aws_access_key_id=os.getenv("S3_ACCESS_KEY")
        or os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        region_name=os.getenv("S3_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1",
        config=Config(connect_timeout=15, read_timeout=15, retries={"max_attempts": 0}),
    )
    try:
        controls = client.get_public_access_block(Bucket=bucket)[
            "PublicAccessBlockConfiguration"
        ]
        if not all(
            controls.get(name) is True
            for name in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise ValueError("S3 export bucket requires all public access blocks.")
    finally:
        client.close()


def ensure_export_bucket(client):
    """Create without an anonymous policy; refuse a publicly configured bucket.

    Never rewrite an operator's policy or change the upload bucket's policy.
    An unreadable policy is not proof of privacy and must fail the upload.
    """
    from tfc.utils.storage_client import STORAGE_BACKEND

    bucket = export_bucket_name()
    if bucket == settings.UPLOAD_BUCKET_NAME:
        raise ValueError("Annotation exports require a separate private bucket.")
    if STORAGE_BACKEND == "gcs":
        # Require an explicit bucket with verifiable provider privacy controls.
        if not getattr(settings, "ANNOTATION_EXPORT_BUCKET_NAME", None):
            raise ValueError(
                "GCS requires an explicitly configured private export bucket."
            )
        if not client.bucket_exists(bucket):
            raise ValueError("The private GCS export bucket must be provisioned first.")
        _require_gcs_private_bucket(bucket)
        return bucket
    if not client.bucket_exists(bucket):
        try:
            client.make_bucket(bucket)
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
    if STORAGE_BACKEND == "s3":
        # A missing bucket policy alone says nothing about existing public ACLs.
        _require_s3_private_bucket(bucket)
    try:
        policy = json.loads(client.get_bucket_policy(bucket))
    except S3Error as exc:
        if exc.code == "NoSuchBucketPolicy":
            return bucket
        raise
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for statement in statements:
        # Only positively identified, named principals are acceptable. A
        # wildcard grant with conditions is still an anonymous grant.
        if statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal")
        values = principal.values() if isinstance(principal, dict) else [principal]
        if "NotPrincipal" in statement or any(
            value is None or "*" in str(value) for value in values
        ):
            raise ValueError("Annotation export bucket permits anonymous access.")
    return bucket


def verify_private_object(bucket, key):
    """Fail closed if the uploaded object is anonymously readable or untestable.

    Defense in depth only: ensure_export_bucket establishes privacy before
    upload. Never expose this URL to a caller; it is a worker-only probe.
    """
    from tfc.utils.storage_client import (
        STORAGE_BACKEND,
        get_object_url,
        s3_endpoint,
    )

    if STORAGE_BACKEND in {"minio", "s3"}:
        host, secure = s3_endpoint()
        url = f"{'https' if secure else 'http'}://{host}/{bucket}/{key}"
    else:
        url = get_object_url(bucket, key)
    response = requests.get(url, stream=True, timeout=15, allow_redirects=True)
    try:
        if response.status_code != 403:
            raise ValueError(
                f"Export storage anonymous read verification failed: HTTP {response.status_code}"
            )
    finally:
        response.close()


def can_download_export(request, queue):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    if (
        organization is None
        or workspace is None
        or organization.pk != queue.organization_id
        or workspace.organization_id != queue.organization_id
        or (queue.workspace_id is not None and workspace.pk != queue.workspace_id)
        or (queue.workspace_id is None and not workspace.is_default)
        or not workspace.is_active
        or workspace.deleted
    ):
        return False
    # Query current membership rather than trusting token claims or cached roles.
    membership = OrganizationMembership.no_workspace_objects.filter(
        user=request.user, organization=organization, is_active=True, deleted=False
    ).first()
    if membership is None or membership.level_or_legacy < Level.VIEWER:
        return False
    if membership.level_or_legacy >= Level.ADMIN:
        return True
    workspace_membership = WorkspaceMembership.no_workspace_objects.filter(
        user=request.user, workspace=workspace, is_active=True, deleted=False
    ).first()
    return (
        workspace_membership is not None
        and workspace_membership.level_or_legacy >= Level.WORKSPACE_VIEWER
    )


def download_path(job):
    return reverse(
        "annotation-queues-export-download",
        kwargs={"pk": job.queue_id, "job_id": job.id},
    )


def download_url(job):
    token = signing.dumps(
        {"job": str(job.pk), "queue": str(job.queue_id)}, salt=DOWNLOAD_SALT
    )
    return f"{download_path(job)}?{urlencode({'token': token})}"


def valid_download_token(job, token):
    try:
        value = signing.loads(token, salt=DOWNLOAD_SALT, max_age=DOWNLOAD_MAX_AGE)
    except (signing.BadSignature, TypeError):
        return False
    return value == {"job": str(job.pk), "queue": str(job.queue_id)}


def export_is_available(job):
    from model_hub.tasks.annotation_export import EXPORT_RETENTION_DAYS

    return (
        job.status == "succeeded"
        and bool(job.storage_key)
        and job.finished_at is not None
        and job.finished_at > timezone.now() - timedelta(days=EXPORT_RETENTION_DAYS)
    )
