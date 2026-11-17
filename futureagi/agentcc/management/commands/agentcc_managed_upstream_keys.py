"""Show and change an org's managed upstream keys (per-org upstream API keys).

    python manage.py agentcc_managed_upstream_keys <org_id> --show
    python manage.py agentcc_managed_upstream_keys <org_id> --provision [--limit-usd 50] [--live]
    python manage.py agentcc_managed_upstream_keys <org_id> --set --provider P --key-file F
        [--key-hash H] [--label L] [--limit-usd N]
    python manage.py agentcc_managed_upstream_keys <org_id> --rotate [--provider P]
        [--key-file F [--key-hash H]] [--live]
    python manage.py agentcc_managed_upstream_keys <org_id> --revoke [--provider P] [--live]

--provision creates an OpenRouter key for the org with the provisioning API
and stores it. --rotate without --key-file provisions the replacement the same
way; --rotate and --revoke of an OpenRouter key then disable the old key at
OpenRouter. Those upstream calls go to the live API only with all three
switches (FUTUREAGI_OPENROUTER_PROVISIONING_KEY, MANAGED_UPSTREAM_PROVISIONING_LIVE=1
and --live); without them the command prints each request it would send and
exits 0. Local writes push the org's config to the gateway when they commit.

The command never prints a key: --show prints each key's provider, key hash,
label, status and limit.
"""

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from agentcc.services import managed_upstream_keys as keys
from agentcc.services import openrouter_provisioning as provisioning


def _usd(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("not a number") from None
    if (
        not amount.is_finite()
        or amount < 0
        or amount != amount.quantize(Decimal("0.01"))
    ):
        raise ValueError("must be a non-negative USD amount with at most 2 decimals")
    return amount


class Command(BaseCommand):
    help = "Show, provision, set, rotate or revoke an org's managed upstream keys."

    def add_arguments(self, parser):
        parser.add_argument("org_id")
        actions = parser.add_mutually_exclusive_group(required=True)
        for flag in ("--show", "--provision", "--set", "--rotate", "--revoke"):
            actions.add_argument(flag, action="store_true")
        parser.add_argument("--provider", default=keys.OPENROUTER)
        parser.add_argument("--key-file", help="a file holding only the key")
        parser.add_argument("--key-hash", help="default: the key's SHA-256, in hex")
        parser.add_argument("--label")
        parser.add_argument("--limit-usd", type=_usd)
        parser.add_argument(
            "--live", action="store_true", help="call the live provisioning API"
        )

    def handle(self, *args, **options):
        org = self._org(options["org_id"])
        try:
            if options["show"]:
                self._show(org)
            elif options["provision"]:
                self._provision(org, options)
            elif options["set"]:
                self._set(org, options)
            elif options["rotate"]:
                self._rotate(org, options)
            else:
                self._revoke(org, options)
        except (keys.ManagedUpstreamKeyError, provisioning.ProvisioningError) as e:
            raise CommandError(str(e)) from e

    # --- actions ---

    def _show(self, org):
        rows = keys.keys_for_org(org)
        self.stdout.write(f"org {org.pk}: {len(rows)} managed upstream key(s)")
        for row in rows:
            self.stdout.write(_describe(row))

    def _provision(self, org, options):
        if options["provider"] != keys.OPENROUTER:
            raise CommandError("--provision is for the openrouter provider only")
        if keys.active_key(org, keys.OPENROUTER) is not None:
            raise CommandError(
                f"org {org.pk} already has an active {keys.OPENROUTER} key: use --rotate"
            )
        request = provisioning.create_key_request(
            keys.provisioned_key_name(org), options["limit_usd"]
        )
        client = self._client(options, [request])
        if client is None:
            return
        change = keys.provision(
            org, client, limit_usd=options["limit_usd"], label=options["label"]
        )
        self._report("provisioned", change)

    def _set(self, org, options):
        api_key = self._read_key(options)
        change = keys.set_key(
            org,
            options["provider"],
            api_key,
            key_hash=options["key_hash"] or _fingerprint(api_key),
            label=options["label"] or "",
            limit_usd=options["limit_usd"],
        )
        self._report("set", change)

    def _rotate(self, org, options):
        provider = options["provider"]
        current = keys.active_key(org, provider)
        if current is None:
            raise CommandError(f"org {org.pk} has no active {provider} key to rotate")
        client = None
        if options["key_file"]:
            api_key = self._read_key(options)
            change = keys.rotate(
                org,
                provider,
                api_key,
                key_hash=options["key_hash"] or _fingerprint(api_key),
                label=options["label"],
                limit_usd=options["limit_usd"],
            )
        else:
            if provider != keys.OPENROUTER:
                raise CommandError("--rotate needs --key-file for this provider")
            limit = options["limit_usd"]
            client = self._client(
                options,
                [
                    provisioning.create_key_request(
                        keys.provisioned_key_name(org),
                        current.limit_usd if limit is None else limit,
                    ),
                    provisioning.update_key_request(current.key_hash, disabled=True),
                ],
            )
            if client is None:
                return
            change = keys.rotate_provisioned(
                org, client, limit_usd=limit, label=options["label"]
            )
        self._report("rotated", change)
        self._disable_upstream(options, change.replaced, client)

    def _revoke(self, org, options):
        change = keys.revoke(org, options["provider"])
        self._report("revoked", change)
        self._disable_upstream(options, change.key, None)

    # --- upstream calls ---

    def _client(self, options, would_send):
        """A provisioning client, or None after printing what a live run
        would send when the live switches are off."""
        try:
            return provisioning.OpenRouterProvisioningClient(live=options["live"])
        except provisioning.LiveProvisioningRefused as refused:
            self.stdout.write(f"Dry run, nothing sent: {refused}")
            for request in would_send:
                self.stdout.write(
                    "Would send: " + request.describe(provisioning.LIVE_BASE_URL)
                )
            return None

    def _disable_upstream(self, options, row, client):
        """Disable a replaced or revoked OpenRouter key at OpenRouter."""
        if row is None or row.provider_id != keys.OPENROUTER:
            return
        if client is None:
            request = provisioning.update_key_request(row.key_hash, disabled=True)
            client = self._client(options, [request])
            if client is None:
                return
        try:
            client.update_key(row.key_hash, disabled=True)
        except provisioning.ProvisioningError as e:
            raise CommandError(
                f"The key was changed and pushed, but disabling key {row.key_hash} "
                f"upstream failed ({e}). Disable it at OpenRouter."
            ) from e
        self.stdout.write(f"disabled upstream key {row.key_hash}")

    # --- helpers ---

    def _org(self, org_id):
        try:
            org = Organization.objects.filter(pk=org_id).first()
        except (ValidationError, ValueError):
            org = None
        if org is None:
            raise CommandError(f"No organization {org_id}")
        return org

    def _read_key(self, options):
        if not options["key_file"]:
            raise CommandError("--key-file is required")
        try:
            api_key = Path(options["key_file"]).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise CommandError(f"Cannot read --key-file: {type(e).__name__}") from None
        if not api_key or any(c.isspace() for c in api_key):
            raise CommandError("--key-file must hold one key and nothing else")
        return api_key

    def _report(self, action, change):
        self.stdout.write(f"{action}: {_describe(change.key)}")
        synced = change.gateway_synced
        if synced is True:
            self.stdout.write("gateway: config pushed")
        elif synced is None:
            self.stdout.write(
                "gateway: no active org config to push; the next config push "
                "carries the key"
            )
        else:
            self.stdout.write(
                "gateway: push failed; the gateway picks the change up on its "
                "next config sync"
            )


def _describe(row) -> str:
    limit = "-" if row.limit_usd is None else f"{row.limit_usd:.2f}"
    rotated = row.rotated_at.isoformat() if row.rotated_at else "-"
    return (
        f"{row.provider_id} key_hash={row.key_hash} label={row.label or '-'} "
        f"status={row.status} limit_usd={limit} "
        f"created={row.created_at.isoformat()} rotated={rotated}"
    )


def _fingerprint(api_key: str) -> str:
    """The key's SHA-256 in hex: an identifier that does not reveal the key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
