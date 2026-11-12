"""Grant or revoke the `hub_maintainer` permission (SPEC §5.1). Audited as maintainer.granted|revoked.

    python manage.py omega_hub_maintainer grant --email b@example.com --by a@example.com
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from omega_hub.permissions import is_maintainer, maintainer_permission
from omega_hub.signals import grant_context


class Command(BaseCommand):
    help = "Grant or revoke the Omega Hub maintainer permission."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["grant", "revoke"])
        parser.add_argument("--email", required=True)
        parser.add_argument("--by", required=True, help="Email of the person making the change (recorded in the audit log).")

    def handle(self, *args, action, email, by, **options):
        users = get_user_model().objects
        user = users.filter(email=email).first()
        actor = users.filter(email=by).first()
        if user is None or actor is None:
            raise CommandError("both --email and --by must name existing users")
        perm = maintainer_permission()
        with grant_context(via="command", by=actor):
            if action == "grant":
                user.user_permissions.add(perm)
            else:
                user.user_permissions.remove(perm)
        user = users.get(pk=user.pk)
        self.stdout.write(f"{action} {email}: is a maintainer now = {is_maintainer(user)}")
        if action == "grant" and not is_maintainer(user):
            self.stdout.write("Note: a maintainer must also be an Admin or Owner of OMEGA_HUB_MAINTAINER_ORG_ID.")
