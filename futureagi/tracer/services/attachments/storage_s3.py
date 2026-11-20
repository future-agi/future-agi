"""Native S3 private storage, with bounded S3-wire operations reused by MinIO.

No default session, profile, endpoint or credential chain. PUT is one create-only
request supervised by a killable process; an unknown result remains cleanup work
at the caller's pre-persisted locator. Tests of this code are not native evidence.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import multiprocessing
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import UUID

import botocore.session
from botocore.config import Config
from botocore.exceptions import ClientError

from tfc.utils.storage_config import (
    AttachmentStorageConfig,
    StorageCredentials,
    _environment_proxy,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    EraseResult,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    VersionPage,
)

BUFFER = 256 * 1024
RANGE_MAX = 8 * 1024 * 1024
PAGE_MAX = 100


@dataclass(frozen=True, repr=False)
class S3PostureCredentials:
    """Explicit already-delivered read-only metadata credentials; never a file."""

    credentials: StorageCredentials = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.credentials, StorageCredentials):
            raise ValueError("invalid posture credentials")


def _outside():
    from tracer.services.attachments.storage import outside_transaction

    outside_transaction()


def _fields():
    from tracer.models.attachment_storage import (
        BINDING_IDENTITY_FIELDS,
        binding_identity_sha256,
    )

    return BINDING_IDENTITY_FIELDS, binding_identity_sha256


def _error(code="storage_unverified"):
    return AttachmentError(code, 503)


def _length(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError
    return value


def _revision(value):
    if not isinstance(value, str) or not value or value == "null" or len(value) > 255:
        raise ValueError
    return value


def _absent(exc):
    if not isinstance(exc, ClientError):
        return False
    return (
        exc.response.get("Error", {}).get("Code")
        in ("NoSuchKey", "NoSuchVersion", "404")
        and exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
    )


def _optional(client, method, bucket, absent):
    try:
        return getattr(client, method)(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == absent:
            return {}
        raise


def _client(config, credentials):
    if not isinstance(credentials, StorageCredentials) or not (
        credentials.access_key and credentials.secret_key
    ):
        raise ValueError
    transport = config.storage
    session = botocore.session.Session()
    # These instance settings override SDK environment/profile file discovery.
    session.set_config_variable("config_file", os.devnull)
    session.set_config_variable("credentials_file", os.devnull)
    session.set_config_variable("profile", "attachment-explicit")
    # None would continue to AWS_PROFILE in botocore's provider chain. Supply
    # one empty in-memory profile so neither env selection nor profile files
    # can influence authentication, endpoints or service options.
    session._config = {"profiles": {"attachment-explicit": {}}}
    session.set_config_variable("region", transport.region)
    proxy = _environment_proxy(transport.endpoint_url)
    client = session.create_client(
        "s3",
        region_name=transport.region,
        endpoint_url=transport.endpoint_url,
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        aws_session_token=credentials.session_token,
        verify=transport.ca_certs,
        config=Config(
            signature_version="s3v4",
            connect_timeout=3,
            read_timeout=10,
            retries={"total_max_attempts": 1},
            proxies={"http": proxy, "https": proxy} if proxy else {},
            s3={"addressing_style": "path", "payload_signing_enabled": True},
            ignore_configured_endpoint_urls=True,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    expected = urlsplit(transport.endpoint_url)

    def pin_endpoint(request, **kwargs):
        target = urlsplit(request.url)
        if (target.scheme, target.netloc) != (expected.scheme, expected.netloc):
            raise _error()

    def no_redirect(response=None, **kwargs):
        # S3 has a special region redirector separate from transport retries.
        if response and (
            response[0].status_code in (301, 302, 303, 307, 308)
            or response[1].get("Error", {}).get("Code")
            in ("PermanentRedirect", "AuthorizationHeaderMalformed")
        ):
            raise _error()

    client.meta.events.register_first("before-send.s3", pin_endpoint)
    client.meta.events.register_first("needs-retry.s3", no_redirect)
    return client


class _SnapshotMismatch(ValueError):
    """The local stream differs from the expected length or digest."""


def _hash_stream(stream, length):
    stream.seek(0)
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = stream.read(min(BUFFER, length - size + 1))
        if not isinstance(chunk, bytes):
            raise ValueError
        if not chunk:
            break
        size += len(chunk)
        if size > length:
            raise _SnapshotMismatch
        digest.update(chunk)
    if size != length:
        raise _SnapshotMismatch
    stream.seek(0)
    return base64.b64encode(digest.digest()).decode(), digest.hexdigest()


def _put_child(pipe, adapter, permit, stream, length, digest):
    try:
        # Construct a fresh client in the child, never use inherited live sockets.
        client = _client(
            adapter.config,
            getattr(adapter.config, permit.locator.purpose + "_credentials"),
        )
        result = adapter._put_request(client, permit, stream, length, digest)
        pipe.send((True, result.version))
    except _SnapshotMismatch:
        pipe.send((False, "snapshot_changed"))
    except BaseException:
        # Never serialize raw provider exceptions, credentials or response bodies.
        pipe.send((False, None))
    finally:
        pipe.close()


class S3PrivateMediaStorage:
    provider = "s3"
    direct_upload_enabled = False

    def __init__(
        self, *, binding, config: AttachmentStorageConfig, posture_credentials=None
    ):
        _outside()
        fields, digest = _fields()
        values = {name: getattr(binding, name) for name in fields}
        if (
            not isinstance(binding.id, UUID)
            or digest(values) != binding.identity_sha256
            or config.storage.backend != self.provider
            or values["provider"] != self.provider
            or values["endpoint"] != config.storage.endpoint_url
            or values["region"] != config.storage.region
            or any(
                values[p + "_bucket"] != getattr(config, p + "_bucket")
                for p in ("quarantine", "serving")
            )
            or config.quarantine_bucket == config.serving_bucket
        ):
            raise _error()
        self._native_guard(config)
        self.binding_id = binding.id
        self.identity_sha256 = binding.identity_sha256
        self.identity = MappingProxyType(values)
        self.config = config
        self.quarantine_bucket = config.quarantine_bucket
        self.serving_bucket = config.serving_bucket
        self.quarantine = _client(config, config.quarantine_credentials)
        self.serving = _client(config, config.serving_credentials)
        self.posture_credentials = posture_credentials
        self.posture = self._posture_client(config, posture_credentials)

    @classmethod
    def _native_guard(cls, config):
        host = config.storage.endpoint
        region = config.storage.region
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        if not config.storage.secure or host not in (
            f"s3.{region}.{suffix}",
            f"s3-{region}.{suffix}",
            "s3.amazonaws.com" if region == "us-east-1" else "",
        ):
            raise ValueError
        for bucket in (config.quarantine_bucket, config.serving_bucket):
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
                raise ValueError

    @classmethod
    def _posture_client(cls, config, posture):
        if posture is not None:
            if not isinstance(posture, S3PostureCredentials):
                raise ValueError
            return _client(config, posture.credentials)
        # An explicit purpose principal may already have metadata permissions.
        return _client(config, config.quarantine_credentials)

    @classmethod
    def _inventory(cls, config, posture, credentials):
        buckets, owner, cursor, seen = {}, None, None, set()
        for _ in range(PAGE_MAX):
            args = {"MaxBuckets": 100}
            if cursor:
                args["ContinuationToken"] = cursor
            result = posture.list_buckets(**args)
            current_owner = result["Owner"]["ID"]
            if not current_owner or (owner is not None and owner != current_owner):
                raise ValueError
            owner = current_owner
            for row in result.get("Buckets", []):
                if row["Name"] in (config.quarantine_bucket, config.serving_bucket):
                    if row["Name"] in buckets:
                        raise ValueError
                    buckets[row["Name"]] = row["CreationDate"]
            cursor = result.get("ContinuationToken")
            if not cursor:
                return owner, buckets
            if cursor in seen:
                raise ValueError
            seen.add(cursor)
        raise ValueError

    @classmethod
    def _observe(cls, config, posture, credentials):
        account, buckets = cls._inventory(config, posture, credentials)
        values = {
            "provider": cls.provider,
            "endpoint": config.storage.endpoint_url,
            "account_identity": account,
            "region": config.storage.region,
        }
        for purpose in ("quarantine", "serving"):
            bucket = getattr(config, purpose + "_bucket")
            created = buckets[bucket]
            if not isinstance(created, datetime) or created.tzinfo is None:
                raise ValueError
            location = (
                posture.get_bucket_location(Bucket=bucket).get("LocationConstraint")
                or "us-east-1"
            )
            if location == "EU":
                location = "eu-west-1"
            if location != config.storage.region:
                raise ValueError
            rules = posture.get_bucket_encryption(Bucket=bucket)[
                "ServerSideEncryptionConfiguration"
            ]["Rules"]
            if len(rules) != 1:
                raise ValueError
            encryption = rules[0]["ApplyServerSideEncryptionByDefault"]
            mode = encryption["SSEAlgorithm"]
            key = encryption.get("KMSMasterKeyID", "")
            cls._validate_encryption(mode, key)
            owner = cls._owner(posture, bucket, account)
            resource = cls._resource(config, bucket, account)
            values.update(
                {
                    purpose + "_" + k: v
                    for k, v in {
                        "bucket": bucket,
                        "resource_identity": resource,
                        "owner_identity": owner,
                        "created_at": created.astimezone(UTC),
                        "encryption_mode": mode,
                        "encryption_key_identity": key,
                    }.items()
                }
            )
        return values

    @classmethod
    def _owner(cls, posture, bucket, account):
        owner = posture.get_bucket_acl(Bucket=bucket)["Owner"]["ID"]
        if owner != account:
            raise ValueError
        return owner

    @classmethod
    def _resource(cls, config, bucket, account):
        partition = (
            "aws-cn"
            if config.storage.region.startswith("cn-")
            else (
                "aws-us-gov" if config.storage.region.startswith("us-gov-") else "aws"
            )
        )
        return f"arn:{partition}:s3:::{bucket}"

    @classmethod
    def _validate_encryption(cls, mode, key):
        if mode == "AES256" and not key:
            return
        if mode == "aws:kms" and re.fullmatch(
            r"arn:aws(?:-cn|-us-gov)?:kms:[a-z0-9-]+:[0-9]{12}:key/[a-zA-Z0-9-]+", key
        ):
            return
        raise ValueError

    @classmethod
    def discover_identity(cls, *, config, posture_credentials=None):
        try:
            _outside()
            cls._native_guard(config)
            return cls._observe(
                config,
                cls._posture_client(config, posture_credentials),
                posture_credentials,
            )
        except Exception:
            raise _error() from None

    @staticmethod
    def _privacy(client, bucket):
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
            raise ValueError
        ownership = client.get_bucket_ownership_controls(Bucket=bucket)[
            "OwnershipControls"
        ]["Rules"]
        if ownership != [{"ObjectOwnership": "BucketOwnerEnforced"}]:
            raise ValueError
        acl = client.get_bucket_acl(Bucket=bucket)
        if not acl.get("Grants") or any(
            grant["Grantee"].get("ID") != acl["Owner"]["ID"]
            or grant["Grantee"].get("Type") != "CanonicalUser"
            for grant in acl["Grants"]
        ):
            raise ValueError
        S3PrivateMediaStorage._policy(client, bucket)

    @staticmethod
    def _policy(client, bucket):
        result = _optional(client, "get_bucket_policy", bucket, "NoSuchBucketPolicy")
        if result:
            policy = json.loads(result["Policy"])
            statements = policy.get("Statement", [])
            if not isinstance(statements, list) or any(
                s.get("Effect") != "Deny" for s in statements
            ):
                # Conservative: even conditional or named-principal Allows need
                # separate reviewed authority, never infer them to be private.
                raise ValueError

    def verify_identity(self, *, operation):
        try:
            _outside()
            if operation not in ("admit", "write", "read", "erase"):
                raise ValueError
            observed = self._observe(
                self.config, self.posture, self.posture_credentials
            )
            if observed != self.identity:
                raise ValueError
            if operation != "erase":
                for bucket in (self.quarantine_bucket, self.serving_bucket):
                    self._privacy(self.posture, bucket)
        except Exception:
            raise _error() from None

    def _full_posture(self, client, bucket, purpose):
        versioning = client.get_bucket_versioning(Bucket=bucket)
        if (
            versioning.get("Status") != "Enabled"
            or versioning.get("MFADelete") == "Enabled"
        ):
            raise ValueError
        cors = _optional(client, "get_bucket_cors", bucket, "NoSuchCORSConfiguration")
        if cors.get("CORSRules"):
            raise ValueError
        lock = _optional(
            client,
            "get_object_lock_configuration",
            bucket,
            "ObjectLockConfigurationNotFoundError",
        )
        if (
            lock.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled")
            == "Enabled"
        ):
            raise ValueError
        rules = client.get_bucket_lifecycle_configuration(Bucket=bucket).get(
            "Rules", []
        )
        cleanup = False
        for rule in rules:
            if rule.get("Status") != "Enabled":
                continue
            if rule.get("Transitions") or rule.get("NoncurrentVersionTransitions"):
                raise ValueError
            expiration = rule.get("Expiration")
            if expiration and (
                purpose == "serving"
                or type(expiration.get("Days")) is not int
                or expiration["Days"] < 1
            ):
                raise ValueError
            universal = rule.get("Filter", {}) in ({}, {"Prefix": ""}) and not rule.get(
                "Prefix", ""
            )
            cleanup |= (
                universal
                and rule.get("AbortIncompleteMultipartUpload", {}).get(
                    "DaysAfterInitiation"
                )
                == 1
                and rule.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays")
                == 1
                and "NewerNoncurrentVersions"
                not in rule.get("NoncurrentVersionExpiration", {})
            )
        if not cleanup:
            raise ValueError

    def verify_readiness(self):
        try:
            self.verify_identity(operation="write")
            for purpose in ("quarantine", "serving"):
                self._full_posture(
                    self.posture, getattr(self, purpose + "_bucket"), purpose
                )
        except Exception:
            raise _error() from None

    def _target(self, locator):
        if (
            not isinstance(locator, (ObjectLocator, ObjectIdentity))
            or locator.binding_id != self.binding_id
        ):
            raise ValueError
        if locator.purpose not in ("quarantine", "serving"):
            raise ValueError
        if (
            not isinstance(locator.key, str)
            or not locator.key
            or len(locator.key) > 255
        ):
            raise ValueError
        return getattr(self, locator.purpose), getattr(
            self, locator.purpose + "_bucket"
        )

    def _encryption(self, purpose):
        result = {"ServerSideEncryption": self.identity[purpose + "_encryption_mode"]}
        key = self.identity[purpose + "_encryption_key_identity"]
        if key:
            result["SSEKMSKeyId"] = key
        return result

    def _check_encryption(self, result, purpose):
        for key, expected in self._encryption(purpose).items():
            if result.get(key) != expected:
                raise ValueError

    def _permit(self, permit, purpose):
        if not isinstance(permit, PutPermit) or permit.locator.purpose != purpose:
            raise ValueError
        self._target(permit.locator)
        remaining = (permit.deadline_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0 or (permit.fence is not None and remaining > 30):
            raise ValueError
        return min(remaining, 30)

    def _put_request(self, client, permit, stream, length, digest):
        # Runs in the supervised process, with a second check at dispatch.
        self._permit(permit, permit.locator.purpose)
        checksum, actual = _hash_stream(stream, length)
        if digest is not None and actual != digest:
            raise _SnapshotMismatch
        self._permit(permit, permit.locator.purpose)
        bucket = getattr(self, permit.locator.purpose + "_bucket")
        result = client.put_object(
            Bucket=bucket,
            Key=permit.locator.key,
            Body=stream,
            ContentLength=length,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
            ChecksumSHA256=checksum,
            **self._encryption(permit.locator.purpose),
        )
        version = _revision(result.get("VersionId"))
        self._check_encryption(result, permit.locator.purpose)
        if result.get("ChecksumSHA256") != checksum:
            raise ValueError
        head = client.head_object(
            Bucket=bucket, Key=permit.locator.key, VersionId=version
        )
        if head.get("VersionId") != version or head.get("ContentLength") != length:
            raise ValueError
        self._check_encryption(head, permit.locator.purpose)
        return ObjectIdentity(
            self.binding_id, permit.locator.purpose, permit.locator.key, version
        )

    def _supervised_put(self, permit, stream, length, digest):
        timeout = self._permit(permit, permit.locator.purpose)
        context = multiprocessing.get_context("fork")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_put_child, args=(child, self, permit, stream, length, digest)
        )
        expires = time.monotonic() + timeout
        try:
            process.start()
            child.close()
            if not parent.poll(max(0, expires - time.monotonic())):
                raise ValueError
            success, version = parent.recv()
            process.join(max(0, expires - time.monotonic()))
            if (
                process.is_alive()
                or process.exitcode != 0
                or time.monotonic() >= expires
            ):
                raise ValueError
            if not success:
                if version == "snapshot_changed":
                    raise _SnapshotMismatch
                raise ValueError
            return ObjectIdentity(
                self.binding_id,
                permit.locator.purpose,
                permit.locator.key,
                _revision(version),
            )
        finally:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(0.2)
                if process.is_alive():
                    process.kill()
                    process.join()
            parent.close()
            child.close()
            if process.pid is not None:
                process.close()

    def _put(self, permit, stream, length, purpose, expected_sha256=None):
        try:
            _outside()
            self._permit(permit, purpose)
            _length(length)
            self.verify_readiness()
            # Hashing/length inspection is inside the same killable deadline as
            # the single PUT and exact-version confirmation, not an unbounded
            # preflight read in the worker process.
            return self._supervised_put(permit, stream, length, expected_sha256)
        except _SnapshotMismatch:
            if purpose == "serving":
                raise AttachmentError("snapshot_changed", 409) from None
            raise _error() from None
        except Exception:
            raise _error() from None

    def put_quarantine(self, permit, stream, length):
        return self._put(permit, stream, length, "quarantine")

    def promote(self, snapshot, *, permit, expected_length, expected_sha256):
        if not isinstance(expected_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256
        ):
            raise AttachmentError("snapshot_changed", 409)
        return self._put(permit, snapshot, expected_length, "serving", expected_sha256)

    def _read(self, identity, *, expected_length, start=None, end=None):
        client, bucket = self._target(identity)
        if not isinstance(identity, ObjectIdentity):
            raise ValueError
        _revision(identity.version)
        _length(expected_length)
        args = {"Bucket": bucket, "Key": identity.key, "VersionId": identity.version}
        if start is not None:
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end < expected_length
                or end - start + 1 > RANGE_MAX
            ):
                raise ValueError
            args["Range"] = f"bytes={start}-{end}"
        result = client.get_object(**args)
        body = result["Body"]
        try:
            size = expected_length if start is None else end - start + 1
            generation = _revision(result.get("VersionId"))
            observed_size = _length(result.get("ContentLength"))
            if generation != identity.version or observed_size != size:
                raise AttachmentError("snapshot_changed", 409)
            if (
                start is not None
                and result.get("ContentRange")
                != f"bytes {start}-{end}/{expected_length}"
            ):
                raise ValueError
            self._check_encryption(result, identity.purpose)
            return body, size
        except BaseException:
            body.close()
            raise

    def snapshot(self, identity, output, *, expected_length):
        body = None
        try:
            _outside()
            self._target(identity)
            self.verify_identity(operation="read")
            body, size = self._read(identity, expected_length=expected_length)
            digest, seen = hashlib.sha256(), 0
            while True:
                chunk = body.read(min(BUFFER, size - seen + 1))
                if not chunk:
                    break
                seen += len(chunk)
                if seen > size or len(chunk) > BUFFER:
                    raise AttachmentError("snapshot_changed", 409)
                digest.update(chunk)
                if output.write(chunk) != len(chunk):
                    raise ValueError
            if seen != size:
                # Truncated transport is ambiguous even with a matching declared size.
                raise ValueError
            return digest.hexdigest()
        except AttachmentError:
            raise
        except Exception:
            raise _error() from None
        finally:
            if body is not None:
                with contextlib.suppress(Exception):
                    body.close()

    def open_range(self, identity, *, start, end, expected_length):
        body = None
        try:
            _outside()
            self._target(identity)
            self.verify_identity(operation="read")
            body, size = self._read(
                identity, expected_length=expected_length, start=start, end=end
            )
            output, seen = io.BytesIO(), 0
            while True:
                chunk = body.read(min(BUFFER, size - seen + 1))
                if not chunk:
                    break
                seen += len(chunk)
                if seen > size or len(chunk) > BUFFER:
                    raise ValueError
                output.write(chunk)
            if seen != size:
                raise ValueError
            output.seek(0)
            return output
        except Exception:
            raise _error("media_unavailable") from None
        finally:
            if body is not None:
                with contextlib.suppress(Exception):
                    body.close()

    def _list(self, locator, cursor, limit):
        client, bucket = self._target(locator)
        if (
            not isinstance(locator, ObjectLocator)
            or type(limit) is not int
            or not 1 <= limit <= PAGE_MAX
        ):
            raise ValueError
        scope = [str(self.binding_id), locator.purpose, locator.key]
        args = {"Bucket": bucket, "Prefix": locator.key, "MaxKeys": limit}
        if cursor is not None:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError
            decoded = json.loads(base64.b64decode(cursor, validate=True))
            if (
                not isinstance(decoded, list)
                or len(decoded) != 5
                or decoded[:3] != scope
            ):
                raise ValueError
            key, version = decoded[3:]
            if not isinstance(key, str) or not key.startswith(locator.key):
                raise ValueError
            args.update(KeyMarker=key, VersionIdMarker=_revision(version))
        page = client.list_object_versions(**args)
        if page.get("IsTruncated") not in (True, False):
            raise ValueError
        rows = page.get("Versions", []) + page.get("DeleteMarkers", [])
        if len(rows) > limit:
            raise ValueError

        def identities(name):
            return tuple(
                ObjectIdentity(
                    self.binding_id,
                    locator.purpose,
                    row["Key"],
                    _revision(row["VersionId"]),
                )
                for row in page.get(name, [])
                if row["Key"] == locator.key
            )

        next_cursor = None
        if page["IsTruncated"]:
            marker = page["NextKeyMarker"]
            version = _revision(page["NextVersionIdMarker"])
            if not isinstance(marker, str) or not marker.startswith(locator.key):
                raise ValueError
            next_cursor = base64.b64encode(
                json.dumps(scope + [marker, version], separators=(",", ":")).encode()
            ).decode()
            if next_cursor == cursor:
                raise ValueError
        return VersionPage(
            identities("Versions"), identities("DeleteMarkers"), next_cursor
        )

    def list_versions(self, locator, *, cursor=None, limit=100):
        try:
            _outside()
            self._target(locator)
            self.verify_identity(operation="erase")
            return self._list(locator, cursor, limit)
        except Exception:
            raise _error("cleanup_unavailable") from None

    def _erase(self, identity):
        client, bucket = self._target(identity)
        if not isinstance(identity, ObjectIdentity):
            raise ValueError
        args = {
            "Bucket": bucket,
            "Key": identity.key,
            "VersionId": _revision(identity.version),
        }
        try:
            result = client.delete_object(**args)
            if result.get("VersionId") not in (None, identity.version):
                raise ValueError
        except ClientError as exc:
            if not _absent(exc):
                raise
        try:
            client.head_object(**args)
        except ClientError as exc:
            if _absent(exc):
                return
            raise
        raise ValueError  # hold/soft-delete/ambiguous acknowledgement stays pending

    def erase_version(self, identity):
        try:
            _outside()
            self._target(identity)
            self.verify_identity(operation="erase")
            self._erase(identity)
        except Exception:
            raise _error("cleanup_unavailable") from None

    def _multipart(self, locator, budget, *, erase):
        client, bucket = self._target(locator)
        args = {"Bucket": bucket, "Prefix": locator.key, "MaxUploads": 100}
        seen, found = set(), False
        while budget[0] > 0:
            budget[0] -= 1
            page = client.list_multipart_uploads(**args)
            if (
                page.get("IsTruncated") not in (True, False)
                or len(page.get("Uploads", [])) > 100
            ):
                raise ValueError
            for item in page.get("Uploads", []):
                if item["Key"] == locator.key:
                    found = True
                    if erase:
                        try:
                            client.abort_multipart_upload(
                                Bucket=bucket,
                                Key=locator.key,
                                UploadId=_revision(item["UploadId"]),
                            )
                        except ClientError as exc:
                            if (
                                exc.response.get("Error", {}).get("Code")
                                != "NoSuchUpload"
                            ):
                                raise
            if not page["IsTruncated"]:
                return found
            markers = (page["NextKeyMarker"], page["NextUploadIdMarker"])
            if (
                markers in seen
                or not markers[0].startswith(locator.key)
                or not all(markers)
            ):
                raise ValueError
            seen.add(markers)
            args.update(KeyMarker=markers[0], UploadIdMarker=markers[1])
        raise ValueError

    def _versions(self, locator, budget, *, erase):
        cursor, seen, found = None, set(), False
        while budget[0] > 0:
            budget[0] -= 1
            page = self._list(locator, cursor, 100)
            for identity in page.versions + page.delete_markers:
                found = True
                if erase:
                    self._erase(identity)
            cursor = page.next_cursor
            if cursor is None:
                return found
            if cursor in seen:
                raise ValueError
            seen.add(cursor)
        raise ValueError

    def erase_all_versions(self, locator):
        try:
            _outside()
            self._target(locator)
            self.verify_identity(operation="erase")
            budget = [PAGE_MAX]
            self._versions(locator, budget, erase=True)
            self._multipart(locator, budget, erase=True)
            remaining_versions = self._versions(locator, budget, erase=False)
            remaining_parts = self._multipart(locator, budget, erase=False)
            empty = not (remaining_versions or remaining_parts)
            return EraseResult(
                empty,
                not empty,
                None if empty else "cleanup_pending",
                datetime.now(UTC),
            )
        except Exception:
            return EraseResult(False, True, "cleanup_unavailable", datetime.now(UTC))

    def erase_upload(self, upload_id):
        # Deliberately no key-only erasure compatibility wrapper: an upload UUID
        # alone cannot prove these keys belong to a terminal unreferenced owner.
        # Callers must perform survivor/fence checks and pass persisted locators.
        return EraseResult(False, True, "cleanup_unavailable", datetime.now(UTC))
