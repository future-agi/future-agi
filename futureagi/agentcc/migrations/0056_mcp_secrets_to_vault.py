"""Move the MCP secrets stored inline in org configs into the MCP credential
vault (MCP-APPS-PLAN.md §2.1 ruling 3: one MCP secret store, M14's vault).

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
``agentcc_mcp_migrate_secrets --apply``, with two differences:

- It reads an ``enc::`` value as the clear value the release push sent (the
  vault never stores a value that starts with ``enc::``). A well-formed
  ``enc::`` token this deployment cannot decrypt, which that push already
  dropped, is removed and stored nowhere.
- It rewrites every version in place and pushes nothing: it writes no new
  version, and the gateway gets the same secrets from the vault on the next
  push or bulk sync. On the release line the org-saved MCP servers never
  connected (MCP-APPS-PLAN.md §1: the tenant, vserver and meter hooks were
  no-ops), so no live connection depends on the order.

Afterwards ``agentcc_mcp_migrate_secrets --check`` passes and ``--apply``
finds nothing to do. ``--check`` stays the deploy's gate: a version a pod
still running the release code writes during the rollout can hold a new
inline secret, and ``--apply`` moves it.

The rules, per org:

- Versions are read active first, then by ``deleted``, then newest first.
  The first usable secret seen for a key (a server id, or the ``fagi.<id>``
  of a managed entry) is the one stored. An older value it superseded, and a
  value the gateway never sent (``auth`` of type none, an api_key with no
  header, a value the vault's check refuses), are removed, not stored.
- A secret is stored as an active credential when the active version's own
  entry holds it, and as a revoked one otherwise (its server was removed, or
  its auth was). An active shared credential the active version already
  references is kept, and the inline secret is dropped; an active one it
  does not reference is revoked first.
- Every entry that held a usable secret gets a reference to its key's
  credential (``state`` is the credential's status) in place of the secret.
  An entry whose secret was never usable gets no reference, so a server
  left without auth stays without auth.
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
import re

from django.db import DEFAULT_DB_ALIAS, connections, migrations, transaction
from django.db.migrations.recorder import MigrationRecorder

from tfc.migration_database import executor_alias_scope

ENCRYPTED_PREFIX = "enc::"
# Rows read per batch in the first (lock-free) pass.
BATCH_SIZE = 500
LABEL = "Moved from the org config"
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
    """(key, secret or None) for each secret ``mcp`` holds; None for one the
    gateway never sent."""
    found = []
    for server_id, entry in _servers(mcp).items():
        if isinstance(entry, dict) and _server_holds_secret(entry):
            found.append((server_id, _legacy_secret(entry, read)))
    for key, entry in _managed_entries(mcp):
        if entry.get("secret") not in (None, {}):
            found.append((key, _legacy_secret({"credential": entry}, read)))
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


def _move_org(apps, using, org_id, cutoff, read, encrypt):
    """Move one org's MCP secrets, in one transaction under its config lock.
    Returns whether anything was written."""
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
        for row in rows:
            for key, secret in _secrets_of(row.mcp, read):
                if secret is not None and key not in secrets:
                    secrets[key] = secret
                    if row is active:
                        live.add(key)
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
        OrgConfig = apps.get_model("prism", "AgentccOrgConfig")
        cutoff = _vault_applied_at(using)
        read = _reader(CredentialManager)
        encrypt = _encrypter(CredentialManager)
        for org_id in _orgs_to_move(OrgConfig, using, cutoff):
            _move_org(apps, using, org_id, cutoff, read, encrypt)


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
