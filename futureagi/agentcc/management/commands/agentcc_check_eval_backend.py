"""Blocking, read-only deploy preflight for the 733 guardrail rollout.

Run in the target Django environment before image promotion/config re-push:
    python manage.py agentcc_check_eval_backend --gateway-fi-base-url "$GATEWAY_FI_BASE_URL"

Supply the gateway's effective FI_BASE_URL from the deployment manifest/runtime,
not this Django process's FI_BASE_URL. The latter can be the collector URL.
This check intentionally has no environment fallback and makes no HTTP calls.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from agentcc.services.eval_backend import check_eval_backend_alignment


class Command(BaseCommand):
    help = "Fail deployment unless Django BASE_URL matches the gateway FI_BASE_URL."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--gateway-fi-base-url",
            required=True,
            help="Effective FI_BASE_URL of the gateway deployment (not the collector).",
        )

    def handle(self, *args, **options):
        try:
            check_eval_backend_alignment(
                settings.BASE_URL, options["gateway_fi_base_url"]
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write("Django BASE_URL and gateway FI_BASE_URL are aligned.")
