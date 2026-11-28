"""Start trajectory item erasure for projects whose spans are already deleted.

GDPR path on installs without hosted retention: delete the project's spans
first, then run this command. It refuses while the project is live in Postgres
or any replica still holds a span row for it, and otherwise starts (or joins)
the TrajectoryErasureWorkflow, which deletes the items, clears the collector
seen sets and checks again after 72 hours. It never deletes inline.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2.trajectory_lifecycle import (
    TrajectoryErasurePending,
    erasure_gate,
    request_trajectory_erasure,
)
from tracer.services.trajectory_seen_contract import canonical_project_id


class Command(BaseCommand):
    help = "Erase a deleted project's trajectory items (after its spans are gone)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            action="append",
            required=True,
            help="Project UUID; repeat for several projects.",
        )

    def handle(self, *args, **options):
        try:
            projects = sorted({canonical_project_id(p) for p in options["project"]})
        except ValueError:
            raise CommandError("--project must be a project UUID") from None
        for pid in projects:
            try:
                erasure_gate(pid)
            except TrajectoryErasurePending as exc:
                raise CommandError(
                    f"Refusing to erase {pid}: {exc}. Delete the project and its "
                    "spans first."
                ) from None
        for workflow_id in request_trajectory_erasure(projects):
            self.stdout.write(f"Started {workflow_id}")
