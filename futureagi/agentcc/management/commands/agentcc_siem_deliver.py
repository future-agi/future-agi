"""Send the SIEM exports that are due (G16-SIEM).

Delivers pending exports of enabled destinations once and prints the counts.
It does nothing while the deployment's AGENTCC_SIEM_EXPORT_ENABLED switch is
off. No schedule runs it until the deployment adopts one.
"""

import json

from django.core.management.base import BaseCommand

from agentcc.services.siem_delivery import deliver_due


class Command(BaseCommand):
    help = "Send the SIEM exports that are due."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        result = deliver_due(limit=max(1, min(options["limit"], 1000)))
        self.stdout.write(json.dumps(result, sort_keys=True))
