from django.core.management.base import BaseCommand

from accounts.models.auth_token import AuthToken, AuthTokenOrigin


class Command(BaseCommand):
    help = "Deactivate all active SAML-origin access tokens."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tokens = AuthToken.no_workspace_objects.filter(
            auth_origin=AuthTokenOrigin.SAML, is_active=True
        )
        count = tokens.count()
        if options["dry_run"]:
            self.stdout.write(f"Would revoke {count} SAML access token(s).")
            return
        tokens.update(is_active=False)
        self.stdout.write(f"Revoked {count} SAML access token(s).")
