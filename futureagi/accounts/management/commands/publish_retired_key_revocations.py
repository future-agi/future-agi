"""Deploy-time replay of retired-key cache invalidation (safe to repeat)."""

from django.core.management.base import BaseCommand

from accounts.api_key_constants import RETIREMENT_REASON
from accounts.api_key_revocation import publish_key_revocation
from accounts.models import OrgApiKey


class Command(BaseCommand):
    help = "Publish every retired API key's cache invalidation and flag MCP reconnects."

    def handle(self, *args, **options):
        from mcp_server.models.connection import MCPConnection

        rows = OrgApiKey._base_manager.filter(retired_reason=RETIREMENT_REASON)
        count = 0
        for key in rows.iterator(chunk_size=500):
            publish_key_revocation(key, strict=True)
            MCPConnection._base_manager.filter(api_key_id=key.pk).update(
                is_active=False, reconnect_required=True
            )
            count += 1
        self.stdout.write(
            f"Published revocation for {count} retired keys; safe to rerun."
        )
