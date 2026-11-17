"""Move the MCP secrets stored inline in org configs into the MCP credential
vault (one MCP secret store: the credential vault).

On the release line an org config's ``mcp`` section held its secrets inline:
a server's ``auth.token`` or ``auth.key``, its ``credential.secret``, and a
managed tool's ``managed_tools.credentials.<fagi.id>.secret`` (under either
spelling of ``managed_tools``). NG-01 stored each one encrypted under
``enc::`` (the release writer, migrations 0047 and 0050); a row neither
reached holds it in clear. M14 keeps an MCP secret only in
``AgentccMCPCredential``, and the config only a reference to it,
``credential {mode, credential_id, state}``; the push adds the secret from the
vault. ``enc::`` stays for every other section.

This migration moves every such secret, in every version of every org
(active, older and soft-deleted), by the rules of
``agentcc_mcp_migrate_secrets --apply``, with three differences:

- It reads an ``enc::`` value as the clear value the release push sent (the
  vault never stores a value that starts with ``enc::``). A well-formed
  ``enc::`` token this deployment cannot decrypt, which that push already
  dropped, is kept as it was stored (see the rules).
- It keeps a value the vault does not accept, and a value under a key longer
  than the vault's ``server_id``, as a revoked credential with the reason in
  its label, and says so in its report, where the command removes it.
- It rewrites every version in place and pushes nothing: it writes no new
  version, and the gateway gets the same secrets from the vault on the next
  push or bulk sync. On the release line the org-saved MCP servers never
  connected (the tenant, vserver and meter hooks were no-ops there), so no
  live connection depends on the order.

Afterwards ``agentcc_mcp_migrate_secrets --check`` passes and ``--apply``
finds nothing to do. ``--check`` stays the deploy's gate: a version a pod
still running the release code writes during the rollout can hold a new
inline secret, and ``--apply`` moves it.

The rules, per org:

- Versions are read active first, then by ``deleted``, then newest first.
  The first usable secret seen for a key (a server id, or the ``fagi.<id>``
  of a managed entry) is the one stored. An older value it superseded is
  removed, not stored.
- A value the vault does not accept (one the gateway never sent: ``auth`` of
  type none, an api_key with no header, a value or header the vault's check
  refuses, a token this deployment cannot decrypt) is kept when it is the
  first value seen for its key, or newer than the one stored: stored as a
  revoked credential, which is never sent, holding the value as it was (a
  token that cannot be decrypted, as its stored ciphertext), with the reason
  and the key in its label, beside any credential the key has (one the
  active version already sends included). An older one is removed, as
  above.
- A key longer than the vault's ``server_id`` (``SERVER_ID_CHARS``) stores
  nothing: the first value seen for it is kept the same way, under a stable
  ``SERVER_ID_CHARS`` form of the key (its first 47 characters, ``~`` and 16
  hex of its SHA-256). A revoked credential is never looked up by server id,
  so nothing else needs that form.
- The migration prints one line per org that kept a value: the keys and the
  reasons, never a value, and the command that deletes them
  (``agentcc_mcp_migrate_secrets --remove-kept [--org <id>] --apply``; without
  --apply it counts them). Deleting the org deletes them with it (the
  credential's organization foreign key cascades).
- A secret is stored as an active credential when the active version's own
  entry holds it, and as a revoked one otherwise (its server was removed, or
  its auth was). An active shared credential the active version already
  references is kept, and the inline secret is dropped; an active one it
  does not reference is revoked first.
- Every entry that held a usable secret gets a reference to its key's
  credential (``state`` is the credential's status) in place of the secret.
  An entry whose secret was never usable, or whose key is too long to
  store, gets no reference (a kept credential included), so a server left
  without auth stays without auth.
- An org with MCP servers in a version created before the vault's migration
  (``prism.0035_mcp_credential``) was applied gets ``mcp.access.default:
  allow`` on its active version, unless it states a default (M10's
  compatibility rule).

The secret locations, the rules, the vault's check of a secret
(``mcp_credentials.new_secret`` for a header placement, with
``mcp_credential_placement``'s reserved headers) and the NG-01 reader are
copied here, not imported, so this migration keeps doing the same when the
live code changes. Historical models only.

One transaction per org (``atomic = False``), under the org's config lock
(``FOR NO KEY UPDATE`` on the organization row, as
``org_config_writer.lock_org`` takes it), so a config write on a pod still
running the release code waits for it, and a failure rolls back that org
alone; a re-run (idempotent) finishes the rest. A first pass reads the table
in primary-key batches of ``BATCH_SIZE``, without locks, to find the orgs
with anything to move. Volume: one row per saved config version; not measured
for this change (no production database was read).

Reverse: nothing. The references and the vault rows stay. The release code's
contract accepts a reference with no inline secret and reads it as a server
with no secret, and its org MCP servers never connected, so a rollback of the
code loses nothing that worked.
"""

import base64
import copy
import hashlib
import re

from django.db import DEFAULT_DB_ALIAS, connections, migrations, transaction
from django.db.migrations.recorder import MigrationRecorder

from agentcc.migrations import _integration_key_guard as key_guard
from tfc.migration_database import executor_alias_scope

ENCRYPTED_PREFIX = "enc::"
# Rows read per batch in the first (lock-free) pass.
BATCH_SIZE = 500
LABEL = "Moved from the org config"
KEPT_LABEL = "Kept from the org config, not accepted by the vault, never sent"
# The command that deletes the kept values (agentcc_mcp_migrate_secrets).
REMOVE_KEPT = "agentcc_mcp_migrate_secrets --remove-kept"
# agentcc_mcp_credential's server_id, header_name and label lengths.
SERVER_ID_CHARS = 64
HEADER_NAME_CHARS = 128
LABEL_CHARS = 255
# A longer key's stored form: this many of its characters, "~", 16 hex.
SHORT_KEY_PREFIX_CHARS = SERVER_ID_CHARS - 1 - 16
VAULT_MIGRATION = ("prism", "0035_mcp_credential")

SHARED = "shared"
ACTIVE, REVOKED = "active", "revoked"
BEARER, API_KEY = "bearer", "api_key"
STATIC_KINDS = (BEARER, API_KEY)
MANAGED_PREFIX = "fagi."
# mcp.managed_tools under each spelling the contract reads, managed_tools first.
MANAGED_TOOLS_KEYS = ("managed_tools", "managedTools")
# A reference's credential_id under each spelling the contract reads.
CREDENTIAL_ID_KEYS = ("credential_id", "credentialId", "credentialID")

# mcp_credentials.new_secret's checks of a header-placed secret.
MAX_SECRET_CHARS = 8192
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
# org_config_secrets' read mask (the stem alone, or the stem and a 16-hex
# fingerprint), which new_secret refuses as a value.
_READ_MASK = re.compile(r"__encrypted__(?:[0-9a-f]{16})?")
# Headers the gateway sets itself on an upstream hop (mcp_credential_placement).
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "expect",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-method",
        "mcp-name",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "traceparent",
        "tracestate",
    }
)
_RESERVED_HEADER_PREFIXES = ("mcp-", "x-agentcc-", "x-omega-")

_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_organization",
        "postgres:agentcc_mcp_credential",
        "postgres:agentcc_org_config",
        "postgres:django_migrations",
    ],
    "mitigation": (
        "Moves the MCP secrets held inline in the mcp section of agentcc_org_config "
        "rows (a server's auth token or key, its credential secret, managed-tool "
        "credential secrets) into agentcc_mcp_credential rows, and replaces each "
        "inline secret with a credential reference (mode, credential_id, state) in "
        "every version of the organization, writing no new version. A secret is stored "
        "active when the active version holds it and revoked otherwise; an active "
        "shared credential the active version already sends is kept. The active "
        "version of an organization whose versions held MCP servers before prism "
        "0035_mcp_credential was applied (time read from django_migrations) gets "
        "mcp.access.default allow unless it states a default. Finds the organizations "
        "with an unlocked primary-key scan, 500 rows per batch; then one transaction "
        "per organization (atomic=False) under FOR NO KEY UPDATE on its "
        "accounts_organization row and FOR UPDATE on its config rows. Replay-safe: "
        "after a run no version holds an inline secret and the access default is set, "
        "so a re-run writes nothing for that organization and creates no credential; a "
        "failure rolls back that organization alone. Reverse is a no-op. Row scale: "
        "one row per saved org-config version for every organization; at most two new "
        "credential rows per server or managed-tool id per organization: the stored "
        "secret, and a revoked one keeping a newer value the vault does not accept "
        "(or, for an id longer than server_id's 64 characters, one revoked row under a "
        "stable 64-character form of the id); each kept value is reported on stdout "
        "by id and reason, never by value, with the command that deletes them "
        "(agentcc_mcp_migrate_secrets --remove-kept --apply), and deleting the "
        "organization cascades to them. Needs a stable "
        "INTEGRATION_ENCRYPTION_KEY; refuses to start without one when there is "
        "anything to encrypt."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


# --- reading a stored leaf (NG-01) and encrypting a vault row --------------


def _fernet_shaped(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def _reader(CredentialManager):
    """``read(value)``: the clear value a stored leaf holds. ``enc::`` plus a
    blob that decrypts to ``{"v": ...}`` gives that value; a well-formed token
    that does not gives None (the push dropped it); anything else, one that
    merely starts with ``enc::`` included, is a clear value, as it is."""

    def read(value):
        if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
            return value
        raw = value[len(ENCRYPTED_PREFIX) :]
        try:
            data = CredentialManager.decrypt(raw.encode("utf-8"))
        except Exception:
            return None if _fernet_shaped(raw) else value
        if isinstance(data, dict) and "v" in data:
            return data["v"]
        return None

    return read


def _encrypter(CredentialManager):
    """``encrypt(value)``: a vault row's ``encrypted_secret``, as
    ``credential_manager.encrypt_token`` writes it."""

    def encrypt(value):
        blob = CredentialManager.encrypt({"v": value})
        text = blob.decode("utf-8") if isinstance(blob, bytes) else blob
        return ENCRYPTED_PREFIX + text

    return encrypt


# --- the vault's check of a secret (mcp_credentials) -----------------------


def _reserved_header(name):
    lowered = name.lower()
    return lowered in _RESERVED_HEADERS or lowered.startswith(_RESERVED_HEADER_PREFIXES)


def _usable(kind, value, header):
    """``(kind, value, header)`` when the vault would store this secret in a
    header (``new_secret``), else None."""
    if kind not in STATIC_KINDS:
        return None
    if not isinstance(value, str) or not value:
        return None
    if (
        value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or len(value) > MAX_SECRET_CHARS
        or value.startswith(ENCRYPTED_PREFIX)
        or _READ_MASK.fullmatch(value) is not None
    ):
        return None
    header = header or ""
    if kind == API_KEY and not header:
        return None
    if header and (
        not isinstance(header, str)
        or not _HEADER_NAME.fullmatch(header)
        or _reserved_header(header)
    ):
        return None
    return (kind, value, header)


def _refusal(kind, value, header):
    """Why ``_usable`` refuses this secret, in a few words and never the
    value, or None when it would not."""
    if kind not in STATIC_KINDS:
        return "an auth type the vault does not hold"
    if not isinstance(value, str):
        return "a value that is not text"
    if not value:
        return "an empty value"
    if value != value.strip():
        return "spaces around the value"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "a control character in the value"
    if len(value) > MAX_SECRET_CHARS:
        return f"a value longer than {MAX_SECRET_CHARS} characters"
    if value.startswith(ENCRYPTED_PREFIX):
        return f"a clear value that starts with {ENCRYPTED_PREFIX}"
    if _READ_MASK.fullmatch(value) is not None:
        return "the read mask, not a value"
    header = header or ""
    if kind == API_KEY and not header:
        return "an api_key with no header"
    if header and (not isinstance(header, str) or not _HEADER_NAME.fullmatch(header)):
        return "a header name the vault does not accept"
    if header and _reserved_header(header):
        return "a header the gateway sets itself"
    return None


def _held(entry, read):
    """``(kind, clear value, header, stored leaf)`` of the secret a server
    entry (or ``{"credential": managed entry}``) holds inline, read the way
    ``_legacy_secret`` reads it, or None when it holds none. The clear value
    is None for a stored ciphertext this deployment cannot read."""
    if not isinstance(entry, dict):
        return None
    credential = entry.get("credential")
    secret = credential.get("secret") if isinstance(credential, dict) else None
    if isinstance(secret, dict):
        stored = secret.get("value")
        return (secret.get("type"), read(stored), secret.get("header"), stored)
    auth = entry.get("auth")
    if isinstance(auth, dict):
        kind = auth.get("type")
        fields = ("key", "token") if kind == API_KEY else ("token", "key")
        stored = next(
            (auth[name] for name in fields if auth.get(name) not in (None, "")), None
        )
        if stored is not None:
            header = auth.get("header") if kind == API_KEY else None
            return (kind, read(stored), header, stored)
    if secret not in (None, {}):
        return (None, read(secret), None, secret)
    return None


def _kept_value(held, usable):
    """``(value to keep, reason)`` for a held secret the vault does not take,
    or None when it holds nothing worth keeping."""
    kind, value, header, stored = held
    unread = value is None and isinstance(stored, str)
    if unread and stored.startswith(ENCRYPTED_PREFIX):
        return stored, "a stored ciphertext this deployment cannot read"
    if value is None or value == "":
        return None
    if usable:
        return value, None
    return value, _refusal(kind, value, header) or "a value the gateway did not send"


def _vault_server_id(key):
    """``key``, or for one longer than the vault's server_id a stable form
    that fits: its first characters, ``~`` and 16 hex of its SHA-256."""
    if len(key) <= SERVER_ID_CHARS:
        return key
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{key[:SHORT_KEY_PREFIX_CHARS]}~{digest}"


def _legacy_secret(entry, read):
    """The secret a server entry (or ``{"credential": managed entry}``) holds
    inline and the gateway sent, read clear, or None (``legacy_secret``)."""
    if not isinstance(entry, dict):
        return None
    credential = entry.get("credential")
    secret = credential.get("secret") if isinstance(credential, dict) else None
    if isinstance(secret, dict):
        return _usable(
            secret.get("type"), read(secret.get("value")), secret.get("header")
        )
    auth = entry.get("auth")
    if not isinstance(auth, dict):
        return None
    if auth.get("type") == BEARER:
        return _usable(BEARER, read(auth.get("token")), None)
    if auth.get("type") == API_KEY:
        return _usable(API_KEY, read(auth.get("key")), auth.get("header"))
    return None


# --- where an mcp section holds a secret (config_secrets) ------------------


def _servers(mcp):
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    return servers if isinstance(servers, dict) else {}


def _managed_entries(mcp):
    """(key, entry) for each managed_tools.credentials entry, under every
    spelling of managed_tools, managed_tools first."""
    entries = []
    for key in MANAGED_TOOLS_KEYS:
        section = mcp.get(key) if isinstance(mcp, dict) else None
        credentials = section.get("credentials") if isinstance(section, dict) else None
        for managed_id, entry in (
            credentials if isinstance(credentials, dict) else {}
        ).items():
            if isinstance(entry, dict):
                entries.append((managed_id, entry))
    return entries


def _server_holds_secret(entry):
    auth = entry.get("auth")
    if isinstance(auth, dict) and any(
        auth.get(key) not in (None, "") for key in ("token", "key")
    ):
        return True
    credential = entry.get("credential")
    return isinstance(credential, dict) and credential.get("secret") not in (None, {})


def _holds_secrets(mcp):
    return any(
        isinstance(entry, dict) and _server_holds_secret(entry)
        for entry in _servers(mcp).values()
    ) or any(
        entry.get("secret") not in (None, {}) for _, entry in _managed_entries(mcp)
    )


def _secrets_of(mcp, read):
    """(key, secret or None, held) for each secret ``mcp`` holds: the secret
    is None for one the gateway never sent; ``held`` is what ``_held`` reads."""
    found = []
    for server_id, entry in _servers(mcp).items():
        if isinstance(entry, dict) and _server_holds_secret(entry):
            found.append(
                (server_id, _legacy_secret(entry, read), _held(entry, read))
            )
    for key, entry in _managed_entries(mcp):
        if entry.get("secret") not in (None, {}):
            wrapped = {"credential": entry}
            found.append(
                (key, _legacy_secret(wrapped, read), _held(wrapped, read))
            )
    return found


# --- references (pushes_credential) ----------------------------------------


def _reference_id(reference):
    for key in CREDENTIAL_ID_KEYS:
        if key in reference:
            return reference[key]
    return None


def _entry_reference(mcp, key):
    """The entry that holds ``key``'s credential reference, or {}."""
    if key.startswith(MANAGED_PREFIX):
        managed = next(
            (
                mcp[name]
                for name in MANAGED_TOOLS_KEYS
                if isinstance(mcp, dict) and isinstance(mcp.get(name), dict)
            ),
            None,
        )
        credentials = managed.get("credentials") if managed else None
        entry = credentials.get(key) if isinstance(credentials, dict) else None
    else:
        server = _servers(mcp).get(key)
        entry = server.get("credential") if isinstance(server, dict) else None
    return entry if isinstance(entry, dict) else {}


def _pushes(mcp, credential):
    """Whether ``mcp`` sends ``credential``: its key's entry names it with
    state active."""
    reference = _entry_reference(mcp, credential.server_id)
    return (
        _reference_id(reference) == str(credential.pk)
        and reference.get("state") == ACTIVE
    )


def _reference(credential, mode):
    return {
        "mode": mode,
        "credential_id": str(credential.pk),
        "state": credential.status,
    }


# --- rewriting a version ---------------------------------------------------


def _scrub(mcp, read, credentials):
    """``mcp`` with every secret removed and, where a usable one was, its
    key's credential reference in its place; ``mcp`` itself when it holds no
    secret."""
    if not _holds_secrets(mcp):
        return mcp
    mcp = copy.deepcopy(mcp)
    for key, entry in _managed_entries(mcp):
        usable = _legacy_secret({"credential": entry}, read) is not None
        entry.pop("secret", None)
        if usable and key in credentials:
            entry.update(_reference(credentials[key], entry.get("mode") or SHARED))
    for server_id, entry in _servers(mcp).items():
        if not isinstance(entry, dict):
            continue
        usable = _legacy_secret(entry, read) is not None
        entry.pop("auth", None)
        held = entry.get("credential")
        held = held if isinstance(held, dict) else {}
        held.pop("secret", None)
        if usable and server_id in credentials:
            held.update(_reference(credentials[server_id], SHARED))
        if held:
            entry["credential"] = held
        else:
            entry.pop("credential", None)
    return mcp


def _needs_access_default(mcp):
    access = mcp.get("access") if isinstance(mcp, dict) else None
    return bool(_servers(mcp)) and not (
        isinstance(access, dict) and "default" in access
    )


def _with_access_default(mcp, opens_access):
    if not (opens_access and _needs_access_default(mcp)):
        return mcp
    access = mcp.get("access")
    access = access if isinstance(access, dict) else {}
    return {**mcp, "access": {**access, "default": "allow"}}


# --- the move --------------------------------------------------------------


def _vault_applied_at(using):
    applied = (
        MigrationRecorder(connections[using])
        .migration_qs.filter(app=VAULT_MIGRATION[0], name=VAULT_MIGRATION[1])
        .values_list("applied", flat=True)
        .first()
    )
    if applied is None:
        raise RuntimeError(f"migration {'.'.join(VAULT_MIGRATION)} is not applied")
    return applied


def _orgs_to_move(OrgConfig, using, cutoff):
    """The orgs with a version that holds an MCP secret, or MCP servers from
    before ``cutoff``: read in primary-key batches, without locks."""
    manager = OrgConfig._base_manager.db_manager(using)
    found = set()
    after = None
    while True:
        batch = manager.order_by("pk").only("pk", "organization", "created_at", "mcp")
        if after is not None:
            batch = batch.filter(pk__gt=after)
        rows = list(batch[:BATCH_SIZE])
        for row in rows:
            if _holds_secrets(row.mcp) or (
                row.created_at < cutoff and _servers(row.mcp)
            ):
                found.add(row.organization_id)
        if len(rows) < BATCH_SIZE:
            return sorted(found, key=str)
        after = rows[-1].pk


def _report_line(org_id, kept):
    listed = "; ".join(f"{key} ({reason})" for key, reason in kept)
    return (
        f"0056_mcp_secrets_to_vault: org {org_id}: kept {len(kept)} value(s) the "
        f"vault does not accept, as revoked credentials (never sent): {listed}. "
        f"To delete them: manage.py {REMOVE_KEPT} --org {org_id} --apply"
    )


def _move_org(apps, using, org_id, cutoff, read, encrypt, report=None):
    """Move one org's MCP secrets, in one transaction under its config lock.
    Returns whether anything was written; appends ``(key, reason)`` to
    ``report`` for each value kept as a revoked credential."""
    report = [] if report is None else report
    Organization = apps.get_model("accounts", "Organization")
    OrgConfig = apps.get_model("prism", "AgentccOrgConfig")
    Credential = apps.get_model("prism", "AgentccMCPCredential")
    configs = OrgConfig._base_manager.db_manager(using)
    vault = Credential._base_manager.db_manager(using)
    with transaction.atomic(using=using):
        list(
            Organization._base_manager.db_manager(using)
            .select_for_update(no_key=True)
            .only("pk")
            .filter(pk=org_id)
        )
        rows = list(
            configs.select_for_update()
            .filter(organization_id=org_id)
            .order_by("-is_active", "deleted", "-version")
        )
        active = next((row for row in rows if row.is_active and not row.deleted), None)
        active_mcp = active.mcp if active is not None else {}
        opens_access = any(
            row.created_at < cutoff and _servers(row.mcp) for row in rows
        )

        secrets = {}
        live = set()
        # key -> (kind, value, header, reason): kept as a revoked credential.
        kept = {}
        for row in rows:
            for key, secret, held in _secrets_of(row.mcp, read):
                if key in secrets:
                    # An older value the stored one superseded.
                    continue
                fits = len(key) <= SERVER_ID_CHARS
                if secret is not None and fits:
                    secrets[key] = secret
                    if row is active:
                        live.add(key)
                    continue
                if key in kept or held is None:
                    continue
                keep = _kept_value(held, usable=secret is not None)
                if keep is None:
                    continue
                value, reason = keep
                if not fits:
                    too_long = (
                        f"its id is {len(key)} characters, longer than the "
                        f"vault's {SERVER_ID_CHARS}"
                    )
                    reason = too_long if reason is None else f"{reason}; {too_long}"
                kept[key] = (held[0], value, held[2], reason)
        if not any(_holds_secrets(row.mcp) for row in rows) and (
            active is None
            or _with_access_default(active_mcp, opens_access) == active_mcp
        ):
            return False

        credentials = {}
        for key, (kind, value, header) in secrets.items():
            shared = vault.filter(
                organization_id=org_id, server_id=key, scope=SHARED, status=ACTIVE
            )
            existing = shared.filter(user__isnull=True, account_label="").first()
            if existing is not None and _pushes(active_mcp, existing):
                # The active config already sends the vault's credential: it
                # wins over the inline secret.
                credentials[key] = existing
                continue
            if existing is not None:
                # Active, but no config sends it: revoked, as create() does.
                shared.update(status=REVOKED)
            credentials[key] = vault.create(
                organization_id=org_id,
                server_id=key,
                scope=SHARED,
                user=None,
                kind=kind,
                header_name=header,
                encrypted_secret=encrypt(value),
                status=ACTIVE if key in live else REVOKED,
                label=LABEL,
                created_by=None,
            )
        for key, (kind, value, header, reason) in kept.items():
            # Never sent and never referenced: the unique rule covers active
            # rows only, so it sits beside any credential the key has, one
            # the active config sends included.
            vault.create(
                organization_id=org_id,
                server_id=_vault_server_id(key),
                scope=SHARED,
                user=None,
                kind=kind if kind in STATIC_KINDS else BEARER,
                header_name=(
                    header
                    if isinstance(header, str) and len(header) <= HEADER_NAME_CHARS
                    else ""
                ),
                encrypted_secret=encrypt(value),
                status=REVOKED,
                label=f"{KEPT_LABEL}: {reason}. Key: {key}"[:LABEL_CHARS],
                created_by=None,
            )
            report.append((key, reason))

        for row in rows:
            scrubbed = _scrub(row.mcp, read, credentials)
            if row is active:
                scrubbed = _with_access_default(scrubbed, opens_access)
            if scrubbed != row.mcp:
                configs.filter(pk=row.pk).update(mcp=scrubbed)
    return True


def forwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from integrations.services.credentials import CredentialManager

        using = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        # Every inline secret is encrypted into the vault, a value it does not
        # accept included (kept revoked): this needs a key every process shares.
        key_guard.require_stable_key(
            lambda: key_guard.any_org_config(
                apps, using, lambda config: _holds_secrets(config.mcp)
            )
        )
        OrgConfig = apps.get_model("prism", "AgentccOrgConfig")
        cutoff = _vault_applied_at(using)
        read = _reader(CredentialManager)
        encrypt = _encrypter(CredentialManager)
        for org_id in _orgs_to_move(OrgConfig, using, cutoff):
            kept = []
            _move_org(apps, using, org_id, cutoff, read, encrypt, kept)
            if kept:
                # After the org's transaction committed: the report states
                # only what was written.
                print(_report_line(org_id, kept))


class Migration(migrations.Migration):
    # One transaction per org (see the module docstring), never the whole
    # move in one.
    atomic = False

    dependencies = [
        ("prism", "0055_ee_agent_key_mint_idempotency"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
