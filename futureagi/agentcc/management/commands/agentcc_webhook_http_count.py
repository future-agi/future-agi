"""Value-free inventory; this command never changes endpoints or delivery policy."""

from django.core.management.base import BaseCommand

from agentcc.models.webhook import AgentccWebhook


class Command(BaseCommand):
    requires_system_checks = []
    requires_migrations_checks = False

    def handle(self, *args, **options):
        # Count stored rows across tenants, including inactive/soft-deleted
        # endpoints. Only an aggregate reaches this process, never row values.
        count = AgentccWebhook._base_manager.filter(url__istartswith="http:").count()
        self.stdout.write(str(count))
