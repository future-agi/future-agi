"""Forget the client a vendor's authorization server registered for us.

When a vendor takes no client metadata document, the first sign-in there
registers a client for us, and every later sign-in reuses it. Run this
command when the vendor has revoked or rotated that client: it deletes the
one client stored for exactly the issuer and redirect URI given, and the next
sign-in at that vendor registers a new client. It prints the deleted row's id
and the count, never the client id, and changes nothing when no client
matches.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamClient


class Command(BaseCommand):
    help = (
        "Forget the client a vendor registered for us, after the vendor revoked "
        "or rotated it. Deletes only the client stored for exactly this issuer "
        "and redirect URI; the next sign-in at that vendor registers a new one."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--issuer", required=True, help="The vendor's issuer, exactly as stored."
        )
        parser.add_argument(
            "--redirect", required=True, help="Our redirect URI, exactly as stored."
        )

    def handle(self, *args, **options):
        issuer, redirect = options["issuer"], options["redirect"]
        for name, value in (("--issuer", issuer), ("--redirect", redirect)):
            if not (value or "").strip():
                raise CommandError(f"{name} is required and must not be blank")
        where = f"issuer {issuer} redirect {redirect}"
        with transaction.atomic():
            row = AgentccMCPUpstreamClient.objects.filter(
                issuer=issuer, redirect_uri=redirect
            ).first()
            if row is None:
                self.stdout.write(f"no registered client for {where}; nothing deleted")
                return
            pk = row.pk
            deleted, _ = row.delete()
        self.stdout.write(f"forgot client row {pk} for {where}; deleted {deleted}")
