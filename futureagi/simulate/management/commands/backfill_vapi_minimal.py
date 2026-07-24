import os
import re
import uuid

from django.core.management.base import BaseCommand, CommandError

from temporalio.common import WorkflowIDReusePolicy

from tfc.temporal import start_workflow_sync
from tfc.temporal.backfill.minimal_backfill import (
    VapiBackfillInput,
    VapiMinimalBackfillWorkflow,
    reconcile_backfill_sample,
)
from tfc.temporal.common.client import (
    cancel_workflow_sync,
    query_workflow_sync,
    signal_workflow_sync,
)


class Command(BaseCommand):
    requires_system_checks: list[str] = []
    help = "Launch or control the historical Vapi recording backfill."

    def add_arguments(self, parser):
        parser.add_argument(
            "--action",
            choices=("start", "status", "pause", "resume", "cancel", "reconcile"),
            default="start",
        )
        parser.add_argument("--workflow-id")
        parser.add_argument(
            "--source", choices=("simulation", "observability"), default="simulation"
        )
        parser.add_argument("--project-id")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--proof-gate", type=int, choices=(10, 100, 1000))
        parser.add_argument("--limit", type=int)
        parser.add_argument("--shards", type=int, default=1)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--run-id")
        parser.add_argument(
            "--min-records-per-second",
            type=float,
            default=float(os.getenv("BACKFILL_MIN_RECORDS_PER_SECOND", "0")),
        )

    def handle(self, *args, **options):
        if options["action"] == "reconcile":
            # Observability without --project-id = safe map-only all-project scan.
            # With --project-id = map + day-chunked attributes_extra counts.
            report = reconcile_backfill_sample(
                VapiBackfillInput(
                    source=options["source"],
                    project_id=options["project_id"],
                )
            )
            self.stdout.write(str(report))
            return

        if options["action"] != "start":
            workflow_id = options["workflow_id"]
            if not workflow_id:
                raise CommandError(
                    "--workflow-id is required for status/pause/resume/cancel"
                )
            if options["action"] == "status":
                self.stdout.write(str(query_workflow_sync(workflow_id, "status")))
                return
            delivered = signal_workflow_sync(workflow_id, options["action"])
            if not delivered:
                raise CommandError(f"Could not signal {workflow_id}")
            self.stdout.write(
                self.style.SUCCESS(f"Sent {options['action']} to {workflow_id}")
            )
            return

        if options["shards"] < 1:
            raise CommandError("--shards must be positive")
        if not 1 <= options["batch_size"] <= 100:
            raise CommandError("--batch-size must be between 1 and 100")
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit must be positive")
        if options["source"] == "observability" and not options["project_id"]:
            raise CommandError("--project-id is required for observability")
        if options["project_id"]:
            try:
                uuid.UUID(options["project_id"])
            except ValueError as exc:
                raise CommandError("--project-id must be a UUID") from exc
        if options["proof_gate"] and options["source"] != "observability":
            raise CommandError("--proof-gate is only valid for observability")
        if options["proof_gate"] and options["shards"] != 1:
            raise CommandError(
                "--proof-gate requires --shards 1 so its row count is exact"
            )
        if options["min_records_per_second"] < 0:
            raise CommandError("--min-records-per-second cannot be negative")
        if not os.getenv("VAPI_API_RATE_LIMIT_PER_SECOND"):
            self.stdout.write(
                self.style.WARNING(
                    "VAPI_API_RATE_LIMIT_PER_SECOND is unset; authenticated API fallback is disabled"
                )
            )
        limit = options["proof_gate"] or options["limit"]
        run_id = (
            options["run_id"] or f"vapi_{options['source']}_{uuid.uuid4().hex[:12]}"
        )
        if not re.fullmatch(r"[A-Za-z0-9_]+", run_id):
            raise CommandError(
                "--run-id may contain only letters, digits, and underscores"
            )
        started: list[str] = []
        try:
            for shard in range(options["shards"]):
                workflow_id = (
                    f"vapi-backfill-{options['source']}-"
                    f"{options['project_id'] or 'all'}-{run_id}-{shard}"
                )
                start_workflow_sync(
                    VapiMinimalBackfillWorkflow,
                    VapiBackfillInput(
                        source=options["source"],
                        dry_run=options["dry_run"],
                        limit=limit,
                        project_id=options["project_id"],
                        shards=options["shards"],
                        shard=shard,
                        batch_size=options["batch_size"],
                        run_id=run_id,
                        proof_gate=options["proof_gate"],
                        min_records_per_second=options["min_records_per_second"],
                    ),
                    workflow_id=workflow_id,
                    task_queue="backfill",
                    # Fail closed: never terminate an in-flight backfill by ID reuse.
                    cancel_existing=False,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                )
                started.append(workflow_id)
                self.stdout.write(self.style.SUCCESS(f"Started {workflow_id}"))
        except Exception as exc:
            for workflow_id in started:
                try:
                    cancel_workflow_sync(workflow_id)
                except Exception as cancel_exc:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Failed to cancel {workflow_id} after partial start: {cancel_exc}"
                        )
                    )
            if started:
                raise CommandError(
                    f"Multi-shard start failed after launching {len(started)} "
                    f"workflow(s); cancelled: {', '.join(started)}. Error: {exc}"
                ) from exc
            raise
