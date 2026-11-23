"""Queue derived-store erasure after upstream source deletion."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from tracer.services.derived_metrics.erasure import erase_metric_data
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)


class Command(BaseCommand):
    help = "Queue durable metric erasure. Delete upstream telemetry first; the 15-minute worker resumes cleanup."

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True)
        parser.add_argument("--project")
        parser.add_argument(
            "--scope",
            required=True,
            choices=["organization", "project", "users", "sessions"],
        )
        parser.add_argument(
            "--subjects-file",
            help="Private JSON array of subject IDs; never place IDs on the command line.",
        )

    def handle(self, *args, **options):
        try:
            subjects = (
                json.loads(Path(options["subjects_file"]).read_text())
                if options["subjects_file"]
                else []
            )
            if not isinstance(subjects, list):
                raise ValueError
            request = erase_metric_data(
                organization_id=options["organization"],
                project_id=options["project"],
                scope_kind=options["scope"],
                subject_ids=subjects,
            )
        except Exception as exc:
            private_logger.exception("metric_erasure_queue_failed", exc)
            raise CommandError(
                "Erasure was not queued. Check scope and private subject-file format."
            ) from None
        self.stdout.write(
            f"Queued erasure {request.id}; completion requires physical-store receipts."
        )
