"""
Seed system-defined NodeTemplate records (idempotent).

Core logic lives in :func:`seed_node_templates` so both the post_migrate
signal (AgentPlaygroundConfig) and this command share one implementation.
New templates are auto-seeded on deploy; this command is kept for manual
re-seeding / dry-run inspection.

Usage:
    python manage.py seed_node_templates
    python manage.py seed_node_templates --dry-run
    python manage.py seed_node_templates --template llm_prompt
"""

from typing import Callable, Optional

from django.core.management.base import BaseCommand

from agent_playground.templates import get_all_templates

# Fields that can be safely updated without breaking existing Node records
SAFE_UPDATE_FIELDS = {"display_name", "description", "icon", "categories"}

# Structural fields that can only be set on creation - changing these would break
# existing Node records that reference the template
PROTECTED_FIELDS = {
    "input_definition",
    "output_definition",
    "input_mode",
    "output_mode",
    "config_schema",
}


def seed_node_templates(
    node_template_model,
    template_filter: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> dict:
    """Upsert template definitions into ``node_template_model`` (real or historical).

    Creates missing templates with all fields; existing ones are only
    updated on ``SAFE_UPDATE_FIELDS`` — protected structural fields are
    logged as a warning if they've drifted, never overwritten.
    """
    emit = log or (lambda _msg: None)
    emit_warn = warn or emit

    templates = get_all_templates()
    if template_filter:
        templates = {template_filter: templates[template_filter]}

    created, updated = 0, 0
    for name, definition in templates.items():
        defaults = {k: v for k, v in definition.items() if k != "name"}
        existing = node_template_model.objects.filter(
            name=name, deleted=False
        ).first()

        if existing:
            for field in PROTECTED_FIELDS:
                if getattr(existing, field) != defaults.get(field):
                    emit_warn(
                        f"Template '{name}': Cannot update protected field "
                        f"'{field}'. Create a new template for structural changes."
                    )
            for field in SAFE_UPDATE_FIELDS:
                setattr(existing, field, defaults[field])
            existing.save()
            updated += 1
            emit(f"Updated template: '{name}' (metadata only)")
        else:
            node_template_model.objects.create(name=name, **defaults)
            created += 1
            emit(f"Created template: '{name}'")

    return {"created": created, "updated": updated}


class Command(BaseCommand):
    help = "Seed system-defined NodeTemplate records (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without writing to the database.",
        )
        parser.add_argument(
            "--template",
            type=str,
            help="Seed only the specified template by name.",
        )

    def handle(self, *args, **options):
        from agent_playground.models.node_template import NodeTemplate

        template_filter = options.get("template")
        if template_filter and template_filter not in get_all_templates():
            self.stderr.write(
                self.style.ERROR(f"Unknown template: '{template_filter}'")
            )
            return

        if options["dry_run"]:
            templates = get_all_templates()
            if template_filter:
                templates = {template_filter: templates[template_filter]}
            for name, definition in templates.items():
                self.stdout.write(
                    self.style.WARNING(f"[DRY RUN] Would upsert template: '{name}'")
                )
                for field, value in definition.items():
                    if field != "name":
                        self.stdout.write(f"  {field}: {value}")
            return

        seed_node_templates(
            NodeTemplate,
            template_filter=template_filter,
            log=lambda msg: self.stdout.write(self.style.SUCCESS(msg)),
            warn=lambda msg: self.stderr.write(self.style.WARNING(msg)),
        )
