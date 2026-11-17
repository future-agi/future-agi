from django.core.management.base import BaseCommand

from agentcc.services.request_log_retention import run_retention


class Command(BaseCommand):
    help = (
        "Delete gateway Request Logs past each organization's retention, as the "
        "daily agentcc-request-log-retention schedule does. Organizations that "
        "keep logs forever are not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Only this organization (UUID).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be deleted; delete nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        results = run_retention(organization_id=options["org"], dry_run=dry_run)
        total = 0
        for r in results:
            if "skipped" in r:
                line = f"skipped ({r['skipped']})"
            elif dry_run:
                n = r["would_delete"]
                total += n
                line = f"would delete {n}{'+' if r['capped'] else ''}"
            else:
                total += r["deleted"]
                line = f"deleted {r['deleted']}"
                if not r.get("complete", True):
                    line += " (time budget spent; the next run continues)"
                if r.get("error"):
                    line += f"; error: {r['error']}"
            self.stdout.write(
                f"{r['organization_id']}: {line}, cutoff {r['cutoff'] or '-'}"
            )
        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            f"{verb} {total} request log rows across {len(results)} organizations."
        )
