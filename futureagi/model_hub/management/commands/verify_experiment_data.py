"""
Pre-migration verification: checks ALL existing experiments for data integrity.

Run BEFORE applying migrations:
    python manage.py verify_experiment_data

Exit code 0 if no errors, 1 if errors found.
"""

import sys

from django.core.management.base import BaseCommand
from django.db.models import Count, Prefetch, Q
from tqdm import tqdm

from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentsTable,
)


class Command(BaseCommand):
    help = "Verify integrity of all existing experiments before migration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Attempt to fix minor issues (e.g., orphaned EDT records).",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        errors = []
        warnings = []

        edt_prefetch_qs = ExperimentDatasetTable.all_objects.filter(
            deleted=False
        ).annotate(active_col_count=Count("columns", filter=Q(columns__deleted=False)))

        experiments = (
            ExperimentsTable.all_objects.filter(deleted=False)
            .select_related("dataset")
            .prefetch_related(
                "user_eval_template_ids",
                Prefetch(
                    "experiment_datasets",
                    queryset=edt_prefetch_qs,
                    to_attr="_active_edts",
                ),
            )
        )
        total = experiments.count()
        self.stdout.write(f"Checking {total} experiments...")

        valid_eval_ids = set(UserEvalMetric.all_objects.values_list("id", flat=True))

        for exp in tqdm(
            experiments.iterator(chunk_size=500),
            total=total,
            desc="Verifying",
            unit="exp",
        ):
            exp_errors, exp_warnings = self._check_experiment(exp, valid_eval_ids)
            errors.extend(exp_errors)
            warnings.extend(exp_warnings)

        # Check orphaned ExperimentDatasetTable records
        orphaned = self._check_orphaned_edts(fix)
        warnings.extend(orphaned)

        self.stdout.write("")
        if warnings:
            self.stdout.write(self.style.WARNING(f"Warnings: {len(warnings)}"))
            for w in warnings:
                self.stdout.write(f"  WARN: {w}")

        if errors:
            self.stdout.write(self.style.ERROR(f"Errors: {len(errors)}"))
            for e in errors:
                self.stdout.write(f"  ERROR: {e}")
            sys.exit(1)
        else:
            self.stdout.write(
                self.style.SUCCESS(f"All {total} experiments passed verification.")
            )
            sys.exit(0)

    def _check_experiment(self, exp, valid_eval_ids):
        errors = []
        warnings = []
        exp_id = str(exp.id)

        # 1. Check prompt_config is a valid list
        pc = exp.prompt_config
        if not isinstance(pc, list):
            errors.append(
                f"Experiment {exp_id}: prompt_config is not a list (got {type(pc).__name__})"
            )
            return errors, warnings

        if not pc:
            warnings.append(f"Experiment {exp_id}: prompt_config is empty")
            return errors, warnings

        # 2. Check each entry has required keys
        for i, entry in enumerate(pc):
            if not isinstance(entry, dict):
                errors.append(f"Experiment {exp_id}: prompt_config[{i}] is not a dict")
                continue

            if "messages" not in entry:
                errors.append(
                    f"Experiment {exp_id}: prompt_config[{i}] missing 'messages'"
                )
            elif not isinstance(entry["messages"], list):
                errors.append(
                    f"Experiment {exp_id}: prompt_config[{i}].messages is not a list"
                )

            if "model" not in entry:
                errors.append(
                    f"Experiment {exp_id}: prompt_config[{i}] missing 'model'"
                )
            elif not isinstance(entry["model"], list):
                errors.append(
                    f"Experiment {exp_id}: prompt_config[{i}].model is not a list"
                )
            elif not entry["model"]:
                errors.append(
                    f"Experiment {exp_id}: prompt_config[{i}].model is empty list"
                )

            if "name" not in entry:
                warnings.append(
                    f"Experiment {exp_id}: prompt_config[{i}] missing 'name'"
                )

        # 3. Check referenced dataset exists
        try:
            dataset = exp.dataset
            if not dataset:
                errors.append(f"Experiment {exp_id}: dataset is null")
            elif dataset.deleted:
                warnings.append(
                    f"Experiment {exp_id}: dataset {dataset.id} is soft-deleted"
                )
        except Dataset.DoesNotExist:
            errors.append(
                f"Experiment {exp_id}: dataset FK references non-existent record"
            )

        # 4. Check user_eval_template_ids reference existing records
        eval_ids = [m.id for m in exp.user_eval_template_ids.all()]
        if eval_ids:
            missing = set(eval_ids) - valid_eval_ids
            if missing:
                warnings.append(
                    f"Experiment {exp_id}: user_eval_template_ids references "
                    f"{len(missing)} non-existent UserEvalMetric records"
                )

        # 5. Check ExperimentDatasetTable records have valid columns
        # Use both FK and M2M to be thorough
        for edt in exp._active_edts:
            if edt.active_col_count == 0:
                warnings.append(
                    f"Experiment {exp_id}: ExperimentDatasetTable {edt.id} ('{edt.name}') has no columns"
                )

        return errors, warnings

    def _check_orphaned_edts(self, fix=False):
        warnings = []

        # EDT records with no experiment FK and no M2M link
        orphaned_edts = ExperimentDatasetTable.all_objects.filter(
            experiment__isnull=True,
            experiments_datasets_created__isnull=True,
            deleted=False,
        )
        count = orphaned_edts.count()
        if count > 0:
            warnings.append(
                f"Found {count} orphaned ExperimentDatasetTable records (no experiment link)"
            )
            if fix:
                orphaned_edts.update(deleted=True)
                warnings.append(f"  -> Soft-deleted {count} orphaned EDT records")

        return warnings
