import getpass

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Set a new password for an existing account. Recovery path for "
        "self-hosted deployments with no mail delivery configured."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Account email address")
        parser.add_argument("--password", help="New password (omit to be prompted)")

    def handle(self, *args, **options):
        from accounts.models import User
        from accounts.models.auth_token import AuthToken, AuthTokenType

        email = (options["email"] or input("Email: ")).strip().lower()
        password = options["password"] or getpass.getpass("New password: ")

        if not email or not password:
            raise CommandError("Email and password are both required.")

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise CommandError(f"No account found for '{email}'.")

        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("\n".join(exc.messages))

        user.password = make_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])

        token_ids = list(
            AuthToken.objects.filter(
                user=user, is_active=True, auth_type=AuthTokenType.ACCESS.value
            ).values_list("id", flat=True)
        )
        try:
            for token_id in token_ids:
                cache.delete(f"access_token_{token_id}")
        except Exception:
            pass
        AuthToken.objects.filter(id__in=token_ids).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(f"Password updated for '{user.email}'.")
        )
        self.stdout.write("Existing sessions have been signed out.")
