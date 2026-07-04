"""Register the eval-task Temporal Search Attributes on the namespace.

Deploy prerequisite (§13.7): run this once before the eval-task workflows start
upserting attributes — an unregistered upsert wedges the Workflow Task forever.
Idempotent, so it's safe to re-run on every deploy.
"""

import asyncio

from django.core.management.base import BaseCommand

from tfc.temporal import TEMPORAL_NAMESPACE
from tfc.temporal.common.client import get_client
from tfc.temporal.eval_tasks.registration import register_search_attributes
from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES


class Command(BaseCommand):
    help = "Register eval-task Temporal search attributes (idempotent)."

    def handle(self, *args, **options):
        registered = asyncio.run(self._run())
        names = ", ".join(SEARCH_ATTRIBUTE_NAMES)
        if registered:
            self.stdout.write(
                self.style.SUCCESS(f"Registered search attributes: {names}")
            )
        else:
            self.stdout.write(f"Search attributes already registered: {names}")

    async def _run(self) -> bool:
        client = await get_client()
        return await register_search_attributes(client, TEMPORAL_NAMESPACE)
