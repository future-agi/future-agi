import getpass

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

        if not email or not name or not password:
            raise CommandError("Email, name, and password are all required.")

        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")

        from accounts.utils import first_signup

        user = first_signup(
            {
                "email": email,
                "full_name": name,
                "password": password,
                "allow_email": True,
            }
        )
        self.stdout.write(
            self.style.SUCCESS(f"User '{user.email}' created successfully.")
        )
        self.stdout.write("You can now log in at your instance URL.")
