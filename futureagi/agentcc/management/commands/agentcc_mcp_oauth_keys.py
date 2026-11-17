"""Manage the MCP authorization server's signing keys.

    manage.py agentcc_mcp_oauth_keys            # list keys (kid, status, created)
    manage.py agentcc_mcp_oauth_keys --rotate   # new active key, previous kept published
    manage.py agentcc_mcp_oauth_keys --push     # send the published keys to the gateway

Only key ids, statuses and the push version are printed; a private key never
is. ``--push`` exits 1 when the gateway does not take the keys.
"""

from django.core.management.base import BaseCommand

from agentcc.mcp_oauth import keys
from agentcc.models.mcp_oauth import AgentccMCPOAuthSigningKey
from agentcc.services.gateway_client import GatewayClientError


class Command(BaseCommand):
    help = "List, rotate or push the MCP authorization server's signing keys."

    def add_arguments(self, parser):
        parser.add_argument("--rotate", action="store_true")
        parser.add_argument("--push", action="store_true")

    def handle(self, *args, **options):
        if options["rotate"]:
            key = keys.rotate()
            self.stdout.write(f"rotated: new active key {key.kid}")
        if options["push"]:
            keys.ensure_active_key()
            try:
                keys.push()
            except GatewayClientError as e:
                self.stderr.write(f"push failed: {e}")
                raise SystemExit(1) from None
            self.stdout.write(f"pushed version {keys.version()}")
        for row in AgentccMCPOAuthSigningKey.objects.order_by("-created_at", "-id"):
            self.stdout.write(
                f"{row.kid}\t{row.status}\t{row.created_at:%Y-%m-%dT%H:%M:%SZ}"
            )
