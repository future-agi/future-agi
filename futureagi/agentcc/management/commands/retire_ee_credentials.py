"""Retire Enterprise-issued credentials and confirm gateway revocation."""

import json

from django.core.management.base import BaseCommand, CommandError

from agentcc.services.ee_credential_retirement import retire_ee_credentials


class Command(BaseCommand):
    help = "Retire Enterprise-issued credentials and retry gateway revocation readback."

    def handle(self, *args, **options):
        try:
            result = retire_ee_credentials()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["gateway_pending"]:
            raise CommandError(
                "Gateway retirement is unconfirmed; retry after connectivity is restored."
            )
