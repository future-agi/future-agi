"""
Django management command to register Temporal schedules.

Usage:
    python manage.py register_temporal_schedules
    python manage.py register_temporal_schedules --list
    python manage.py register_temporal_schedules --delete-all
    python manage.py register_temporal_schedules --pause <schedule_id>
    python manage.py register_temporal_schedules --unpause <schedule_id>
    python manage.py register_temporal_schedules --trigger <schedule_id>
"""

import asyncio

from django.core.management.base import BaseCommand, CommandError

from tfc.temporal import (
    ALL_SCHEDULES,
    MODEL_HUB_SCHEDULES,
    PROPERTY_CATALOG_SCHEDULES,
)
from tfc.temporal.common.client import get_client
from tfc.temporal.schedules import (
    a_delete_schedule,
    a_describe_schedule,
    a_list_schedules,
    a_pause_schedule,
    a_register_schedules,
    a_trigger_schedule,
    a_unpause_schedule,
)


class Command(BaseCommand):
    help = "Register and manage Temporal schedules (replacing Celery Beat)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all registered schedules",
        )
        parser.add_argument(
            "--delete-all",
            action="store_true",
            help="Delete all schedules",
        )
        parser.add_argument(
            "--model-hub-only",
            action="store_true",
            help="Only register model_hub schedules",
        )
        parser.add_argument(
            "--property-catalog-only",
            action="store_true",
            help="Only register the one reviewed DEV property-catalog schedule",
        )
        parser.add_argument(
            "--pause",
            type=str,
            metavar="SCHEDULE_ID",
            help="Pause a specific schedule",
        )
        parser.add_argument(
            "--unpause",
            type=str,
            metavar="SCHEDULE_ID",
            help="Unpause a specific schedule",
        )
        parser.add_argument(
            "--trigger",
            type=str,
            metavar="SCHEDULE_ID",
            help="Trigger a specific schedule immediately",
        )
        parser.add_argument(
            "--describe",
            type=str,
            metavar="SCHEDULE_ID",
            help="Describe a specific schedule",
        )

    def handle(self, *args, **options):
        asyncio.run(self._handle_async(options))

    async def _handle_async(self, options):
        action_names = ("list", "delete_all", "pause", "unpause", "trigger", "describe")
        scoped_registration = (
            options["model_hub_only"] or options["property_catalog_only"]
        )
        if options["model_hub_only"] and options["property_catalog_only"]:
            raise CommandError(
                "--model-hub-only and --property-catalog-only are mutually exclusive"
            )
        if scoped_registration and any(options[name] for name in action_names):
            raise CommandError(
                "registration scope flags cannot be combined with schedule actions"
            )

        if options["property_catalog_only"]:
            schedules = PROPERTY_CATALOG_SCHEDULES
            if len(schedules) != 1:
                raise CommandError(
                    "property-catalog-only registration requires exactly one configured schedule"
                )
        elif options["model_hub_only"]:
            schedules = MODEL_HUB_SCHEDULES
        else:
            schedules = ALL_SCHEDULES

        client = await get_client()

        if options["list"]:
            await self._list_schedules(client)
            return

        if options["delete_all"]:
            await self._delete_all_schedules(client)
            return

        if options["pause"]:
            await self._pause_schedule(client, options["pause"])
            return

        if options["unpause"]:
            await self._unpause_schedule(client, options["unpause"])
            return

        if options["trigger"]:
            await self._trigger_schedule(client, options["trigger"])
            return

        if options["describe"]:
            await self._describe_schedule(client, options["describe"])
            return

        # Register schedules
        self.stdout.write(f"Registering {len(schedules)} schedules...")

        # A scoped registration must not treat schedules outside its scope as
        # orphans. Only a full registration owns the complete schedule set.
        await a_register_schedules(
            client,
            schedules,
            cleanup_orphans=not scoped_registration,
        )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully registered {len(schedules)} schedules")
        )

        # List what was registered
        self.stdout.write("\nRegistered schedules:")
        for config in schedules:
            self.stdout.write(
                f"  - {config.schedule_id}: {config.activity_name} "
                f"(every {config.interval_seconds}s on queue '{config.queue}')"
            )

    async def _list_schedules(self, client):
        self.stdout.write("Listing all Temporal schedules...")
        schedule_ids = await a_list_schedules(client)

        if not schedule_ids:
            self.stdout.write("No schedules found.")
            return

        self.stdout.write(f"\nFound {len(schedule_ids)} schedules:")
        for schedule_id in schedule_ids:
            self.stdout.write(f"  - {schedule_id}")

    async def _delete_all_schedules(self, client):
        self.stdout.write("Deleting all Temporal schedules...")
        schedule_ids = await a_list_schedules(client)

        if not schedule_ids:
            self.stdout.write("No schedules to delete.")
            return

        deleted_count = 0
        for schedule_id in schedule_ids:
            if await a_delete_schedule(client, schedule_id):
                deleted_count += 1
                self.stdout.write(f"  Deleted: {schedule_id}")

        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted_count} schedules"))

    async def _pause_schedule(self, client, schedule_id: str):
        self.stdout.write(f"Pausing schedule: {schedule_id}")
        await a_pause_schedule(
            client, schedule_id, note="Paused via management command"
        )
        self.stdout.write(self.style.SUCCESS(f"Paused: {schedule_id}"))

    async def _unpause_schedule(self, client, schedule_id: str):
        self.stdout.write(f"Unpausing schedule: {schedule_id}")
        await a_unpause_schedule(
            client, schedule_id, note="Unpaused via management command"
        )
        self.stdout.write(self.style.SUCCESS(f"Unpaused: {schedule_id}"))

    async def _trigger_schedule(self, client, schedule_id: str):
        self.stdout.write(f"Triggering schedule: {schedule_id}")
        await a_trigger_schedule(client, schedule_id)
        self.stdout.write(self.style.SUCCESS(f"Triggered: {schedule_id}"))

    async def _describe_schedule(self, client, schedule_id: str):
        self.stdout.write(f"Describing schedule: {schedule_id}")
        description = await a_describe_schedule(client, schedule_id)
        self.stdout.write(f"\nSchedule: {schedule_id}")
        self.stdout.write(
            f"  State: {'paused' if description.schedule.state.paused else 'running'}"
        )
        self.stdout.write(f"  Note: {description.schedule.state.note or 'N/A'}")
        if description.info.recent_actions:
            self.stdout.write(
                f"  Recent actions: {len(description.info.recent_actions)}"
            )
        if description.info.next_action_times:
            next_time = description.info.next_action_times[0]
            self.stdout.write(f"  Next run: {next_time}")
