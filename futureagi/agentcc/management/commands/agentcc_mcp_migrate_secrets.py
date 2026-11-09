"""Move every plaintext MCP secret out of org configs into the MCP credential
vault (M14). On the release line migration 0056 does the move at ``migrate``
by these rules (MCP-APPS-PLAN.md §2.1 ruling 3); ``--check`` is the deploy's
gate after it, and ``--apply`` moves what a pod still on the release code
wrote inline during the rollout.

    python manage.py agentcc_mcp_migrate_secrets [--apply | --check]
        [--org <uuid>] [--servers-before <ISO datetime>]

With neither flag it is a dry run (M14 IR-12, ruled 2026-10-19; IC-R1-10): it
reports, per org, what ``--apply`` would store, revoke, rewrite and remove,
and writes nothing. ``--apply`` migrates, as below.

Every config version is read, the active one and its history, soft-deleted
versions included: a secret in any of them is a secret in the database. An
inline value is read as the push sent it (``org_config_secrets.
clear_mcp_secrets``): the release line's NG-01 ``enc::`` ciphertext is
decrypted, and a token this deployment cannot decrypt counts as a value the
gateway never sent. Per org, under its config lock:

- Each server's secret goes into the vault once. The vault keeps a credential
  the org already has when the active version sends it (references it with
  state active); an active one no config sends (a save disconnected it) is
  revoked, as create() does, and never bound to an entry. Otherwise the
  active version's secret is stored, else the newest version's, bound to the
  origin of the URL its server has in that version (IC-R1-12). Only a
  secret the active version's own entry holds
  (one the gateway sends today) becomes an active credential; a server whose
  only usable secret is in older versions (it was removed, or the active
  entry's auth was removed or is one the gateway never sent) gets a revoked
  one, as remove-mcp-server leaves it. Older values a newer one superseded,
  and values the gateway never sent (``auth`` of type none, an api_key with
  no header), are removed and counted, not stored.
- Each entry that held a usable secret gets a reference to its server's
  credential in place of the secret (``credential {mode, credential_id,
  state}``, ``state`` the credential's status); an entry whose secret was
  never sent loses it and gets no reference, so a server left without auth
  stays without auth. The active version is written again through the config
  writer as a new version and pushed; if the writer refuses it for another
  reason, it is rewritten in place, so no secret stays.
- An org that had MCP servers before the vault existed also gets
  ``mcp.access.default: allow`` (M10's compatibility rule) unless it states a
  default. "Before" is a config version with servers created before the
  vault's migration (``prism.0035_mcp_credential``) was applied, or before
  ``--servers-before``: a later run never opens access for an org that
  added its first server after the deploy.
- Every older version is rewritten in place the same way.

Running ``--apply`` again changes nothing. ``--check`` changes nothing and
exits 1 while anything is unmigrated: a plaintext MCP secret in any version,
or an org whose access default ``--apply`` would still set. Output names orgs,
versions and paths, never a value.
"""

from typing import NamedTuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils.dateparse import parse_datetime

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.org_config_secrets import clear_mcp_secrets
from agentcc.services.org_config_writer import lock_org, write_config_version_with

Status = vault.Status
DESCRIPTION = "Move MCP secrets into the MCP credential vault"
LABEL = "Moved from the org config"
VAULT_MIGRATION = ("prism", "0035_mcp_credential")


class Command(BaseCommand):
    help = "Move plaintext MCP secrets from org configs into the credential vault."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Migrate. Without it the command is a dry run: it reports what "
                "--apply would do and writes nothing."
            ),
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Change nothing; exit 1 while anything is unmigrated: an MCP "
                "secret in any config version, or an access default --apply "
                "would set."
            ),
        )
        parser.add_argument("--org", help="Only this organization id.")
        parser.add_argument(
            "--servers-before",
            help=(
                "ISO datetime: orgs with MCP servers in a config version created "
                "before it get mcp.access.default=allow. Default: when the vault's "
                "migration was applied."
            ),
        )

    def handle(self, *args, **options):
        if options["apply"] and options["check"]:
            raise CommandError("--apply and --check cannot be combined")
        versions = AgentccOrgConfig._base_manager.all()
        if options["org"]:
            versions = versions.filter(organization_id=options["org"])
        cutoff = _cutoff(options["servers_before"])
        # order_by clears the model's ordering, which DISTINCT would select.
        org_ids = (
            versions.order_by("organization_id")
            .values_list("organization_id", flat=True)
            .distinct()
        )
        if options["check"]:
            return self._check(versions, org_ids, cutoff)
        if not options["apply"]:
            return self._dry_run(org_ids, cutoff)
        failed = []
        for org_id in org_ids:
            try:
                self._migrate(Organization.objects.get(pk=org_id), cutoff)
            except vault.MCPCredentialError as e:
                failed.append(str(org_id))
                self.stderr.write(f"org {org_id}: not migrated: {e}")
        if failed:
            raise CommandError(
                f"{len(failed)} org(s) not migrated: {', '.join(failed)}"
            )

    def _check(self, versions, org_ids, cutoff):
        """Read only: every plaintext secret by version and path, and every
        org whose access default --apply would still set."""
        found = 0
        for row in versions.only("id", "organization_id", "version", "mcp"):
            paths = sorted(vault.config_secrets(row.mcp))
            if paths:
                found += len(paths)
                self.stdout.write(
                    f"org {row.organization_id} v{row.version}: {', '.join(paths)}"
                )
        pending = 0
        for org_id in org_ids:
            rows, active = _versions_of(org_id)
            if _opens_access(rows, cutoff) and active is not None:
                if _needs_access_default(active.mcp):
                    pending += 1
                    self.stdout.write(
                        f"org {org_id} v{active.version}: mcp.access.default is not "
                        "set (MCP servers before the vault); --apply sets it to allow"
                    )
        problems = []
        if found:
            problems.append(f"{found} plaintext MCP secret(s) remain in org configs")
        if pending:
            problems.append(f"{pending} org(s) still need mcp.access.default")
        if problems:
            raise CommandError("; ".join(problems) + " (run with --apply)")
        self.stdout.write(
            "No plaintext MCP secrets in org configs. Nothing is left to migrate."
        )

    def _dry_run(self, org_ids, cutoff):
        """What --apply would do, per org: read only, no lock taken."""
        self.stdout.write("Dry run: nothing is written. Run with --apply to migrate.")
        pending = 0
        for org_id in org_ids:
            rows, active = _versions_of(org_id)
            plan = _Plan(
                Organization(pk=org_id),
                _opens_access(rows, cutoff),
                active.mcp if active is not None else {},
            )
            for row in rows:
                plan.collect(row.mcp, active=row is active)
            if not plan.needed(rows, active):
                continue
            pending += 1
            preview = plan.preview()
            in_place = sum(1 for row in rows if vault.config_secrets(row.mcp))
            access = (
                active is not None
                and plan.with_access_default(active.mcp) != active.mcp
            )
            # --apply writes the active version again when its mcp changes:
            # it holds a secret, or it gets the access default.
            new_active = active is not None and (
                bool(vault.config_secrets(active.mcp)) or access
            )
            self.stdout.write(
                f"org {org_id}: would store {preview.active + preview.revoked} "
                f"credential(s) ({preview.active} active, {preview.revoked} revoked), "
                f"keep {preview.kept} the active config already sends, revoke "
                f"{preview.unreferenced} unreferenced credential(s), rewrite "
                f"{in_place} version(s) in place, remove "
                f"{plan.dropped + preview.kept} superseded or unused secret(s)"
                + (
                    ", write the active config again as a new version"
                    if new_active
                    else ""
                )
                + (", set mcp.access.default=allow" if access else "")
            )
        if not pending:
            self.stdout.write("Nothing to migrate.")
        else:
            self.stdout.write(
                f"{pending} org(s) to migrate: run with --apply to write it."
            )

    def _migrate(self, org, cutoff):
        with transaction.atomic():
            lock_org(org.pk)
            rows = list(
                AgentccOrgConfig._base_manager.select_for_update()
                .filter(organization=org)
                .order_by("-is_active", "deleted", "-version")
            )
            active = next((r for r in rows if r.is_active and not r.deleted), None)
            opens_access = _opens_access(rows, cutoff)
            plan = _Plan(org, opens_access, active.mcp if active is not None else {})
            for row in rows:
                plan.collect(row.mcp, active=row is active)
            if not plan.needed(rows, active):
                return
            plan.store_credentials()
            written = self._rewrite_active(org, active, plan)
            in_place = 0
            for row in rows:
                scrubbed = plan.scrub(row.mcp)
                if row is active and written is None:
                    scrubbed = plan.with_access_default(scrubbed)
                if scrubbed != row.mcp:
                    AgentccOrgConfig._base_manager.filter(pk=row.pk).update(
                        mcp=scrubbed
                    )
                    in_place += 1
        self.stdout.write(
            f"org {org.pk}: {len(plan.stored)} credential(s) stored, "
            f"{in_place} version(s) rewritten in place, "
            f"{plan.dropped} superseded or unused secret(s) removed"
            + (
                f", {len(plan.revoked)} unreferenced credential(s) revoked"
                if plan.revoked
                else ""
            )
            + (f", active config now v{written}" if written else "")
        )

    def _rewrite_active(self, org, active, plan):
        """Write the active version again through the writer (a new version,
        pushed) when its mcp section changes. Returns the new version's
        number, or None when nothing changes or the writer refuses it for
        another reason, in which case the caller rewrites it in place."""
        if (
            active is None
            or plan.with_access_default(plan.scrub(active.mcp)) == active.mcp
        ):
            return None

        def updater(config):
            config.mcp = plan.with_access_default(plan.scrub(config.mcp))

        try:
            # The command writes the vault's references (M14's write guard).
            with vault.vault_write():
                config, _ = write_config_version_with(
                    org, updater, description=DESCRIPTION
                )
        except OrgConfigRejected as e:
            self.stderr.write(
                f"org {org.pk}: the writer refused a new version ({e}); "
                f"v{active.version} is rewritten in place"
            )
            return None
        return config.version


class _Plan:
    """One org's secrets, by server key (a server id, or the fagi.<id> of a
    managed_tools.credentials entry), and the credentials that replace them."""

    def __init__(self, org, opens_access, active_mcp):
        self.org = org
        # The active version's mcp section as the command found it: a vault
        # credential wins over a plaintext secret only when this pushes it.
        self.active_mcp = active_mcp
        # Keys whose kept secret is the active version's own: the only ones
        # stored as active credentials.
        self.live = set()
        self.opens_access = opens_access
        self.secrets = {}  # key -> Secret: the active version's, else the newest
        # key -> the origin of its server's URL where that secret was held ("" for
        # a fagi.* key): the stored credential is bound to it (IC-R1-12).
        self.origins = {}
        self.credentials = {}  # key -> AgentccMCPCredential
        self.stored = []
        self.revoked = []  # ids of active credentials no config pushed
        self.dropped = 0

    def collect(self, mcp, *, active=False):
        """Rows arrive active first, then newest first, so the first usable
        secret seen for a key is the one the vault keeps."""
        for key, secret in _secrets_of(mcp):
            if secret is None:
                self.dropped += 1
            elif key not in self.secrets:
                self.secrets[key] = secret
                self.origins[key] = vault.origin_of(vault.configured_url(mcp, key))
                if active:
                    self.live.add(key)
            elif secret != self.secrets[key]:
                self.dropped += 1

    def needed(self, rows, active):
        if any(vault.config_secrets(row.mcp) for row in rows):
            return True
        return active is not None and self.with_access_default(active.mcp) != active.mcp

    def preview(self):
        """What store_credentials would do, read only (the dry run)."""
        kept = active = revoked = unreferenced = 0
        for key in self.secrets:
            existing = vault.active_shared(self.org.pk, key)
            if existing is not None and vault.pushes_credential(
                self.active_mcp, existing
            ):
                kept += 1
                continue
            if existing is not None:
                unreferenced += AgentccMCPCredential.objects.filter(
                    organization_id=self.org.pk,
                    server_id=key,
                    scope=vault.Scope.SHARED,
                    status=Status.ACTIVE,
                ).count()
            if key in self.live:
                active += 1
            else:
                revoked += 1
        return _Preview(kept, active, revoked, unreferenced)

    def store_credentials(self):
        for key, secret in self.secrets.items():
            existing = vault.active_shared(self.org.pk, key)
            if existing is not None and vault.pushes_credential(
                self.active_mcp, existing
            ):
                # The active config already sends the vault's credential for
                # this server: it wins. One it references but does not send
                # (its server is not at the origin it is bound to, or it is
                # bound to none, IC-R1-12) is revoked below like one it does
                # not reference.
                self.credentials[key] = existing
                self.dropped += 1
                continue
            if existing is not None:
                # Active but sent by no config (a save disconnected it): it is
                # revoked, as create() does, and never bound to an entry.
                self.revoked.extend(
                    vault.revoke_server_credentials(
                        self.org, key, scope=vault.Scope.SHARED
                    )
                )
            credential = vault.store(
                self.org,
                key,
                secret,
                actor=None,
                label=LABEL,
                status=Status.ACTIVE if key in self.live else Status.REVOKED,
                origin=self.origins[key],
            )
            self.credentials[key] = credential
            self.stored.append(credential)

    def scrub(self, mcp):
        """``mcp`` with every secret removed and each server's or managed
        entry's credential reference in its place; returned unchanged when it
        holds no secret. Whether an entry's secret was usable is read from
        its clear value (a copy: the secrets are removed either way)."""
        if not vault.config_secrets(mcp):
            return mcp
        mcp = clear_mcp_secrets(mcp)
        for key, entry in _managed_entries(mcp):
            usable = vault.legacy_secret({"credential": entry}) is not None
            entry.pop("secret", None)
            if usable and key in self.credentials:
                entry.update(self._reference(key, entry.get("mode") or "shared"))
        for server_id, entry in _servers(mcp).items():
            if not isinstance(entry, dict):
                continue
            usable = vault.legacy_secret(entry) is not None
            entry.pop("auth", None)
            held = entry.get("credential")
            held = held if isinstance(held, dict) else {}
            held.pop("secret", None)
            if usable and server_id in self.credentials:
                held.update(self._reference(server_id, "shared"))
            if held:
                entry["credential"] = held
            else:
                entry.pop("credential", None)
        return mcp

    def with_access_default(self, mcp):
        """``mcp`` with ``access.default: allow`` for an org that had servers
        before the vault (M10's compatibility rule)."""
        if not (self.opens_access and _needs_access_default(mcp)):
            return mcp
        access = mcp.get("access")
        access = access if isinstance(access, dict) else {}
        return {**mcp, "access": {**access, "default": "allow"}}

    def _reference(self, key, mode):
        credential = self.credentials[key]
        return {
            "mode": mode,
            "credential_id": str(credential.pk),
            "state": credential.status,
        }


class _Preview(NamedTuple):
    """The credential writes --apply would make for one org."""

    kept: int  # vault credentials the active config already sends
    active: int  # credentials stored active
    revoked: int  # credentials stored revoked (a secret only older versions hold)
    unreferenced: int  # active credentials no config sends, revoked


def _versions_of(org_id):
    """An org's config versions, active first, then newest first, and its
    active version (or None). Read only: no lock."""
    rows = list(
        AgentccOrgConfig._base_manager.filter(organization_id=org_id).order_by(
            "-is_active", "deleted", "-version"
        )
    )
    return rows, next((r for r in rows if r.is_active and not r.deleted), None)


def _opens_access(rows, cutoff):
    """Whether the org had MCP servers before ``cutoff`` (the vault): M10's
    compatibility rule then gives it mcp.access.default=allow."""
    return any(row.created_at < cutoff and _servers(row.mcp) for row in rows)


def _cutoff(servers_before):
    if servers_before:
        cutoff = parse_datetime(servers_before)
        if cutoff is None or cutoff.tzinfo is None:
            raise CommandError(
                "--servers-before must be an ISO datetime with a timezone"
            )
        return cutoff
    applied = (
        MigrationRecorder.Migration.objects.filter(
            app=VAULT_MIGRATION[0], name=VAULT_MIGRATION[1]
        )
        .values_list("applied", flat=True)
        .first()
    )
    if applied is None:
        raise CommandError(f"migration {'.'.join(VAULT_MIGRATION)} is not applied")
    return applied


def _secrets_of(mcp):
    """(key, Secret or None) for each secret an mcp section holds, read as
    the push sent it; None for one the gateway never sent. Whether an entry
    holds a secret is read from the stored section, its value from the clear
    copy (``clear_mcp_secrets``)."""
    clear = clear_mcp_secrets(mcp)
    clear_servers = _servers(clear)
    found = []
    for server_id, entry in _servers(mcp).items():
        if vault.config_secrets({"servers": {server_id: entry}}):
            found.append((server_id, vault.legacy_secret(clear_servers[server_id])))
    for (key, entry), (_, clear_entry) in zip(
        _managed_entries(mcp), _managed_entries(clear), strict=True
    ):
        if entry.get("secret") not in (None, {}):
            found.append((key, vault.legacy_secret({"credential": clear_entry})))
    return found


def _servers(mcp):
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    return servers if isinstance(servers, dict) else {}


def _managed_entries(mcp):
    """(key, entry) for each managed_tools.credentials entry, under every
    spelling of managed_tools."""
    entries = []
    for section in vault.managed_tools_sections(mcp):
        credentials = section.get("credentials")
        for key, entry in (
            credentials if isinstance(credentials, dict) else {}
        ).items():
            if isinstance(entry, dict):
                entries.append((key, entry))
    return entries


def _needs_access_default(mcp):
    access = mcp.get("access") if isinstance(mcp, dict) else None
    return bool(_servers(mcp)) and not (
        isinstance(access, dict) and "default" in access
    )
