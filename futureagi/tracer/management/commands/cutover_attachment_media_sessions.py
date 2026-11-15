"""One-time, operator-gated invalidation of unreleased legacy media credentials.

No startup hook, wire-version column, or access-token/login-family mutation.
See docs/attachments/media-session-cutover.rst before use.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from tracer.models.attachment import AttachmentMediaSession


class Command(BaseCommand):
    help = "Revoke only media sessions issued through a fixed, recorded legacy cutoff (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--issued-through", required=True)
        parser.add_argument("--quiesced", action="store_true")
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if (
            getattr(settings, "FI_ATTACHMENTS_ENABLED", False)
            or not options["quiesced"]
        ):
            raise CommandError(
                "Disable the feature on ALL workers and quiesce legacy issuance first."
            )
        try:
            cutoff = parse_datetime(options["issued_through"])
        except ValueError:
            cutoff = None
        now = timezone.now()
        if cutoff is None or timezone.is_naive(cutoff) or cutoff > now:
            raise CommandError(
                "Use the recorded, timezone-aware past quiescence cutoff."
            )
        rows = AttachmentMediaSession.objects.filter(
            created_at__lte=cutoff, revoked_at__isnull=True
        )
        if options["apply"]:
            count = rows.update(revoked_at=now)
            self.stdout.write(f"revoked={count}; issued_through={cutoff.isoformat()}")
        else:
            self.stdout.write(
                f"would_revoke={rows.count()}; issued_through={cutoff.isoformat()}"
            )
