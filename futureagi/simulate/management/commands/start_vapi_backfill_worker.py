from django.core.management.base import BaseCommand

from tfc.temporal.backfill.start_backfill_worker import main


class Command(BaseCommand):
    requires_system_checks: list[str] = []
    help = "Start the dedicated single-slot Vapi backfill Temporal worker."

    def handle(self, *args, **options):
        main()
