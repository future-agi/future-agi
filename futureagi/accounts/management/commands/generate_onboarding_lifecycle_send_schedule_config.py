from __future__ import annotations

import json
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from accounts.services.onboarding.lifecycle_schedule_config import (
    lifecycle_send_schedule_config_result,
)


class Command(BaseCommand):
    help = "Write scheduler env config from validated onboarding lifecycle launch artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--approval-manifest", required=True)
        parser.add_argument("--approval-record", required=True)
        parser.add_argument("--dry-run-report", required=True)
        parser.add_argument("--dry-run-report-review-record", required=True)
        parser.add_argument("--launch-packet", required=True)
        parser.add_argument("--output")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--enable", action="store_true")
        parser.add_argument("--max-limit", type=int)
        parser.add_argument("--format", choices=["env", "json"], default="env")

    def handle(self, *args, **options):
        try:
            result = lifecycle_send_schedule_config_result(
                approval_manifest_path=options["approval_manifest"],
                approval_record_path=options["approval_record"],
                dry_run_report_path=options["dry_run_report"],
                dry_run_report_review_record_path=(
                    options["dry_run_report_review_record"]
                ),
                launch_packet_path=options["launch_packet"],
                enable=options["enable"],
                max_limit=options.get("max_limit"),
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        if options["format"] == "json":
            content = json.dumps(result.to_payload(), indent=2, sort_keys=True) + "\n"
        else:
            content = result.to_env_text()

        output_path = options.get("output")
        if output_path:
            path = Path(output_path)
            if path.exists() and not options["force"]:
                raise CommandError(f"{path} already exists. Use --force to overwrite.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.stdout.write(f"output_path={path}")
        else:
            self.stdout.write(content, ending="")
            return

        payload = result.to_payload()
        self.stdout.write(f"enabled={str(payload['enabled']).lower()}")
        self.stdout.write(f"cohort={payload['command']['cohort']}")
        self.stdout.write(f"limit={payload['command']['limit']}")
        self.stdout.write(f"max_limit={payload['command']['max_limit']}")
        self.stdout.write(
            f"launch_packet_sha256={payload['artifacts']['launch_packet']['sha256']}"
        )
