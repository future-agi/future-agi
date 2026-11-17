"""Short-lived host assertions. This module has no Django or database dependency."""

import base64
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AUDIENCE = "omega-squad"
ASSERTION_PREFIX = "omega_delegate_"
# Includes the prefix, header, payload, separators and RSA signature; matches the gateway.
MAX_ASSERTION_LENGTH = 8192
LIFETIME_SECONDS = 45
# The most one-use keys one exchange call may mint (CP P7, D2).
MAX_BATCH = 8
# Omega EE allows no clock skew on `iat`: an assertion whose `iat` is ahead of EE's clock by any amount is
# refused. A minting host a fraction of a second ahead of EE (a container VM, NTP jitter) then has some
# assertions refused at random. Fresh assertions are dated this far back; `exp - iat` stays LIFETIME_SECONDS.
CLOCK_SKEW_MARGIN_SECONDS = 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}\Z", re.ASCII)
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_MAX_KEY_BYTES = 16384
# A Kubernetes Secret volume (READINESS L09): the kubelet's atomic writer presents each key as
# <dir>/<name> -> ..data/<name>, with ..data -> ..<timestamp>, and the files are owned by root with
# the pod's fsGroup as their group (mode 0440 at most when the chart sets defaultMode 0440).
_SECRET_VOLUME_OWNER_UID = 0
_SECRET_VOLUME_DATA_LINK = "..data"
# The hosted products a key may name (omega_rollout.products) and their states. Kept literal here: this
# module has no Django dependency, and the claim is part of the assertion contract EE and the gateway read.
_CLAIM_PRODUCTS = frozenset(
    {"console", "sandboxes", "deployments", "studio_tab", "hub"}
)
_CLAIM_STATES = frozenset({"active", "paused"})
# How the caller proved who they are (SB W2-KEYS; the contract EE reads, W2-ACCESS, is in
# `.omega/program/SB-W2-KEYS/CLAIMS.md`). Every assertion names one, with the credential it binds
# to (D2): `sid`, the browser access token's id, for "session"; `key_id`, the API key's id, for
# "api_key". A verifier treats a missing or unknown method as "not a session" (fail closed), so
# human-session-only verbs (approvals, sharing, settings) refuse an API key.
AUTH_METHODS = {"session": "sid", "api_key": "key_id"}


class DelegationUnavailable(ValueError):
    """Missing or invalid operator configuration. Never include secret material."""


def _secret_volume_target(key_file):
    """The file a Kubernetes Secret volume links `key_file` to, or ValueError for any other symlink.

    Only the atomic writer's exact layout is followed: `<dir>/<name>` links to `..data/<name>`,
    `<dir>/..data` links to a `..<timestamp>` directory beside it, and that directory is not
    writable by group or other users. Any other link (an absolute target, another directory,
    another name, a chain) is refused, so a key file cannot be swapped by planting a link.
    """
    directory, name = os.path.split(key_file)
    if os.readlink(key_file) != os.path.join(_SECRET_VOLUME_DATA_LINK, name):
        raise ValueError("Invalid key file")
    snapshot = os.readlink(os.path.join(directory, _SECRET_VOLUME_DATA_LINK))
    if (
        not snapshot.startswith("..")
        or os.sep in snapshot
        or snapshot in ("..", _SECRET_VOLUME_DATA_LINK)
    ):
        raise ValueError("Invalid key file")
    snapshot_dir = os.path.join(directory, snapshot)
    info = os.lstat(snapshot_dir)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_mode & 0o022
        or info.st_uid not in (_SECRET_VOLUME_OWNER_UID, os.geteuid())
    ):
        raise ValueError("Invalid key file")
    return os.path.join(snapshot_dir, name)


def _key_file_is_private(info, euid, groups):
    """A regular file only this process can read: owned by its uid with no group or other bits, or a
    Secret volume file owned by root, readable through one of its groups (the fsGroup), nothing else.
    """
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_KEY_BYTES:
        return False
    if info.st_uid == euid:
        return not info.st_mode & 0o077
    return (
        info.st_uid == _SECRET_VOLUME_OWNER_UID
        and info.st_gid in groups
        and not info.st_mode & 0o037
    )


def delegation_role(org_level, workspace_level):
    """Keep organization authority as a cap; never promote workspace admins."""
    if org_level == 15:
        return "owner"
    if org_level == 8:
        return "admin"
    if org_level == 3 and workspace_level in (3, 8):
        return "member"
    # Viewers have no equally restricted counterpart in Omega's role vocabulary.
    raise ValueError("No compatible delegation role")


def emergency_authority(org_level, workspace_level, auth_method):
    """Separate, session-only grant for the emergency routes; never changes the delegated role."""
    if auth_method != "session":
        return None
    if org_level in (15, 8):
        return "organization"
    if org_level == 3 and workspace_level == 8:
        return "workspace"
    return None


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_segment(value):
    # JWS carries UTF-8 JSON. ASCII escaping triples supplementary-Unicode labels.
    return _base64url(
        json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _bounded_claims_segment(claims, display_claims, budget):
    payload = _json_segment({**claims, **display_claims})
    if len(payload) <= budget:
        return payload
    # Identity, authority and replay claims never yield to optional display metadata.
    # Only unusually large issuer/identity/key configurations need shorter labels.
    # Check actual encoded lengths (including escaping and ellipses), not character counts.
    for length in range(max(map(len, display_claims.values()), default=0) - 1, -1, -1):
        names = (
            {
                key: value if len(value) <= length else value[:length] + "…"
                for key, value in display_claims.items()
            }
            if length
            else {}
        )
        payload = _json_segment({**claims, **names})
        if len(payload) <= budget:
            return payload
    raise DelegationUnavailable("Omega delegation is unavailable")


@dataclass(frozen=True)
class HostAssertionSigner:
    issuer: str
    key_id: str
    private_key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        issuer = environ.get("OMEGA_SQUAD_DELEGATION_ISSUER", "")
        key_id = environ.get("OMEGA_SQUAD_DELEGATION_KEY_ID", "")
        key_file = environ.get("OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE", "")
        try:
            parsed = urlsplit(issuer)
            # Accessing .port rejects malformed and out-of-range configured ports.
            _ = parsed.port
            if (
                len(issuer) > 2048
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(char.isspace() for char in issuer)
                or not _KEY_ID.fullmatch(key_id)
                or not os.path.isabs(key_file)
            ):
                raise ValueError("Invalid configuration")
            # A symlink is followed only in a Kubernetes Secret volume's layout. O_NOFOLLOW on the
            # resolved file prevents key-file substitution; fstat checks the opened inode.
            if os.path.islink(key_file):
                key_file = _secret_volume_target(key_file)
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(key_file, flags), "rb") as handle:
                info = os.fstat(handle.fileno())
                groups = {os.getegid(), *os.getgroups()}
                if not _key_file_is_private(info, os.geteuid(), groups):
                    raise ValueError("Invalid key file")
                material = handle.read(_MAX_KEY_BYTES + 1)
            if len(material) > _MAX_KEY_BYTES:
                raise ValueError("Invalid key file")
            key = serialization.load_pem_private_key(material, password=None)
            if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
                raise ValueError("Invalid signing key")
            return cls(issuer=issuer, key_id=key_id, private_key=key)
        except (OSError, TypeError, ValueError, UnsupportedAlgorithm) as exc:
            raise DelegationUnavailable("Omega delegation is unavailable") from exc

    def issue(
        self,
        *,
        subject,
        organization_id,
        workspace_id,
        role,
        audience,
        now=None,
        auth_method,
        credential_id,
        products=None,
        rollout_epoch=None,
        policy_workspace_admin=False,
        emergency_control=None,
        organization_name=None,
        workspace_name=None,
    ):
        if audience != AUDIENCE:
            raise ValueError("Unsupported audience")
        if not all(
            isinstance(value, str) and _ID.fullmatch(value)
            for value in (subject, organization_id, workspace_id)
        ) or role not in ("owner", "admin", "member"):
            raise ValueError("Invalid delegation identity")
        binding_claim = (
            AUTH_METHODS.get(auth_method) if isinstance(auth_method, str) else None
        )
        if binding_claim is None or not (
            isinstance(credential_id, str) and _ID.fullmatch(credential_id)
        ):
            raise ValueError("Invalid authentication method")
        issued_at = int(time.time()) - CLOCK_SKEW_MARGIN_SECONDS if now is None else now
        if type(issued_at) is not int or issued_at < 0:
            raise ValueError("Invalid delegation timestamp")
        if emergency_control is not None and (
            auth_method != "session"
            or emergency_control not in ("workspace", "organization")
            or (emergency_control == "organization" and role not in ("owner", "admin"))
        ):
            raise ValueError("Invalid emergency authority")
        display_claims = {}
        for key, value in (
            ("org_name", organization_name),
            ("workspace_name", workspace_name),
        ):
            if value is not None:
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > 256
                    or re.search(r"[\x00-\x1f\x7f]", value)
                ):
                    raise ValueError("Invalid scope display name")
                display_claims[key] = value
        rollout_claims = _rollout_claims(products, rollout_epoch)
        claims = {
            "iss": self.issuer,
            "aud": AUDIENCE,
            "sub": subject,
            "org_id": organization_id,
            "workspace_id": workspace_id,
            "role": role,
            "iat": issued_at,
            "exp": issued_at + LIFETIME_SECONDS,
            "jti": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "auth_method": auth_method,
            binding_claim: credential_id,
            **rollout_claims,
            **({"emergency_control": emergency_control} if emergency_control else {}),
        }
        if type(policy_workspace_admin) is not bool:
            raise ValueError("Invalid workspace policy grant")
        if policy_workspace_admin and auth_method == "session":
            claims["tool_policy_workspace_admin"] = True
        header = _json_segment({"alg": "RS256", "typ": "JWT", "kid": self.key_id})
        signature_bytes = (self.private_key.key_size + 7) // 8
        signature_length = (4 * signature_bytes + 2) // 3
        payload_budget = (
            MAX_ASSERTION_LENGTH
            - len(ASSERTION_PREFIX)
            - len(header)
            - signature_length
            - 2
        )
        signing_input = (
            header
            + "."
            + _bounded_claims_segment(claims, display_claims, payload_budget)
        )
        signature = self.private_key.sign(
            signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        access_token = ASSERTION_PREFIX + signing_input + "." + _base64url(signature)
        if len(access_token) > MAX_ASSERTION_LENGTH:
            raise DelegationUnavailable("Omega delegation is unavailable")
        return {
            "accessToken": access_token,
            "tokenType": "Bearer",
            "expiresIn": LIFETIME_SECONDS,
        }


def _rollout_claims(products, rollout_epoch):
    """`products` maps each hosted product the workspace is admitted to onto "active" or "paused";
    `rollout_epoch` is the rollout counter at mint time (READINESS 3.13). The gateway refuses a route
    whose product is absent (404) or paused (503); a key minted without them names no product.
    """
    if products is None and rollout_epoch is None:
        return {}
    if (
        not isinstance(products, dict)
        or not all(
            key in _CLAIM_PRODUCTS and value in _CLAIM_STATES
            for key, value in products.items()
        )
        or type(rollout_epoch) is not int
        or rollout_epoch < 0
    ):
        raise ValueError("Invalid rollout claims")
    return {
        "products": {key: products[key] for key in sorted(products)},
        "rollout_epoch": rollout_epoch,
    }
