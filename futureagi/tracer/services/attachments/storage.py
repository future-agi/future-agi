"""Private binding-addressed storage; never resolves historical owners by default.

Provider IO is outside transactions. Bindings contain observed resource identity,
not credentials or authorization. Caller-owned publication/lease/cleanup fencing
is still required; an empty enumeration is NOT proof of a stopped writer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from django.db import connections

from tfc.utils.storage_config import (
    materialize_attachment_binding_config,
    resolve_attachment_binding_config,
    resolve_attachment_posture_credentials,
    resolve_attachment_storage_config,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectIdentity,
    PrivateMediaStorage,
    StorageOperation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tfc.utils.storage_config import BindingAccessConfig
    from tracer.models.attachment_storage import AttachmentStorageBinding
    from tracer.services.attachments.storage_gcs import GCSPrivateMediaStorage
    from tracer.services.attachments.storage_s3 import S3PrivateMediaStorage

__all__ = ["ObjectIdentity", "PrivateMediaStorage", "bind_for_admission", "for_binding"]


def outside_transaction():
    """Refuse IO if this thread holds any Django transaction, including aliases."""
    if any(connection.in_atomic_block for connection in connections.all()):
        raise AttachmentError("storage_unverified", 503)


class _AdmissionSettings(Protocol):
    @property
    def FI_ATTACHMENTS_STORAGE(self) -> Mapping[str, str | None]: ...


def _schema() -> tuple[
    type[AttachmentStorageBinding], tuple[str, ...], Callable[[dict[str, object]], str]
]:
    from tracer.models.attachment_storage import (
        BINDING_IDENTITY_FIELDS,
        AttachmentStorageBinding,
        binding_identity_sha256,
    )

    return AttachmentStorageBinding, BINDING_IDENTITY_FIELDS, binding_identity_sha256


def _provider(
    provider: str,
) -> type[S3PrivateMediaStorage] | type[GCSPrivateMediaStorage]:
    if provider == "s3":
        from tracer.services.attachments.storage_s3 import S3PrivateMediaStorage

        return S3PrivateMediaStorage
    if provider == "minio":
        from tracer.services.attachments.storage_minio import MinIOPrivateMediaStorage

        return MinIOPrivateMediaStorage
    if provider == "gcs":
        from tracer.services.attachments.storage_gcs import GCSPrivateMediaStorage

        return GCSPrivateMediaStorage
    raise AttachmentError("storage_unverified", 503)


def _posture_type(provider):
    if provider == "gcs":
        from google.auth.credentials import Credentials

        return Credentials
    if provider == "minio":
        from tracer.services.attachments.storage_minio import MinIOPostureCredentials

        return MinIOPostureCredentials
    from tracer.services.attachments.storage_s3 import S3PostureCredentials

    return S3PostureCredentials


def _settings(supplied):
    if supplied is None:
        from django.conf import settings

        return settings
    return supplied


def _posture(access: BindingAccessConfig, settings, provider):
    # Identity/incarnation verification can need read-only provider metadata even
    # for historical erasure. It never needs decoder/source/default credentials.
    return resolve_attachment_posture_credentials(
        access,
        supplied=getattr(settings, "FI_ATTACHMENTS_POSTURE_CREDENTIALS", {}),
        expected_type=_posture_type(provider),
    )


class _NativeRegistry:
    @staticmethod
    def admission(settings):
        config = resolve_attachment_storage_config(settings.FI_ATTACHMENTS_STORAGE)
        assert config is not None  # Resolution is enabled for admission.
        provider = _provider(config.storage.backend)
        reference = settings.FI_ATTACHMENTS_STORAGE.get("posture_credential_ref")
        supplied = getattr(settings, "FI_ATTACHMENTS_POSTURE_CREDENTIALS", {})
        posture = supplied.get(reference) if reference is not None else None
        if posture is not None and not isinstance(
            posture, _posture_type(config.storage.backend)
        ):
            raise ValueError
        return provider, config, posture

    @staticmethod
    def historical(binding, settings, *, operation):
        access = resolve_attachment_binding_config(
            binding.id, operation=operation, settings=settings
        )
        config = materialize_attachment_binding_config(binding=binding, config=access)
        return _provider(binding.provider)(
            binding=binding,
            config=config,
            posture_credentials=_posture(access, settings, binding.provider),
        )


def _registry(settings):
    # A Python settings dependency, never an environment selector or fallback.
    return getattr(settings, "FI_ATTACHMENTS_STORAGE_REGISTRY", _NativeRegistry)


def for_binding(
    binding_id: UUID, *, operation: StorageOperation, settings: object | None = None
) -> PrivateMediaStorage:
    """Resolve only the persisted tuple and its explicit credential references."""
    failure_code = "storage_unverified"
    try:
        outside_transaction()
        if not isinstance(binding_id, UUID) or operation not in (
            "admit",
            "write",
            "read",
            "erase",
        ):
            raise ValueError
        settings = _settings(settings)
        model, _, _ = _schema()
        binding = model.objects.get(pk=binding_id)
        if binding.provider == "gcs" and operation == "erase":
            failure_code = "cleanup_unavailable"
        adapter = _registry(settings).historical(binding, settings, operation=operation)
        adapter.verify_identity(operation=operation)
        if operation in ("admit", "write"):
            adapter.verify_readiness()
        return adapter
    except Exception:  # noqa: BLE001 - provider boundary, fails closed
        # Provider/configuration exceptions may carry secrets or locators.
        raise AttachmentError(failure_code, 503) from None


def bind_for_admission(*, settings: _AdmissionSettings) -> UUID:
    """Observe current resources, verify posture, then persist the immutable tuple.

    Observation and readiness happen BEFORE the short get-or-create transaction.
    The resulting historical access mapping must already exist in configuration;
    creating a binding does not mint credentials or authorize resource changes.
    """
    try:
        outside_transaction()
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
            raise AttachmentError("disabled", 503)
        model, fields, digest = _schema()
        registry = _registry(settings)
        provider, config, posture = registry.admission(settings)
        values = provider.discover_identity(config=config, posture_credentials=posture)
        if set(values) != set(fields):
            raise ValueError
        fingerprint = digest(values)
        candidate = model(id=uuid4(), identity_sha256=fingerprint, **values)
        adapter = provider(
            binding=candidate, config=config, posture_credentials=posture
        )
        adapter.verify_readiness()
        binding, _ = model.objects.get_or_create(
            identity_sha256=fingerprint, defaults=values
        )
        if any(getattr(binding, name) != values[name] for name in fields):
            raise ValueError
        # Check that owners created now will actually be resolvable historically.
        # No fallback to the current config if the deployment omitted references.
        registry.historical(binding, settings, operation="admit").verify_readiness()
        return binding.id
    except AttachmentError:
        raise
    except Exception:  # noqa: BLE001 - provider boundary, fails closed
        raise AttachmentError("storage_unverified", 503) from None


def from_settings(settings):
    """Fail closed for old key-only callers until their binding migration lands."""
    raise AttachmentError("storage_unverified", 503)
