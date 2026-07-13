"""
Backfill call_logs into span_attributes for existing conversation spans
that have artifact.logUrl in their raw_log but no call_logs yet.

Usage:
    python manage.py backfill_call_logs
    python manage.py backfill_call_logs --dry-run
    python manage.py backfill_call_logs --limit 100
    python manage.py backfill_call_logs --project-id <uuid>
"""

from django.core.management.base import BaseCommand

from tracer.models.observation_span import ObservationSpan
from tracer.selectors import get_agent_api_key
from tracer.utils.vapi import _extract_call_logs


class Command(BaseCommand):
    help = "Backfill call_logs from VAPI logUrl into conversation span_attributes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only count spans that need backfill, don't download anything",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of spans to process (0 = all)",
        )
        parser.add_argument(
            "--project-id",
            type=str,
            default=None,
            help="Only process spans for a specific project",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        project_id = options.get("project_id")

        # Resolve the Vapi api_key up front when --project-id is supplied so
        # every span in this batch can use the authenticated call-log
        # endpoint. Without --project-id, the api_key stays None and the
        # download falls back to the legacy unauthenticated URL.
        api_key = get_agent_api_key(project_id, "vapi") if project_id else None
        if project_id and not api_key:
            self.stdout.write(
                self.style.WARNING(
                    "No Vapi api_key found for --project-id; falling back to "
                    "the legacy call-log URL."
                )
            )

        qs = ObservationSpan.objects.filter(observation_type="conversation")

        if project_id:
            qs = qs.filter(trace__project_id=project_id)

        # Fetch all, then filter in Python since JSONField lookups for nested
        # keys with __isnull can be unreliable across DB backends.
        spans = qs.only("id", "span_attributes").iterator(chunk_size=100)

        candidates = []
        total_scanned = 0
        for span in spans:
            total_scanned += 1
            attrs = span.span_attributes or {}
            raw_log = attrs.get("raw_log", {})
            if not isinstance(raw_log, dict):
                continue
            log_url = raw_log.get("artifact", {}).get("logUrl")
            if not log_url:
                continue
            if "call_logs" in attrs:
                continue
            candidates.append(span)
            if limit and len(candidates) >= limit:
                break

        self.stdout.write(
            f"Scanned {total_scanned} conversation spans, "
            f"found {len(candidates)} needing backfill"
        )

        if dry_run:
            self.stdout.write("Dry run — no changes made.")
            return

        success = 0
        failed = 0
        for i, span in enumerate(candidates):
            raw_log = span.span_attributes.get("raw_log", {})
            call_id = raw_log.get("id", span.id)
            self.stdout.write(
                f"[{i + 1}/{len(candidates)}] Processing span {span.id} "
                f"(call {call_id})..."
            )

            attrs = dict(span.span_attributes)
            _extract_call_logs(raw_log, attrs, api_key=api_key)

            if "call_logs" in attrs:
                span.span_attributes = attrs
                span.save(update_fields=["span_attributes"])
                log_count = len(attrs["call_logs"])
                self.stdout.write(
                    self.style.SUCCESS(f"  -> Stored {log_count} log entries")
                )
                success += 1
            else:
                self.stdout.write(
                    self.style.WARNING("  -> Failed to download logs (see warnings)")
                )
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. Success: {success}, Failed: {failed}")
        )
