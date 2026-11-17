"""Operator configuration of the FA -> Omega EE event channel. No Django dependency.

- ``OMEGA_HOST_EVENTS_URL``: EE's receiver, ``https://<ee>/host/events``. Plain ``http``
  only with ``OMEGA_HOST_EVENTS_ALLOW_HTTP=1`` (a private network or a local stack).
- ``OMEGA_HOST_EVENTS_SECRET_FILE``: an absolute path to the shared secret (at least 32
  bytes). The file rule is the issuer key's (``accounts.omega_session``): a regular file
  owned by this user with no group or other bits, or a Kubernetes Secret volume's file,
  owned by root, readable through one of this process's groups (the pod's fsGroup) and
  by nobody else. The only link followed is the kubelet's atomic-writer layout.
- ``OMEGA_HOST_EVENTS_KEY_ID``: which of EE's secrets this is, so it can be rotated.
- ``OMEGA_HOST_EVENTS_CA_FILE`` (optional): an absolute path to the PEM CA that signed EE's
  certificate (inside a cluster, a private CA such as omega-internal-ca). Not a secret. It is
  passed as ``verify=`` on the sender's own request only, so no other outbound HTTPS call of the
  process changes its trust. Unset: requests' default trust (``REQUESTS_CA_BUNDLE``, else certifi).
- ``OMEGA_SQUAD_DELEGATION_ISSUER``: the issuer the session exchange signs keys as.
  Events carry it, so EE applies them to the identities those keys created.

States:
- ``disabled``: Omega delegation is not configured here. Nothing is sent; undelivered
  rows age out after the retention.
- ``misconfigured``: delegation is on but the channel is not usable. Rows stay pending
  and every drain logs an error, because open streams then end only at the 15-minute cap.
- ``ready``.
"""

import os
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from accounts.host_events.signing import KEY_ID, MIN_SECRET_BYTES

# One rule for every secret file FA reads from a Kubernetes Secret volume: the issuer key's.
from accounts.omega_session import _key_file_is_private, _secret_volume_target

DISABLED = "disabled"
MISCONFIGURED = "misconfigured"
READY = "ready"
_MAX_SECRET_BYTES = 4096


@dataclass(frozen=True)
class EventChannelConfig:
    state: str
    issuer: str = ""
    url: str = ""
    key_id: str = ""
    secret: bytes = field(default=b"", repr=False)
    # The PEM CA EE's certificate is checked with, or "" for requests' default trust.
    ca_file: str = ""
    # A short code saying what is wrong when misconfigured; never a path or a secret.
    problem: str = ""


def _read_secret(path):
    if not os.path.isabs(path):
        raise ValueError("secret_path_not_absolute")
    try:
        # A symlink is followed only in a Kubernetes Secret volume's layout; any other link
        # is not followed, so the file is unreadable. O_NOFOLLOW on the resolved file
        # prevents substitution; fstat checks the opened inode.
        if os.path.islink(path):
            try:
                path = _secret_volume_target(path)
            except ValueError as exc:
                raise ValueError("secret_file_unreadable") from exc
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            groups = {os.getegid(), *os.getgroups()}
            if not _key_file_is_private(info, os.geteuid(), groups):
                raise ValueError("secret_file_unsafe")
            material = handle.read(_MAX_SECRET_BYTES + 1)
    except OSError as exc:
        raise ValueError("secret_file_unreadable") from exc
    secret = material.strip()
    if len(material) > _MAX_SECRET_BYTES or len(secret) < MIN_SECRET_BYTES:
        raise ValueError("secret_length")
    return secret


def _check_ca_file(path):
    """The CA file as given, once it loads as a trust store; "" when unset."""
    if not path:
        return ""
    if not os.path.isabs(path):
        raise ValueError("ca_path_not_absolute")
    try:
        # A missing file, a directory or a file with no certificate fails here, at start,
        # instead of every delivery failing its certificate check (ee_unreachable).
        ssl.create_default_context(cafile=path)
    except (OSError, ValueError) as exc:
        raise ValueError("ca_file_unreadable") from exc
    return path


def _check_url(url, allow_http):
    parsed = urlsplit(url)
    _ = parsed.port  # rejects malformed ports
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("url_invalid")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError("url_not_https")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("url_invalid")
    # EE serves only POST <base>/host/events; a bare EE base URL would send to nothing.
    if not parsed.path.endswith("/host/events"):
        raise ValueError("url_path_invalid")


def load_config(environ=None):
    environ = os.environ if environ is None else environ
    issuer = environ.get("OMEGA_SQUAD_DELEGATION_ISSUER", "").strip()
    url = environ.get("OMEGA_HOST_EVENTS_URL", "").strip()
    if not issuer and not url:
        return EventChannelConfig(state=DISABLED)
    try:
        if not issuer:
            raise ValueError("issuer_missing")
        if not url:
            raise ValueError("url_missing")
        _check_url(url, environ.get("OMEGA_HOST_EVENTS_ALLOW_HTTP", "") == "1")
        key_id = environ.get("OMEGA_HOST_EVENTS_KEY_ID", "").strip()
        if not KEY_ID.fullmatch(key_id):
            raise ValueError("key_id_invalid")
        secret = _read_secret(environ.get("OMEGA_HOST_EVENTS_SECRET_FILE", "").strip())
        ca_file = _check_ca_file(environ.get("OMEGA_HOST_EVENTS_CA_FILE", "").strip())
    except ValueError as exc:
        return EventChannelConfig(state=MISCONFIGURED, issuer=issuer, problem=str(exc))
    return EventChannelConfig(
        state=READY,
        issuer=issuer,
        url=url,
        key_id=key_id,
        secret=secret,
        ca_file=ca_file,
    )
