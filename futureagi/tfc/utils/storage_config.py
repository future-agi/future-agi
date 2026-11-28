"""Lazy configuration shared by ordinary media and private attachment adapters.

Importing this module never resolves endpoints, credentials or storage clients.
STORAGE_BACKEND and its existing internal endpoint/region/CA/proxy delivery are
shared; public URLs, bucket provisioning and cached public-media clients are not.
The frozen records below describe configuration, NOT provider qualification,
resource ownership or durable historical bindings.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlparse, urlsplit
from uuid import UUID

import certifi
import idna
import urllib3
from requests.utils import (
    get_auth_from_url,
    get_environ_proxies,
    prepend_scheme_if_needed,
    select_proxy,
)

if TYPE_CHECKING:
    from urllib3.response import BaseHTTPResponse

    from tracer.models.attachment_storage import AttachmentStorageBinding
    from tracer.services.attachments.interfaces import StorageOperation


_ENDPOINT_ERROR = "Storage endpoint must be a host[:port] or an HTTP(S) URL"


def _split_endpoint(raw_endpoint: str):
    """(urlsplit result, whether a scheme was given) for an endpoint setting.

    Surrounding whitespace is not part of the setting: a value read from a
    secret file or a YAML block scalar ends in a newline.
    """
    endpoint = raw_endpoint.strip() if isinstance(raw_endpoint, str) else ""
    if not endpoint or any(c.isspace() for c in endpoint):
        raise ValueError(_ENDPOINT_ERROR)
    has_scheme = "://" in endpoint
    return urlsplit(endpoint if has_scheme else "//" + endpoint), has_scheme


def _parse_endpoint(raw_endpoint: str) -> tuple[str, bool | None]:
    """Parse a storage authority, optionally prefixed by HTTP(S).

    A bare host:port is not a URL scheme. Only the authority is used: anything
    after it (a trailing slash, a path prefix, a query or fragment) is
    ignored, as it always was. Minio addresses buckets at the endpoint's root,
    and generated object URLs are path-style at the root of MINIO_URL's
    authority, the shape tfc.utils.storage.is_own_storage_url recognises.
    Reject what cannot name a usable authority (an empty host, credentials,
    a scheme other than HTTP(S), a bad port) rather than deferring it to the
    first network request. Never include configuration in the error.
    """
    error = _ENDPOINT_ERROR
    try:
        parsed, has_scheme = _split_endpoint(raw_endpoint)
        if (
            (has_scheme and parsed.scheme not in ("http", "https"))
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.netloc.endswith(":")
            or parsed.port == 0
        ):
            raise ValueError(error)
        host = parsed.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
            host = f"[{host}]"
        else:
            # Public MinIO hosts may be internationalized even when the SDK
            # connects to an ASCII internal endpoint. Validate the IDNA form;
            # the SDK connection and server_reachable_url's host comparison use
            # it. Generated object URLs keep the configured form instead (see
            # _configured_authority).
            if not host.isascii():
                host = idna.encode(host, uts46=True).decode("ascii")
            if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-._" for c in host):
                raise ValueError(error)
            if not host.strip("."):
                raise ValueError(error)
    except ValueError:
        raise ValueError(error) from None
    authority = f"{host}:{parsed.port}" if parsed.port is not None else host
    return authority, parsed.scheme == "https" if has_scheme else None


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _environment_proxy(url: str) -> str | None:
    """The proxy the environment names for url, or None to connect directly.

    It is picked the way requests picks it: HTTPS_PROXY or HTTP_PROXY by the
    URL's scheme, then ALL_PROXY, in either case. The request goes direct when
    NO_PROXY names its host or a domain suffix of it (optionally with its
    port), its IPv4 address or a CIDR range holding it, or is exactly "*".
    Unlike requests, loopback is always direct, as in Go's proxy selection:
    no proxy can reach this machine's loopback, where Standalone runs MinIO.
    """
    if _is_loopback(urlparse(url).hostname):
        return None
    proxy = select_proxy(url, get_environ_proxies(url))
    return prepend_scheme_if_needed(proxy, "http") if proxy else None


class _EnvironmentProxyPoolManager(urllib3.PoolManager):
    """Sends each request through the proxy the environment names for its URL.

    Minio's own transport ignores HTTP_PROXY and NO_PROXY. The choice is made
    per request because Minio does not always connect to the configured
    endpoint: on AWS it connects to <bucket>.s3.<region>.amazonaws.com, and
    NO_PROXY has to match that host, as it does for requests.
    """

    def __init__(self, **connection_pool_kw):
        super().__init__(**connection_pool_kw)
        self._through: dict[str, urllib3.ProxyManager] = {}

    def urlopen(  # type: ignore[override]  # Match PoolManager; its RequestMethods ancestor has a different positional API.
        self, method: str, url: str, redirect: bool = True, **kw: Any
    ) -> BaseHTTPResponse:
        proxy = _environment_proxy(url)
        if proxy is None:
            return super().urlopen(method, url, redirect=redirect, **kw)
        return self._proxy_manager(proxy).urlopen(method, url, redirect=redirect, **kw)

    def _proxy_manager(self, proxy: str) -> urllib3.ProxyManager:
        if proxy not in self._through:
            username, password = get_auth_from_url(proxy)
            auth = f"{username}:{password}" if username else None
            # setdefault, so threads racing on the first request share one.
            self._through.setdefault(
                proxy,
                urllib3.ProxyManager(
                    proxy,
                    proxy_headers=urllib3.make_headers(proxy_basic_auth=auth)
                    if auth is not None
                    else None,
                    **self.connection_pool_kw,
                ),
            )
        return self._through[proxy]

    def clear(self):
        super().clear()
        for manager in self._through.values():
            manager.clear()


def storage_http_client(
    *,
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    ca_certs: str | None = None,
) -> urllib3.PoolManager:
    """Shared proxy/CA delivery; callers must choose their own operation bounds.

    Ordinary media preserves Minio's 300-second / five-retry defaults in its
    wrapper. Private attachment adapters must supply bounded transport values;
    this helper neither constructs a storage client nor verifies a bucket.
    """
    return _EnvironmentProxyPoolManager(
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        maxsize=10,
        cert_reqs="CERT_REQUIRED",
        ca_certs=ca_certs or os.environ.get("SSL_CERT_FILE") or certifi.where(),
        retries=urllib3.Retry(
            total=retries, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
        ),
    )


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    endpoint: str
    secure: bool
    region: str
    ca_certs: str

    @property
    def endpoint_url(self) -> str:
        return f"{'https' if self.secure else 'http'}://{self.endpoint}"


@dataclass(frozen=True)
class StorageCredentials:
    # Never put credentials in repr/logging or a persisted resource binding.
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AttachmentStorageConfig:
    storage: StorageConfig
    quarantine_bucket: str
    serving_bucket: str
    quarantine_credentials: StorageCredentials
    serving_credentials: StorageCredentials


class StorageConfigurationError(ValueError):
    """A content-free configuration failure, not a native readiness verdict."""


def resolve_storage_config(
    *,
    backend: str | None = None,
    region: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> StorageConfig:
    """Resolve existing provider selection without constructing a client.

    GCS keeps its fixed XML endpoint and MINIO_REGION/auto. S3/MinIO preserve
    S3_ENDPOINT > S3_ENDPOINT_URL > s3.amazonaws.com, S3_SECURE > URL scheme >
    provider default, and S3_REGION > AWS_DEFAULT_REGION. No MINIO_URL or
    botocore ambient endpoint/profile setting participates in this selection.
    The media client still owns its historical cached backend selection.
    """
    env = os.environ if environ is None else environ
    backend = env.get("STORAGE_BACKEND", "s3").lower() if backend is None else backend
    if backend == "gcs":
        host, secure = "storage.googleapis.com", True
        selected_region = env.get("MINIO_REGION") or "auto"
    else:
        endpoint = env.get("S3_ENDPOINT") or env.get(
            "S3_ENDPOINT_URL", "s3.amazonaws.com"
        )
        host, scheme_hint = _parse_endpoint(endpoint)
        secure_env = env.get("S3_SECURE")
        if secure_env is not None:
            secure = secure_env.lower() == "true"
        elif scheme_hint is not None:
            secure = scheme_hint
        else:
            secure = backend == "s3"
        selected_region = env.get("S3_REGION") or env.get("AWS_DEFAULT_REGION", "")
    return StorageConfig(
        backend=backend,
        endpoint=host,
        secure=secure,
        region=selected_region if region is None else region,
        ca_certs=env.get("SSL_CERT_FILE") or certifi.where(),
    )


def resolve_media_credentials(
    backend: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> StorageCredentials:
    """Ordinary-media credentials ONLY; never an attachment fallback."""
    env = os.environ if environ is None else environ
    if backend == "gcs":
        return StorageCredentials(
            env.get("GCS_HMAC_ACCESS_KEY", ""), env.get("GCS_HMAC_SECRET_KEY", "")
        )
    return StorageCredentials(
        env.get("S3_ACCESS_KEY") or env.get("AWS_ACCESS_KEY_ID", ""),
        env.get("S3_SECRET_KEY") or env.get("AWS_SECRET_ACCESS_KEY", ""),
    )


# Named references may use existing Secret delivery, but must name dedicated
# purpose credentials, not the known public-media/root credential variables.
_SHARED_CREDENTIAL_VARIABLES = frozenset(
    {
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GCS_HMAC_ACCESS_KEY",
        "GCS_HMAC_SECRET_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    }
)
_CREDENTIAL_PARTS = ("access_key", "secret_key", "session_token")
_PURPOSES = ("quarantine", "serving")


def _attachment_credential(
    config: Mapping[str, str | None],
    key: str,
    env: Mapping[str, str],
    *,
    required: bool,
) -> str | None:
    direct = config.get(key, "")
    reference = config.get(f"{key}_env", "")
    if direct and reference:
        raise StorageConfigurationError("attachment_storage_credentials_ambiguous")
    if reference:
        if not isinstance(reference, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", reference
        ):
            raise StorageConfigurationError(
                "attachment_storage_credential_reference_invalid"
            )
        if reference in _SHARED_CREDENTIAL_VARIABLES:
            raise StorageConfigurationError(
                "attachment_storage_credential_reference_shared"
            )
        value = env.get(reference)
        # Optional means the reference itself may be absent, NOT that a
        # declared session-token reference may silently resolve to nothing.
        if not value:
            raise StorageConfigurationError(
                "attachment_storage_credential_reference_missing"
            )
        return value
    if required and not direct:
        raise StorageConfigurationError("attachment_storage_credentials_missing")
    return direct or None


def _storage_credentials(values: Mapping[str, str | None]) -> StorageCredentials:
    access_key, secret_key = values["access_key"], values["secret_key"]
    # _attachment_credential has already required both; only the token is optional.
    assert access_key is not None and secret_key is not None
    return StorageCredentials(access_key, secret_key, values["session_token"])


def resolve_attachment_storage_config(
    config: Mapping[str, str | None],
    *,
    enabled: bool = True,
    environ: Mapping[str, str] | None = None,
) -> AttachmentStorageConfig | None:
    """Activate a private config without clients, network I/O or posture claims.

    A legacy backend override must agree with STORAGE_BACKEND (default s3).
    The existing attachment region override is retained; otherwise inherit the
    provider's shared region. Buckets and credentials are NEVER inherited from
    ordinary media. Each purpose credential accepts its existing direct value
    OR an explicit *_env reference, never both. No ambient credential chain.
    Disabled callers return before inspecting even malformed unused config.
    """
    if not enabled:
        return None
    env = os.environ if environ is None else environ
    _validate_posture_reference(config.get("posture_credential_ref"))
    shared_backend = env.get("STORAGE_BACKEND", "s3").lower()
    backend = config.get("backend") or shared_backend
    if backend not in ("s3", "gcs", "minio"):
        raise StorageConfigurationError("attachment_storage_backend_invalid")
    if backend != shared_backend:
        raise StorageConfigurationError("attachment_storage_backend_mismatch")
    storage = resolve_storage_config(
        backend=backend,
        region=config.get("region") or None,
        environ=env,
    )
    if (
        not storage.region
        or not (quarantine_bucket := config.get("quarantine_bucket"))
        or not (serving_bucket := config.get("serving_bucket"))
    ):
        raise StorageConfigurationError("attachment_storage_configuration_missing")
    credentials = {
        purpose: _storage_credentials(
            {
                part: _attachment_credential(
                    config, f"{purpose}_{part}", env, required=part != "session_token"
                )
                for part in _CREDENTIAL_PARTS
            }
        )
        for purpose in _PURPOSES
    }
    return AttachmentStorageConfig(
        storage=storage,
        quarantine_bucket=quarantine_bucket,
        serving_bucket=serving_bucket,
        quarantine_credentials=credentials["quarantine"],
        serving_credentials=credentials["serving"],
    )


def attachment_storage_settings(
    *,
    enabled: bool,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Django settings bridge; keep legacy mapping keys and resolve only if on.

    ``resolved`` is the immutable adapter handoff when enabled, or None when
    disabled. The flat keys preserve existing explicit attachment consumers.
    Reading the settings module while disabled does not parse endpoints or
    dereference credentials, nor import the public-media client module.
    """
    env = os.environ if environ is None else environ
    config = {
        "backend": env.get("FI_ATTACHMENTS_STORAGE_BACKEND")
        or env.get("STORAGE_BACKEND", "s3").lower(),
        "region": env.get("FI_ATTACHMENTS_STORAGE_REGION", ""),
        # Symbolic runtime-object selector, never a token, path or ADC input.
        "posture_credential_ref": env.get("FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF")
        or None,
        **{
            f"{purpose}_bucket": env.get(f"FI_ATTACHMENTS_{purpose.upper()}_BUCKET", "")
            for purpose in _PURPOSES
        },
    }
    for purpose in _PURPOSES:
        for part in _CREDENTIAL_PARTS:
            key = f"{purpose}_{part}"
            config[key] = env.get(f"FI_ATTACHMENTS_{key.upper()}", "")
            config[f"{key}_env"] = env.get(f"FI_ATTACHMENTS_{key.upper()}_ENV", "")
    resolved = resolve_attachment_storage_config(config, enabled=enabled, environ=env)
    if resolved is not None:
        config["backend"] = resolved.storage.backend
        config["region"] = resolved.storage.region
        for purpose in _PURPOSES:
            credentials = getattr(resolved, f"{purpose}_credentials")
            for part in _CREDENTIAL_PARTS:
                key = f"{purpose}_{part}"
                config[key] = getattr(credentials, part) or ""
                # References are already resolved. Do not represent two
                # simultaneous credential sources in this legacy flat view.
                config.pop(f"{key}_env")
    return {**config, "resolved": resolved}


# Historical access is deliberately separate from new-admission selectors.
# No model, client or credential discovery is imported until explicitly used.


@dataclass(frozen=True)
class CredentialRefs:
    access_key_ref: str = field(repr=False)
    secret_key_ref: str = field(repr=False)
    token_ref: str | None = field(default=None, repr=False)

    def __post_init__(self):
        for reference in (self.access_key_ref, self.secret_key_ref):
            _validate_data_reference(reference)
        if self.token_ref is not None:
            _validate_data_reference(self.token_ref)


@dataclass(frozen=True)
class BindingAccessConfig:
    binding_id: UUID
    identity_sha256: str
    quarantine_credentials: CredentialRefs
    serving_credentials: CredentialRefs
    posture_credential_ref: str | None = field(repr=False)
    transport: StorageConfig

    def __post_init__(self):
        if not isinstance(self.binding_id, UUID) or not _is_digest(
            self.identity_sha256
        ):
            raise StorageConfigurationError(
                "attachment_storage_binding_identity_invalid"
            )
        if not all(
            isinstance(refs, CredentialRefs)
            for refs in (self.quarantine_credentials, self.serving_credentials)
        ) or not isinstance(self.transport, StorageConfig):
            raise StorageConfigurationError("attachment_storage_binding_access_invalid")
        _validate_posture_reference(self.posture_credential_ref)


def _validate_posture_reference(reference):
    if reference is not None and (
        not isinstance(reference, str)
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,254}", reference)
    ):
        raise StorageConfigurationError("attachment_storage_posture_reference_invalid")


def _is_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_data_reference(reference):
    if not isinstance(reference, str) or not re.fullmatch(
        r"[A-Z_][A-Z0-9_]*", reference
    ):
        raise StorageConfigurationError(
            "attachment_storage_credential_reference_invalid"
        )
    if reference in _SHARED_CREDENTIAL_VARIABLES:
        raise StorageConfigurationError(
            "attachment_storage_credential_reference_shared"
        )


def _binding_schema():
    # The schema lane owns these fields and the canonical digest algorithm.
    # Missing schema integration is a dependency failure, never a fallback.
    try:
        from tracer.models import attachment_storage
    except ImportError:
        raise StorageConfigurationError(
            "attachment_storage_binding_schema_unavailable"
        ) from None
    return attachment_storage


def _binding_values(binding, schema):
    try:
        values = {
            name: getattr(binding, name) for name in schema.BINDING_IDENTITY_FIELDS
        }
        if (
            not isinstance(binding.id, UUID)
            or not _is_digest(binding.identity_sha256)
            or schema.binding_identity_sha256(values) != binding.identity_sha256
            or values["provider"] not in ("s3", "gcs", "minio")
            or values["quarantine_bucket"] == values["serving_bucket"]
            or values["quarantine_resource_identity"]
            == values["serving_resource_identity"]
        ):
            raise ValueError
        for name, value in values.items():
            if name.endswith("created_at"):
                continue  # The canonical helper checks aware incarnation times.
            if not isinstance(value, str) or (
                not value and not name.endswith("encryption_key_identity")
            ):
                raise ValueError
        return values
    except Exception:  # noqa: BLE001 - configuration boundary, fails closed
        raise StorageConfigurationError(
            "attachment_storage_binding_identity_invalid"
        ) from None


def _read_binding(binding_id, schema):
    if not isinstance(binding_id, UUID):
        raise StorageConfigurationError("attachment_storage_binding_identity_invalid")
    try:
        binding = schema.AttachmentStorageBinding.objects.get(pk=binding_id)
    except Exception:  # noqa: BLE001 - configuration boundary, fails closed
        # Do not expose ORM parameters, provider configuration or raw errors.
        raise StorageConfigurationError(
            "attachment_storage_binding_unavailable"
        ) from None
    if binding.id != binding_id:
        raise StorageConfigurationError("attachment_storage_binding_identity_mismatch")
    return binding, _binding_values(binding, schema)


def _binding_transport(values, *, ca_certs):
    try:
        endpoint = values["endpoint"]
        host, secure = _parse_endpoint(endpoint)
        # Ordinary media deliberately tolerates paths; persisted bindings do not.
        # Compare the canonical origin, including a nondefault explicit port.
        if (
            not secure
            or endpoint != f"https://{host}"
            or urlsplit(endpoint).port == 443
        ):
            raise ValueError
        if not isinstance(ca_certs, str) or not ca_certs:
            raise ValueError
    except (TypeError, ValueError):
        raise StorageConfigurationError(
            "attachment_storage_binding_transport_invalid"
        ) from None
    return StorageConfig(values["provider"], host, True, values["region"], ca_certs)


def _credential_refs(mapping):
    if (
        not isinstance(mapping, Mapping)
        or not {"access_key_ref", "secret_key_ref"} <= mapping.keys()
        or not mapping.keys() <= {"access_key_ref", "secret_key_ref", "token_ref"}
    ):
        raise StorageConfigurationError("attachment_storage_binding_access_invalid")
    return CredentialRefs(
        access_key_ref=mapping["access_key_ref"],
        secret_key_ref=mapping["secret_key_ref"],
        token_ref=mapping.get("token_ref"),
    )


def attachment_binding_access_settings(
    *, environ: Mapping[str, str] | None = None
) -> dict:
    """Decode explicit reference delivery only when historical access is used.

    Values contain names, never credential values. Validation is per selected
    binding, so an unrelated malformed entry cannot block a valid old binding.
    """
    env = os.environ if environ is None else environ
    try:

        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        result = json.loads(
            env.get("FI_ATTACHMENTS_BINDING_ACCESS", "{}"),
            object_pairs_hook=unique_pairs,
        )
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise StorageConfigurationError(
            "attachment_storage_binding_access_invalid"
        ) from None


def resolve_attachment_binding_config(
    binding_id: UUID, *, operation: StorageOperation, settings: object
) -> BindingAccessConfig:
    """Resolve historical identity and reference names, NOT provider readiness.

    Only the persisted tuple and its digest select access. Current storage,
    bucket, CDN, decoder and source-key settings are never consulted. Secrets
    remain unresolved until materialization. A posture reference is a runtime
    object name, not a data-credential environment variable.
    """
    if operation not in ("admit", "write", "read", "erase"):
        raise StorageConfigurationError("attachment_storage_operation_invalid")
    schema = _binding_schema()
    binding, values = _read_binding(binding_id, schema)
    access = getattr(settings, "FI_ATTACHMENTS_BINDING_ACCESS", None)
    if not isinstance(access, Mapping):
        raise StorageConfigurationError("attachment_storage_binding_access_invalid")
    mapping = access.get(binding.identity_sha256)
    if mapping is None:
        raise StorageConfigurationError("attachment_storage_binding_access_missing")
    if (
        not isinstance(mapping, Mapping)
        or not {"quarantine", "serving"} <= mapping.keys()
        or not mapping.keys() <= {"quarantine", "serving", "posture_credential_ref"}
    ):
        raise StorageConfigurationError("attachment_storage_binding_access_invalid")
    return BindingAccessConfig(
        binding_id=binding_id,
        identity_sha256=binding.identity_sha256,
        quarantine_credentials=_credential_refs(mapping["quarantine"]),
        serving_credentials=_credential_refs(mapping["serving"]),
        posture_credential_ref=mapping.get("posture_credential_ref"),
        transport=_binding_transport(
            values, ca_certs=os.environ.get("SSL_CERT_FILE") or certifi.where()
        ),
    )


def _materialize_credentials(
    refs: CredentialRefs, env: Mapping[str, str]
) -> StorageCredentials:
    # Reuse the existing purpose-credential validator, including missing-token
    # and shared/root-reference rejection. Never call the default resolver.
    references = {
        "access_key_env": refs.access_key_ref,
        "secret_key_env": refs.secret_key_ref,
        "session_token_env": refs.token_ref,
    }
    values = {}
    for part in _CREDENTIAL_PARTS:
        value = _attachment_credential(
            references, part, env, required=part != "session_token"
        )
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise StorageConfigurationError("attachment_storage_credentials_invalid")
        values[part] = value
    return _storage_credentials(values)


def materialize_attachment_binding_config(
    *,
    binding: AttachmentStorageBinding,
    config: BindingAccessConfig,
    environ: Mapping[str, str] | None = None,
) -> AttachmentStorageConfig:
    """Factory seam for keyword-only adapter construction with binding+config.

    Recheck both digest and COMPLETE persisted tuple against the supplied
    binding, including incarnation/encryption fields. Do not mutate either.
    Only explicitly named data refs are dereferenced; no ambient credentials,
    public-media values or current bucket/provider selectors participate.
    """
    if not isinstance(config, BindingAccessConfig):
        raise StorageConfigurationError("attachment_storage_binding_access_invalid")
    schema = _binding_schema()
    supplied = _binding_values(binding, schema)
    persisted, values = _read_binding(config.binding_id, schema)
    if (
        binding.id != config.binding_id
        or binding.identity_sha256 != config.identity_sha256
        or persisted.identity_sha256 != config.identity_sha256
        or supplied != values
        or config.transport
        != _binding_transport(values, ca_certs=config.transport.ca_certs)
    ):
        raise StorageConfigurationError("attachment_storage_binding_identity_mismatch")
    env = os.environ if environ is None else environ
    return AttachmentStorageConfig(
        storage=config.transport,
        quarantine_bucket=values["quarantine_bucket"],
        serving_bucket=values["serving_bucket"],
        quarantine_credentials=_materialize_credentials(
            config.quarantine_credentials, env
        ),
        serving_credentials=_materialize_credentials(config.serving_credentials, env),
    )


_PostureCredentials = TypeVar("_PostureCredentials")


def resolve_attachment_posture_credentials(
    config: BindingAccessConfig,
    *,
    supplied: Mapping[str, object],
    expected_type: type[_PostureCredentials],
) -> _PostureCredentials | None:
    """Select an already-supplied, typed runtime object; never create/discover it.

    No reference returns None; a named object must exist and match its type.
    Data env values, paths, dicts and credential constructors are not runtime
    posture objects. A match is NOT native readiness evidence.
    """
    if not isinstance(config, BindingAccessConfig):
        raise StorageConfigurationError("attachment_storage_binding_access_invalid")
    if config.posture_credential_ref is None:
        return None
    if (
        not isinstance(supplied, Mapping)
        or config.posture_credential_ref not in supplied
    ):
        raise StorageConfigurationError("attachment_storage_posture_reference_missing")
    credentials = supplied[config.posture_credential_ref]
    if (
        not isinstance(expected_type, type)
        or expected_type is object
        or not isinstance(credentials, expected_type)
        or isinstance(
            credentials,
            str | bytes | bytearray | Mapping | os.PathLike | type | int | float | bool,
        )
        or credentials is None
        or callable(credentials)
    ):
        raise StorageConfigurationError(
            "attachment_storage_posture_credentials_invalid"
        )
    return credentials
