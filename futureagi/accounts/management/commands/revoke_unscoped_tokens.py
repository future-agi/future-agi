from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenType


class Command(BaseCommand):
    help = "Deactivate legacy unscoped access tokens created before a timestamp."

    def add_arguments(self, parser):
        parser.add_argument("--created-before", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            created_before = datetime.fromisoformat(options["created_before"])
        except ValueError as exc:
            raise CommandError("--created-before must be ISO-8601") from exc
        if timezone.is_naive(created_before):
            created_before = timezone.make_aware(created_before)
        tokens = AuthToken.no_workspace_objects.filter(
            auth_origin__isnull=True,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            created_at__lt=created_before,
        )
        count = tokens.count()
        if options["dry_run"]:
            self.stdout.write(f"Would revoke {count} unscoped access token(s).")
            return
        tokens.update(is_active=False)
        self.stdout.write(f"Revoked {count} unscoped access token(s).")
