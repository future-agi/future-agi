"""MinIO posture is NOT S3 posture with unsupported checks suppressed.

Data operations use the conditional/versioned S3 wire contract. The shipped
MinIO admin SDK supplies explicit read-only installation/effective-CORS evidence;
no admin write API is used. Missing server evidence fails closed. A mock of this
boundary is not proof a deployed MinIO build exposes or satisfies the contract.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from minio.minioadmin import MinioAdmin

from tracer.services.attachments.storage_s3 import (
    S3PrivateMediaStorage,
    S3PostureCredentials,
    _client,
    _optional,
)


@dataclass(frozen=True, repr=False)
class MinIOPostureCredentials:
    """Already-approved bounded admin client and explicit bucket-read identity.

    Runtime delivery owns constructing this object with the shared proxy/CA
    transport, connect<=3s/read<=10s and zero retries. No admin/root credentials
    are discovered here or reused for private data access.
    """

    admin: MinioAdmin = field(repr=False)
    bucket_credentials: S3PostureCredentials = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.admin, MinioAdmin) or not isinstance(
            self.bucket_credentials, S3PostureCredentials
        ):
            raise ValueError("invalid posture credentials")


class MinIOPrivateMediaStorage(S3PrivateMediaStorage):
    provider = "minio"

    @classmethod
    def _native_guard(cls, config):
        endpoint = urlsplit(config.storage.endpoint_url)
        if (
            not config.storage.secure
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
            or endpoint.hostname.endswith(
                ("amazonaws.com", "amazonaws.com.cn", "googleapis.com")
            )
        ):
            raise ValueError

    @classmethod
    def _posture_client(cls, config, posture):
        if not isinstance(posture, MinIOPostureCredentials):
            raise ValueError
        admin = posture.admin
        # The runtime object must target THIS installation, with bounded TLS
        # transport. Do not accept a convenient admin client for another cluster.
        base = getattr(admin, "_url", None)
        if base is None:
            raise ValueError
        url = base.geturl()
        if url.rstrip("/") != config.storage.endpoint_url:
            raise ValueError
        http = getattr(admin, "_http", None)
        options = getattr(http, "connection_pool_kw", {})
        timeout, retries = options.get("timeout"), options.get("retries")
        if (
            timeout is None
            or timeout.connect_timeout is None
            or not 0 < timeout.connect_timeout <= 3
            or timeout.read_timeout is None
            or not 0 < timeout.read_timeout <= 10
            or retries is None
            or retries.total != 0
            or options.get("cert_reqs") != "CERT_REQUIRED"
        ):
            raise ValueError
        return _client(config, posture.bucket_credentials.credentials)

    @staticmethod
    def _server(posture, *, cors):
        info = json.loads(posture.admin.info())
        installation = info["deploymentID"]
        if not isinstance(installation, str) or not installation.strip():
            raise ValueError
        servers = info["servers"]
        if (
            not isinstance(servers, list)
            or not servers
            or any(server.get("state") != "ok" for server in servers)
        ):
            raise ValueError
        if cors:
            # config_get alone is insufficient: env overrides win at runtime.
            # Missing effective env evidence is NOT proof no override exists.
            config = posture.admin.config_get("api")
            records = [
                shlex.split(line) for line in config.splitlines() if line.strip()
            ]
            if len(records) != 1 or records[0][0] != "api":
                raise ValueError
            pairs = [item.split("=", 1) for item in records[0][1:]]
            if any(len(pair) != 2 for pair in pairs):
                raise ValueError
            settings = dict(pairs)
            if len(settings) != len(pairs) or settings.get("cors_allow_origin") != "":
                raise ValueError
            for server in servers:
                env = server.get("env")
                if not isinstance(env, dict):
                    raise ValueError
                # Empty is intentionally required even if another interface
                # reports the configuration as private; '*' is the default.
                if env.get("MINIO_API_CORS_ALLOW_ORIGIN", "") != "":
                    raise ValueError
        return installation

    @classmethod
    def _inventory(cls, config, posture, credentials):
        installation = cls._server(credentials, cors=False)
        _, buckets = super()._inventory(config, posture, credentials)
        return installation, buckets

    @classmethod
    def _owner(cls, posture, bucket, account):
        owner = posture.get_bucket_acl(Bucket=bucket)["Owner"]["ID"]
        if not isinstance(owner, str) or not owner:
            raise ValueError
        return owner

    @classmethod
    def _resource(cls, config, bucket, account):
        return f"minio://{account}/{bucket}"

    @classmethod
    def _validate_encryption(cls, mode, key):
        if mode == "AES256" and not key:
            return
        if (
            mode == "aws:kms"
            and isinstance(key, str)
            and key
            and not any(c in key for c in "\r\n\x00")
        ):
            return
        raise ValueError

    def _privacy(self, client, bucket):
        self._server(self.posture_credentials, cors=True)
        # MinIO does not implement AWS account-level public-access blocks or
        # ownership controls. Verify its actual ACL/policy/global CORS instead.
        acl = client.get_bucket_acl(Bucket=bucket)
        if not acl.get("Grants") or any(
            grant["Grantee"].get("ID") != acl["Owner"]["ID"]
            or grant["Grantee"].get("Type") != "CanonicalUser"
            for grant in acl["Grants"]
        ):
            raise ValueError
        self._policy(client, bucket)

    def _full_posture(self, client, bucket, purpose):
        super()._full_posture(client, bucket, purpose)
        self._server(self.posture_credentials, cors=True)
        mode = self.identity[purpose + "_encryption_mode"]
        key = self.identity[purpose + "_encryption_key_identity"]
        # AES256 on MinIO requires working KMS too; accepting a header alone
        # would silently qualify a server without real encryption support.
        status = json.loads(self.posture_credentials.admin.kms_key_status(key or None))
        if (
            mode not in ("AES256", "aws:kms")
            or status.get("encryptionErr")
            or status.get("decryptionErr")
        ):
            raise ValueError
        if not status.get("key-id"):
            raise ValueError
        # Bucket CORS is checked by the common path as well as server CORS.
        cors = _optional(client, "get_bucket_cors", bucket, "NoSuchCORSConfiguration")
        if cors.get("CORSRules"):
            raise ValueError
