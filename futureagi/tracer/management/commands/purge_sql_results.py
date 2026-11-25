from django.core.management.base import BaseCommand

from tracer.sql_workbench.service import purge_expired_results


class Command(BaseCommand):
    help = "Erase expired SQL result bodies; delete terminal audit records only under an explicit configured policy."

    def handle(self, *args, **options):
        result = purge_expired_results(batch_size=1000)
        self.stdout.write(
            f"Purged {result['expired_result_bodies']} result payloads and {result['old_audit_rows']} audit records under the configured policy."
        )
