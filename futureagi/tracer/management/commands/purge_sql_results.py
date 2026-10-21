from django.core.management.base import BaseCommand
from django.utils import timezone

from tracer.models.sql_query import SQLExecution


class Command(BaseCommand):
    help = (
        "Erase expired SQL result payloads in bounded batches; retain audit metadata."
    )

    def handle(self, *args, **options):
        ids = list(
            SQLExecution.objects.filter(expires_at__lte=timezone.now())
            .exclude(result={})
            .values_list("id", flat=True)[:1000]
        )
        count = SQLExecution.objects.filter(id__in=ids).update(result={})
        self.stdout.write(f"Purged {count} expired result payloads.")
