import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a new user account"

    def add_arguments(self, parser):
        parser.add_argument("--email", help="User email address")
        parser.add_argument("--name", help="Full name")
        parser.add_argument("--password", help="Password (omit to be prompted)")

    def handle(self, *args, **options):
        email = options["email"] or input("Email: ").strip()
        name = options["name"] or input("Full name: ").strip()
        password = options["password"] or getpass.getpass("Password: ")

        from accounts.utils import create_owner_account

        try:
            user = create_owner_account(email, name, password)
        except ValidationError as exc:
            raise CommandError("\n".join(exc.messages)) from None
        self.stdout.write(
            self.style.SUCCESS(f"User '{user.email}' created successfully.")
        )
        self.stdout.write("You can now log in at your instance URL.")
