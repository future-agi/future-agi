import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from accounts.services.onboarding.support_state import build_onboarding_support_state


class Command(BaseCommand):
    help = "Inspect redacted onboarding state for support triage."

    def add_arguments(self, parser):
        parser.add_argument("--workspace-id", required=True)
        parser.add_argument("--user-id")
        parser.add_argument("--user-email")
        parser.add_argument(
            "--include-raw-activation-state",
            action="store_true",
            help="Include the raw activation-state payload in addition to the summary.",
        )
        parser.add_argument("--event-limit", type=int, default=5)
        parser.add_argument(
            "--format",
            choices=("json", "text"),
            default="json",
            dest="output_format",
        )
        parser.add_argument("--pretty", action="store_true")

    def handle(self, *args, **options):
        try:
            payload = build_onboarding_support_state(
                workspace_id=options["workspace_id"],
                user_id=options.get("user_id"),
                user_email=options.get("user_email"),
                include_raw_activation_state=options.get(
                    "include_raw_activation_state", False
                ),
                event_limit=max(1, options.get("event_limit") or 5),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if options["output_format"] == "text":
            self._write_text(payload)
            return

        self.stdout.write(
            json.dumps(
                payload,
                cls=DjangoJSONEncoder,
                indent=2 if options["pretty"] else None,
                sort_keys=True,
            )
        )

    def _write_text(self, payload):
        identity = payload["identity"]
        activation_state = payload["activation_state"]
        readiness = payload["support_readiness"]
        recommendation = activation_state.get("recommended_action") or {}
        sample = (
            activation_state.get("sample_project")
            or payload["logs"].get("latest_sample_project")
            or {}
        )
        lifecycle = (
            payload["logs"].get("latest_lifecycle_send")
            or payload["logs"].get("latest_lifecycle_evaluation")
            or payload["activation_state"].get("lifecycle")
            or {}
        )

        self.stdout.write("onboarding support state")
        self.stdout.write(f"schema_version={payload['schema_version']}")
        self.stdout.write(f"workspace_id={identity['workspace']['id']}")
        self.stdout.write(f"user_id={identity['user']['id']}")
        self.stdout.write(f"user_email_masked={identity['user']['email_masked']}")
        self.stdout.write(f"activation_stage={activation_state.get('stage')}")
        self.stdout.write(f"primary_path={activation_state.get('primary_path')}")
        self.stdout.write(f"recommended_action={recommendation.get('id')}")
        self.stdout.write(
            "recommended_route="
            f"{activation_state.get('current_resolved_recommended_route')}"
        )
        self.stdout.write(f"sample_status={sample.get('status')}")
        self.stdout.write(
            "lifecycle_context="
            f"{lifecycle.get('campaign_key') or lifecycle.get('status')}"
        )
        self.stdout.write(f"support_ready={readiness['ready']}")
        self.stdout.write(f"missing={','.join(readiness['missing'])}")
