"""
Management command: seed_system_evals

Loads system eval templates from YAML files and upserts them into the database.
Idempotent — safe to run multiple times, in dev, staging, or prod.

Usage:
    python manage.py seed_system_evals              # Normal run
    python manage.py seed_system_evals --dry-run    # Preview changes
    python manage.py seed_system_evals --force      # Re-apply all, ignore version cache
    python manage.py seed_system_evals --verbose     # Log each eval processed

YAML files are in: model_hub/system_evals/{function,agent,specialty}/*.yaml
"""

import os
from pathlib import Path

import structlog
import yaml
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = structlog.get_logger(__name__)

# Bump this when system evals change. Seeder skips if DB is already at this version.
SYSTEM_EVALS_VERSION = 15

# Postgres advisory-lock key. Serialises concurrent seed_evals() calls
# across pods so the bulk_create path can't race on new eval_ids. Any
# stable 64-bit constant works; this one is arbitrary.
_SEED_EVALS_LOCK_KEY = 0x5EED_E7A1

SYSTEM_EVALS_DIR = Path(__file__).resolve().parent.parent.parent / "system_evals"
CATALOG_YAML = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "evaluations"
    / "catalog"
    / "system_evals.yaml"
)


def _load_catalog_tags():
    """Load UI-facing tags from the evaluations catalog YAML.

    Returns a dict mapping eval name → list of tags.
    """
    if not CATALOG_YAML.exists():
        return {}
    try:
        with open(CATALOG_YAML) as f:
            catalog = yaml.safe_load(f) or {}
        return {
            name: entry.get("tags", [])
            for name, entry in catalog.items()
            if isinstance(entry, dict) and entry.get("tags")
        }
    except Exception as e:
        logger.warning("catalog_tags_load_error", error=str(e))
        return {}


def load_yaml_evals():
    """Load all YAML eval definitions from the system_evals directory."""
    catalog_tags = _load_catalog_tags()

    evals = []
    for subdir in ("function", "agent", "specialty"):
        dirpath = SYSTEM_EVALS_DIR / subdir
        if not dirpath.exists():
            continue
        for filepath in sorted(dirpath.glob("*.yaml")):
            try:
                with open(filepath) as f:
                    data = yaml.safe_load(f)
                if data:
                    data["_source_file"] = str(filepath.relative_to(SYSTEM_EVALS_DIR))
                    data["_track"] = subdir
                    # Merge UI tags from catalog (keyed by eval name)
                    eval_name = data.get("name") or filepath.stem
                    if eval_name in catalog_tags:
                        data["eval_tags"] = catalog_tags[eval_name]
                    evals.append(data)
            except Exception as e:
                logger.error("yaml_load_error", file=str(filepath), error=str(e))
    return evals


def seed_evals(dry_run=False, force=False, verbose=False):
    """
    Core seeding logic. Called by the management command and by apps.py on startup.

    Wrapped in a transaction-scoped advisory lock so concurrent pod startups
    can't race on bulk_create for newly-added eval_ids (TH-5501).

    Returns (created, updated, skipped) counts.
    """
    from django.core.cache import cache
    from django.db import connection

    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    with transaction.atomic():
        # Serialise concurrent seeders. pg_advisory_xact_lock releases at
        # COMMIT/ROLLBACK, so a killed pod can't leave an orphan lock.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s)", [_SEED_EVALS_LOCK_KEY]
            )

        # Re-check version cache INSIDE the lock — another pod may have
        # finished seeding while we were waiting for the lock.
        if not force:
            cached_version = cache.get("system_evals_version", 0)
            if cached_version >= SYSTEM_EVALS_VERSION:
                if verbose:
                    logger.info("system_evals_up_to_date", version=SYSTEM_EVALS_VERSION)
                return 0, 0, 0

        yaml_evals = load_yaml_evals()
        if not yaml_evals:
            logger.warning("no_yaml_evals_found", dir=str(SYSTEM_EVALS_DIR))
            return 0, 0, 0

        # Build lookup of existing templates by eval_id
        existing_by_eval_id = {
            t.eval_id: t
            for t in EvalTemplate.no_workspace_objects.filter(
                eval_id__in=[e["eval_id"] for e in yaml_evals]
            )
        }

        created_count = 0
        updated_count = 0
        skipped_count = 0

        to_create = []
        to_update = []

        for eval_def in yaml_evals:
            eval_id = eval_def["eval_id"]
            name = eval_def["name"]

            # Build the template fields from YAML
            fields = _yaml_to_template_fields(eval_def)

            if eval_id in existing_by_eval_id:
                # Update existing
                existing = existing_by_eval_id[eval_id]
                changed = False
                for field_name, value in fields.items():
                    if getattr(existing, field_name, None) != value:
                        setattr(existing, field_name, value)
                        changed = True

                if changed:
                    to_update.append(existing)
                    updated_count += 1
                    if verbose:
                        logger.info("eval_updated", name=name, eval_id=eval_id)
                else:
                    skipped_count += 1
                    if verbose:
                        logger.info("eval_unchanged", name=name, eval_id=eval_id)
            else:
                # Create new
                to_create.append(EvalTemplate(**fields))
                created_count += 1
                if verbose:
                    logger.info("eval_created", name=name, eval_id=eval_id)

        if dry_run:
            logger.info(
                "dry_run_summary",
                would_create=created_count,
                would_update=updated_count,
                unchanged=skipped_count,
            )
            return created_count, updated_count, skipped_count

        if to_create:
            EvalTemplate.no_workspace_objects.bulk_create(
                to_create, ignore_conflicts=True
            )
        if to_update:
            update_fields = [
                "name",
                "description",
                "criteria",
                "eval_tags",
                "config",
                "choices",
                "multi_choice",
                "owner",
                "eval_type",
                "visible_ui",
                "output_type_normalized",
                "pass_threshold",
                "choice_scores",
                "allow_edit",
                "allow_copy",
                "updated_at",
            ]
            EvalTemplate.no_workspace_objects.bulk_update(to_update, update_fields)

        # Update version cache
        try:
            cache.set("system_evals_version", SYSTEM_EVALS_VERSION, timeout=None)
        except Exception:
            pass  # Cache unavailable — seeder still works, just re-runs next time

    logger.info(
        "system_evals_seeded",
        created=created_count,
        updated=updated_count,
        unchanged=skipped_count,
        total=len(yaml_evals),
        version=SYSTEM_EVALS_VERSION,
    )

    return created_count, updated_count, skipped_count


def _eval_accepts_pdf(config):
    """True when any of the eval's params accepts a PDF input. The picker
    filters on eval_tags, so this is what fills the PDF filter chip."""
    param_modalities = (config or {}).get("param_modalities") or {}
    for modalities in param_modalities.values():
        if isinstance(modalities, (list, tuple)) and any(
            str(m).upper() == "PDF" for m in modalities
        ):
            return True
    return False


def _yaml_to_template_fields(eval_def):
    """Convert a YAML eval definition dict into EvalTemplate field values."""
    from model_hub.models.choices import OwnerChoices

    from tfc.ee_gating import is_oss

    track = eval_def.get("_track", "agent")
    # In OSS, agent evals run as LLM-as-a-Judge (CustomPromptEvaluator)
    # since AgentEvaluator requires the ee module.
    if is_oss():
        eval_type_map = {"function": "code", "agent": "llm", "specialty": "llm"}
    else:
        eval_type_map = {"function": "code", "agent": "agent", "specialty": "agent"}

    # Build config dict
    config = eval_def.get("config", {})

    # For function evals, ensure code is in config
    if track == "function" and "code" in eval_def:
        config["code"] = eval_def["code"]
        config["eval_type_id"] = "CustomCodeEval"

    # For agent evals, ensure rule_prompt is in config
    if track in ("agent", "specialty"):
        if "rule_prompt" not in config and eval_def.get("criteria"):
            config["rule_prompt"] = eval_def["criteria"]
        if is_oss():
            config["eval_type_id"] = "CustomPromptEvaluator"
        else:
            config.setdefault("eval_type_id", "AgentEvaluator")

    # Permissions
    permissions = eval_def.get("permissions", {})
    allow_edit = permissions.get("allow_edit", False)  # System evals default to no edit
    allow_copy = permissions.get("allow_copy", True)  # But copy is allowed

    eval_tags = list(eval_def.get("eval_tags", []) or [])
    if _eval_accepts_pdf(config) and "PDF" not in eval_tags:
        eval_tags.append("PDF")

    # Output type mapping
    output = config.get("output", "Pass/Fail")
    output_type_normalized = eval_def.get("output_type_normalized")
    if not output_type_normalized:
        if output == "Pass/Fail":
            output_type_normalized = "pass_fail"
        elif output in ("score", "numeric"):
            output_type_normalized = "percentage"
        elif output == "choices":
            output_type_normalized = "deterministic"
        else:
            output_type_normalized = "percentage"

    return {
        "eval_id": eval_def["eval_id"],
        "name": eval_def["name"],
        "description": eval_def.get("description", ""),
        "criteria": eval_def.get("criteria", ""),
        "eval_tags": eval_tags,
        "config": config,
        "choices": eval_def.get("choices"),
        "multi_choice": eval_def.get("multi_choice", False),
        "owner": OwnerChoices.SYSTEM.value,
        "organization": None,
        "workspace": None,
        "eval_type": eval_type_map.get(track, "agent"),
        "visible_ui": eval_def.get("visible_ui", True),
        "output_type_normalized": output_type_normalized,
        "pass_threshold": eval_def.get("pass_threshold", 0.5),
        "choice_scores": eval_def.get("choice_scores"),
        "allow_edit": allow_edit,
        "allow_copy": allow_copy,
    }


class Command(BaseCommand):
    help = "Seed system eval templates from YAML config files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to the database.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-apply all templates, ignoring version cache.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Log each eval being processed.",
        )

    def handle(self, *args, **options):
        created, updated, skipped = seed_evals(
            dry_run=options["dry_run"],
            force=options["force"],
            verbose=options["verbose"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"System evals: {created} created, {updated} updated, {skipped} unchanged."
            )
        )
