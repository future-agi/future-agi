"""Report saved eval configs whose mapping holds a non-path value.

An eval mapping value is an attribute path string; every resolver splits it on
".". Rows written before the API refused non-strings can still hold an object
or a list, and those rows produce no eval result. This command finds them so
support can tell the owner which eval to re-map.

It deliberately does not repair. The correct path for a broken value is only
knowable by whoever configured the eval, and guessing one would silently
evaluate the wrong attribute. The repair surface is the product: the mapping
panel now renders such a value as "invalid mapping" instead of tearing the page
down, so the owner can open the eval and set the path themselves.

Usage:
    python manage.py scan_eval_mapping_paths
    python manage.py scan_eval_mapping_paths --project-id <uuid>
    python manage.py scan_eval_mapping_paths --organization-id <uuid>
"""

from django.core.management.base import BaseCommand

from model_hub.utils.eval_mapping import WHOLE_MAPPING_KEY, non_path_mapping_keys
from tracer.models.custom_eval_config import CustomEvalConfig


class Command(BaseCommand):
    help = (
        "List eval configs whose mapping holds a value that is not an "
        "attribute path string. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            help="Restrict the scan to one project.",
        )
        parser.add_argument(
            "--organization-id",
            help="Restrict the scan to one organization.",
        )

    def handle(self, *args, **options):
        queryset = CustomEvalConfig.no_workspace_objects.select_related(
            "project", "project__organization"
        )
        if options["project_id"]:
            queryset = queryset.filter(project_id=options["project_id"])
        if options["organization_id"]:
            queryset = queryset.filter(
                project__organization_id=options["organization_id"]
            )

        affected = 0
        for config in queryset.iterator():
            # This sweeps legacy rows of unknown shape, so one surprising row
            # must not cost the report on every row after it.
            try:
                bad_keys = non_path_mapping_keys(config.mapping)
            except Exception as exc:  # noqa: BLE001 - a support tool, keep going
                affected += 1
                self.stderr.write(f"eval_config={config.id} unreadable_mapping={exc!r}")
                continue
            if not bad_keys:
                continue
            affected += 1
            if bad_keys == [WHOLE_MAPPING_KEY]:
                details = f"{WHOLE_MAPPING_KEY}={type(config.mapping).__name__}"
            else:
                details = ", ".join(
                    f"{key}={type(config.mapping[key]).__name__}" for key in bad_keys
                )
            self.stdout.write(
                f"organization={config.project.organization_id} "
                f"project={config.project_id} "
                f"eval_config={config.id} name={config.name!r} "
                f"invalid_keys=[{details}]"
            )

        self.stdout.write(
            self.style.SUCCESS(f"affected_eval_configs={affected}")
            if affected == 0
            else self.style.WARNING(f"affected_eval_configs={affected}")
        )
