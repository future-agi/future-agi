"""Bounded preparation or repair of historical escalation evidence."""

from django.core.management.base import BaseCommand, CommandError

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.services.feed_escalation_evidence import prepare_issue_evidence


class Command(BaseCommand):
    help = "Capture original source occurrence times for Feed issues; never synthesize publication times."

    def add_arguments(self, parser):
        parser.add_argument("--workspace-id", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--max-issues", type=int, default=1000)

    def handle(self, *args, **options):
        if (
            not 1 <= options["batch_size"] <= 500
            or not 1 <= options["max_issues"] <= 10000
        ):
            raise CommandError("Use batch-size 1–500 and max-issues 1–10000")
        qs = TraceErrorGroup.no_workspace_objects.filter(
            target_type="error_feed",
            project__workspace_id=options["workspace_id"],
        ).order_by("id")
        prepared = unresolved = issues = 0
        for issue_id in qs.values_list("id", flat=True)[
            : options["max_issues"]
        ].iterator(chunk_size=100):
            result = prepare_issue_evidence(
                issue_id, limit=options["batch_size"], dry_run=options["dry_run"]
            )
            prepared += result["prepared"]
            unresolved += result["unresolved"]
            issues += 1
        self.stdout.write(
            f"issues={issues} prepared={prepared} unresolved={unresolved} dry_run={options['dry_run']}"
        )
