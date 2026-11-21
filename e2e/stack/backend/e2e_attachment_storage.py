"""PR byte-store registry, copied into the E2E image only. Not AWS qualification."""

import os

from tfc.utils.storage_config import (
    AttachmentStorageConfig,
    StorageConfig,
    StorageCredentials,
)
from tracer.services.attachments.storage_s3 import S3PrivateMediaStorage

HOST = "attachments-s3:5000"
REGION = "us-east-1"
QUARANTINE = "e2e-attachments-quarantine"
SERVING = "e2e-attachments-serving"


class MotoByteStore(S3PrivateMediaStorage):
    # Use the existing S3 wire implementation, including versioned HEAD, digest,
    # ranged GET, supervised conditional PUT and exact-version erasure. Never
    # pass an address from a binding, setting or ambient AWS profile to boto.
    @classmethod
    def _native_guard(cls, config):
        if (
            config.storage.endpoint_url,
            config.storage.region,
            config.quarantine_bucket,
            config.serving_bucket,
        ) != (f"https://{HOST}", REGION, QUARANTINE, SERVING):
            raise ValueError("not the managed E2E byte store")

    @classmethod
    def _inventory(cls, config, posture, credentials):
        owner, buckets = super()._inventory(config, posture, credentials)
        return f"e2e-double:{owner}", buckets

    @classmethod
    def _owner(cls, posture, bucket, account):
        # Moto returns different owner IDs for ListBuckets and GetBucketAcl.
        # Record both observed values; never assert a native AWS account match.
        owner = posture.get_bucket_acl(Bucket=bucket)["Owner"]["ID"]
        if not isinstance(owner, str) or not owner:
            raise ValueError("missing fixture bucket owner")
        return owner

    @classmethod
    def _resource(cls, config, bucket, account):
        # The schema's provider is the wire protocol, not a fourth native
        # provider. This explicit tag is part of both immutable resource IDs.
        return f"e2e-double://{HOST}/{bucket}"


def _config():
    def credentials(purpose):
        prefix = f"FI_ATTACHMENTS_{purpose}_"
        return StorageCredentials(
            os.environ[prefix + "ACCESS_KEY"], os.environ[prefix + "SECRET_KEY"]
        )

    return AttachmentStorageConfig(
        StorageConfig("s3", HOST, True, REGION, "/etc/futureagi-e2e/attachments-s3.crt"),
        QUARANTINE,
        SERVING,
        credentials("QUARANTINE"),
        credentials("SERVING"),
    )


def admission(settings):
    return MotoByteStore, _config(), None


def historical(binding, settings, *, operation):
    # Never use a real binding or a new bucket for an old owner. The base
    # constructor also verifies the full persisted digest and both bucket names;
    # the caller re-observes the tuple before granting data access.
    for purpose in ("quarantine", "serving"):
        expected = f"e2e-double://{HOST}/{getattr(binding, purpose + '_bucket')}"
        if getattr(binding, purpose + "_resource_identity") != expected:
            raise ValueError("not an E2E binding")
    return MotoByteStore(binding=binding, config=_config())
