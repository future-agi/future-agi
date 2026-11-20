"""Independent admission, decoder, source-key and historical storage gates.

Workers select durable owners first. Invalid current storage, decoder setup or
source keys never become an implicit prerequisite for historical read/erasure.
"""

import os
import stat
import tempfile

from django.conf import settings as django_settings

from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import DecoderRequirements, SourceRef


def require_new_admission(*, operation, settings=None):
    from tracer.services.attachments.storage import bind_for_admission

    settings = django_settings if settings is None else settings
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        raise AttachmentError("disabled", 503)
    if operation not in ("upload", "import"):
        raise AttachmentError("storage_unverified", 503)
    # Format-specific decoder and source readiness are separately requested by
    # the producer once its immutable plan/source is known. They cannot be
    # guessed from 'import' (ProviderFile need not use a URL envelope).
    from tracer.services.attachments import sandbox

    sandbox.self_test()
    _scratch(settings, 0)
    return bind_for_admission(settings=settings)


def _scratch(settings, required):
    fd = None
    try:
        path = getattr(settings, "FI_ATTACHMENTS_WORK_DIR", "")
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & stat.S_IWOTH:
            raise ValueError
        space = os.fstatvfs(fd)
        if space.f_bavail * space.f_frsize < required or not os.access(
            path, os.W_OK | os.X_OK
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 - filesystem boundary, fails closed
        raise AttachmentError("validation_unavailable", 503) from None
    finally:
        if fd is not None:
            os.close(fd)

    return path


def snapshot_file(*, named):
    # Never fall back to the process-wide temporary volume for attachment bytes.
    directory = _scratch(django_settings, 0)
    factory = tempfile.NamedTemporaryFile if named else tempfile.TemporaryFile
    try:
        return factory(dir=directory)
    except OSError:
        raise AttachmentError("validation_unavailable", 503) from None


def require_decoder(requirements: DecoderRequirements) -> None:
    from tracer.services.attachments import sandbox

    if not isinstance(requirements, DecoderRequirements):
        raise AttachmentError("validation_unavailable", 503)
    _scratch(django_settings, requirements.scratch_bytes)
    sandbox.self_test()


def require_source_keys(*, source: SourceRef) -> None:
    if not isinstance(source, SourceRef):
        raise AttachmentError("source_unavailable", 503)
    if source.kind == "RemoteUrlEnvelope":
        from tracer.services.attachments import envelopes

        envelopes._keys()
    # ProviderFile credentials belong to the selected provider connection and
    # must be resolved by that source reader, not by a global envelope gate.


def resolve_historical(binding_id, *, operation):
    from tracer.services.attachments.storage import for_binding

    if operation not in ("read", "write", "erase"):
        raise AttachmentError("storage_unverified", 503)
    return for_binding(binding_id, operation=operation)


def check(*, storage=None):
    """Old monolithic callers must migrate; never silently choose a default."""
    raise AttachmentError("storage_unverified", 503)
