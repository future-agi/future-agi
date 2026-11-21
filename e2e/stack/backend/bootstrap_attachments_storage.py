"""E2E-image-only provisioning of the private attachment buckets in the S3 double.

This provisions the byte-store fixture, not a qualified AWS deployment.
"""

import os
import sys
import time

import boto3
from botocore.config import Config

ENDPOINT = "https://attachments-s3:5000"
BUCKETS = ("QUARANTINE", "SERVING")


def provision(environ):
    if (
        environ.get("FUTURE_AGI_E2E_FIXTURE") != "attachments-storage"
        or tuple(environ.get(f"FI_ATTACHMENTS_{p}_BUCKET") for p in BUCKETS)
        != ("e2e-attachments-quarantine", "e2e-attachments-serving")
    ):
        raise ValueError(
            "Attachment storage bootstrap requires the managed E2E configuration."
        )
    for prefix in BUCKETS:
        bucket = environ[f"FI_ATTACHMENTS_{prefix}_BUCKET"]
        client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            verify="/etc/futureagi-e2e/attachments-s3.crt",
            region_name=environ["FI_ATTACHMENTS_STORAGE_REGION"],
            aws_access_key_id=environ[f"FI_ATTACHMENTS_{prefix}_ACCESS_KEY"],
            aws_secret_access_key=environ[f"FI_ATTACHMENTS_{prefix}_SECRET_KEY"],
            config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 0}),
        )
        for attempt in range(60):
            try:
                client.list_buckets()
                break
            except Exception:  # noqa: BLE001 - waiting for the double to listen
                if attempt == 59:
                    raise
                time.sleep(1)
        try:
            client.create_bucket(Bucket=bucket, ObjectOwnership="BucketOwnerEnforced")
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
        client.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        client.put_bucket_ownership_controls(
            Bucket=bucket,
            OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        )
        client.put_bucket_versioning(
            Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        client.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
        )
        # DEPLOY.md "Bucket lifecycle backstop" (no current-version expiry).
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "e2e-version-cleanup",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                    }
                ]
            },
        )
        print(f"attachment bucket ready: {prefix.lower()}")


if __name__ == "__main__":
    try:
        provision(os.environ)
    except Exception as exc:  # noqa: BLE001 - one-shot job; the type is enough
        print(f"attachment storage bootstrap failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
