"""Manage the agent service deployments' keys for FA's console-run signer.

    manage.py agentcc_console_run_signer_keys
    manage.py agentcc_console_run_signer_keys --add --deployment D --kid K \
        --public-key=<32-byte Ed25519 public key, base64url> --operator NAME
    manage.py agentcc_console_run_signer_keys --revoke --kid K --operator NAME

The only writer of the registry (fi-security ruling 4). At most two active keys
per deployment. Lists key id, deployment, status, operator and dates; never key
bytes. A refusal prints its reason and exits 1. Pass the key as
``--public-key=<key>``: a base64url key may start with "-", which a separate
argument would read as an option.
"""

from django.core.management.base import BaseCommand

from agentcc.models.console_run_signer_key import AgentccConsoleRunSignerKey
from agentcc.services.console_run_signer_keys import (
    SignerKeyRefused,
    add_key,
    revoke_key,
)


class Command(BaseCommand):
    help = "List, add or revoke the console-run signer's agent service keys."

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--add", action="store_true")
        action.add_argument("--revoke", action="store_true")
        parser.add_argument("--deployment")
        parser.add_argument("--kid")
        parser.add_argument("--public-key", dest="public_key")
        parser.add_argument("--operator")

    def handle(self, *args, **options):
        try:
            if options["add"]:
                row = add_key(
                    deployment=options["deployment"],
                    kid=options["kid"],
                    public_key=options["public_key"],
                    operator=options["operator"],
                )
                self.stdout.write(f"added {row.kid} for {row.deployment}")
            elif options["revoke"]:
                row = revoke_key(kid=options["kid"], operator=options["operator"])
                self.stdout.write(f"revoked {row.kid} for {row.deployment}")
        except SignerKeyRefused as refusal:
            self.stderr.write(f"refused: {refusal}")
            raise SystemExit(1) from None
        rows = AgentccConsoleRunSignerKey.objects.order_by("deployment", "created_at")
        for row in rows:
            status = "revoked" if row.revoked_at else "active"
            self.stdout.write(
                f"{row.kid}\t{row.deployment}\t{status}\t{row.created_by}"
                f"\t{row.created_at:%Y-%m-%dT%H:%M:%SZ}"
            )
