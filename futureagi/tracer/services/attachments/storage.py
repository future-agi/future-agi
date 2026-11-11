"""Dedicated, private, version-addressed storage. No legacy storage helpers.

Only the native S3 backend is implemented here. MinIO/GCS interoperability must
remain disabled until their privacy/capability checks are independently qualified.
The deployment owns bucket provisioning; the application never creates a bucket
or changes policy, ACL, CORS, lifecycle or public-access settings.
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from botocore.exceptions import ClientError

from tracer.services.attachments.contract import AttachmentError


@dataclass(frozen=True)
class ObjectIdentity:
    key: str
    version: str


class PrivateMediaStorage:
    """Two dedicated clients, with no presign or public URL interface."""

    direct_upload_enabled = False

    def __init__(
        self, *, quarantine_client, serving_client, quarantine_bucket, serving_bucket
    ):
        if (
            not quarantine_bucket
            or not serving_bucket
            or quarantine_bucket == serving_bucket
        ):
            raise AttachmentError("storage_unverified", 503)
        self.quarantine = quarantine_client
        self.serving = serving_client
        self.quarantine_bucket = quarantine_bucket
        self.serving_bucket = serving_bucket

    def verify_readiness(self):
        try:
            for client, bucket in (
                (self.quarantine, self.quarantine_bucket),
                (self.serving, self.serving_bucket),
            ):
                self._verify_bucket(client, bucket)
        except Exception:
            # Storage exceptions may contain endpoint/key/signature details.
            raise AttachmentError("storage_unverified", 503) from None

    @staticmethod
    def _verify_bucket(client, bucket):
        block = client.get_public_access_block(Bucket=bucket)[
            "PublicAccessBlockConfiguration"
        ]
        if not all(
            block.get(flag) is True
            for flag in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise ValueError("public access")
        if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
            raise ValueError("versioning")
        ownership = client.get_bucket_ownership_controls(Bucket=bucket)[
            "OwnershipControls"
        ]["Rules"]
        if ownership != [{"ObjectOwnership": "BucketOwnerEnforced"}]:
            raise ValueError("ACL enabled")
        acl = client.get_bucket_acl(Bucket=bucket)
        owner = acl["Owner"]["ID"]
        if any(g["Grantee"].get("ID") != owner for g in acl["Grants"]):
            raise ValueError("foreign ACL")
        encryption = client.get_bucket_encryption(Bucket=bucket)[
            "ServerSideEncryptionConfiguration"
        ]["Rules"]
        if not encryption or any(
            r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
            not in {"AES256", "aws:kms"}
            for r in encryption
        ):
            raise ValueError("encryption")
        # Any CORS is unnecessary for authenticated gateway-only storage.
        try:
            cors = client.get_bucket_cors(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchCORSConfiguration":
                raise
        else:
            if cors.get("CORSRules"):
                raise ValueError("storage CORS")
        try:
            policy = json.loads(client.get_bucket_policy(Bucket=bucket)["Policy"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchBucketPolicy":
                raise
        else:
            # Conservative: even conditional public Allows are not accepted.
            if any(s.get("Effect") == "Allow" for s in policy.get("Statement", [])):
                raise ValueError("bucket policy allow")
        rules = client.get_bucket_lifecycle_configuration(Bucket=bucket).get(
            "Rules", []
        )
        if not any(
            r.get("Status") == "Enabled"
            and r.get("Filter", {}) in ({}, {"Prefix": ""})
            and r.get("AbortIncompleteMultipartUpload", {}).get(
                "DaysAfterInitiation", 999
            )
            <= 1
            and r.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays", 999) <= 1
            for r in rules
        ):
            raise ValueError("version cleanup")

    @staticmethod
    def _put(client, bucket, key, stream, length):
        result = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=stream,
            ContentLength=length,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
            ServerSideEncryption="AES256",
        )
        version = result.get("VersionId")
        if not version or version == "null":
            raise AttachmentError("storage_unverified", 503)
        return ObjectIdentity(key, version)

    def put_quarantine(self, upload_id, stream, length):
        self.verify_readiness()
        return self._put(
            self.quarantine,
            self.quarantine_bucket,
            f"quarantine/{upload_id}",
            stream,
            length,
        )

    def snapshot(self, identity, output, *, expected_length):
        """Bounded read of an exact version; never fall back to mutable latest."""
        self.verify_readiness()
        if not identity.version or identity.version == "null":
            raise AttachmentError("storage_unverified", 503)
        result = self.quarantine.get_object(
            Bucket=self.quarantine_bucket, Key=identity.key, VersionId=identity.version
        )
        body = result["Body"]
        try:
            if (
                result.get("VersionId") != identity.version
                or result["ContentLength"] != expected_length
            ):
                raise AttachmentError("snapshot_changed", 409)
            digest, size = hashlib.sha256(), 0
            while True:
                chunk = body.read(min(256 * 1024, expected_length - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_length:
                    raise AttachmentError("length_mismatch")
                digest.update(chunk)
                output.write(chunk)
            if size != expected_length:
                raise AttachmentError("length_mismatch")
            return digest.hexdigest()
        finally:
            body.close()

    def open_range(self, identity, *, start, end, expected_length):
        """Exact immutable serving version; caller closes the bounded body."""
        body = None
        try:
            self.verify_readiness()
            if (
                not identity.version
                or identity.version == "null"
                or not 0 <= start <= end < expected_length
            ):
                raise ValueError
            result = self.serving.get_object(
                Bucket=self.serving_bucket,
                Key=identity.key,
                VersionId=identity.version,
                Range=f"bytes={start}-{end}",
            )
            body = result["Body"]
            if (
                result.get("VersionId") != identity.version
                or result.get("ContentLength") != end - start + 1
                or result.get("ContentRange")
                != f"bytes {start}-{end}/{expected_length}"
            ):
                raise ValueError
            return body
        except Exception:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass
            raise AttachmentError("media_unavailable", 503) from None

    def erase_upload(self, upload_id):
        """Remove exact server-owned keys, every version and incomplete part.

        No prefix-wide deletion, source URLs, or user-provided storage identities.
        Repeated sweeps through capability expiry handle writes racing enumeration.
        Future derivative namespaces must extend this before they can be enabled.
        """
        upload_id = UUID(str(upload_id))
        try:
            for client, bucket, key in (
                (self.quarantine, self.quarantine_bucket, f"quarantine/{upload_id}"),
                (self.serving, self.serving_bucket, f"original/{upload_id}"),
            ):
                pages = client.get_paginator("list_object_versions").paginate(
                    Bucket=bucket, Prefix=key, PaginationConfig={"PageSize": 100}
                )
                for index, page in enumerate(pages):
                    if index >= 100:
                        raise ValueError
                    for item in page.get("Versions", []) + page.get(
                        "DeleteMarkers", []
                    ):
                        if item["Key"] == key:
                            client.delete_object(
                                Bucket=bucket, Key=key, VersionId=item["VersionId"]
                            )
                pages = client.get_paginator("list_multipart_uploads").paginate(
                    Bucket=bucket, Prefix=key, PaginationConfig={"PageSize": 100}
                )
                for index, page in enumerate(pages):
                    if index >= 100:
                        raise ValueError
                    for item in page.get("Uploads", []):
                        if item["Key"] == key:
                            client.abort_multipart_upload(
                                Bucket=bucket, Key=key, UploadId=item["UploadId"]
                            )
        except Exception:
            raise AttachmentError("cleanup_unavailable", 503) from None

    def promote(self, snapshot, *, upload_id, expected_length, expected_sha256):
        self.verify_readiness()
        snapshot.seek(0)
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: snapshot.read(256 * 1024), b""):
            size += len(chunk)
            if size > expected_length:
                raise AttachmentError("snapshot_changed", 409)
            digest.update(chunk)
        if size != expected_length or digest.hexdigest() != expected_sha256:
            raise AttachmentError("snapshot_changed", 409)
        snapshot.seek(0)
        # Deterministic server identity lets the deletion outbox find an object
        # even if the DB transaction dies after PUT but before blob publication.
        key = f"original/{UUID(str(upload_id))}"
        return self._put(self.serving, self.serving_bucket, key, snapshot, size)


def from_settings(settings):
    """Explicit credentials only: never the ambient workload identity chain."""
    import boto3
    from botocore.config import Config

    config = getattr(settings, "FI_ATTACHMENTS_STORAGE", {})
    if config.get("backend") != "s3":
        raise AttachmentError("storage_backend_unqualified", 503)
    required = (
        "region",
        "quarantine_bucket",
        "serving_bucket",
        "quarantine_access_key",
        "quarantine_secret_key",
        "serving_access_key",
        "serving_secret_key",
    )
    if any(not config.get(k) for k in required):
        raise AttachmentError("storage_unverified", 503)

    # No endpoint override: GCS/MinIO cannot masquerade as qualified native S3.
    def client(prefix):
        return boto3.client(
            "s3",
            region_name=config["region"],
            aws_access_key_id=config[f"{prefix}_access_key"],
            aws_secret_access_key=config[f"{prefix}_secret_key"],
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 0},
                proxies={},
            ),
        )

    return PrivateMediaStorage(
        quarantine_client=client("quarantine"),
        serving_client=client("serving"),
        quarantine_bucket=config["quarantine_bucket"],
        serving_bucket=config["serving_bucket"],
    )
