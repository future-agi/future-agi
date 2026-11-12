"""
Migration: convert inline prompt_config JSON to ExperimentPromptConfig records
and create snapshot datasets for V2 compatibility.

For each existing experiment:
1. Determine experiment_type from model_type in prompt_config entries.
2. Set experiment_type on the experiment record.
3. Create a snapshot dataset (full copy of source dataset).
4. Remap column UUIDs in messages from original -> snapshot columns.
5. For each entry in prompt_config[], expand models into individual EPC records:
   - Each old entry has model: ["gpt-5", "claude-3"] -> one EPC per model.
   - LLM: create PromptTemplate + PromptVersion, then EPC with FKs.
   - TTS/STT/Image: create EPC with inline messages (no PromptTemplate).
6. Link each EPC to the correct ExperimentDatasetTable (matched by column_name pattern).
7. Create output columns in the snapshot dataset for each EDT.
8. Remap eval metrics to point to snapshot dataset with translated column mappings.
9. Build canonical column ordering on the snapshot dataset.

Run AFTER Django migrations and migrate_m2m_to_fk:
    python manage.py migrate_experiment_prompts [--dry-run]

Idempotent: skips experiments that already have snapshot_dataset or EPC records.
"""

import copy
import logging
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction
from tqdm import tqdm

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.views.experiments import (
    _build_and_save_v2_column_order,
    _translate_eval_mapping,
)
from model_hub.views.utils.utils import update_column_id

logger = logging.getLogger(__name__)


def _detect_experiment_type(prompt_config):
    """Detect experiment type from model_type field in prompt_config entries."""
    for entry in prompt_config:
        model_type = entry.get("model_type", "llm")
        if model_type in ("tts", "stt", "image"):
            return model_type
    return "llm"


def _extract_model_info(model_spec):
    """Extract model name, display name, and run_prompt_config from model spec."""
    if isinstance(model_spec, str):
        return model_spec, None, None
    elif isinstance(model_spec, dict):
        return (
            model_spec.get("name", ""),
            model_spec.get("display_name"),
            model_spec.get("config"),
        )
    return str(model_spec), None, None


def _build_column_name(experiment_name, config_name, model_name, voice=None):
    """Build the column name pattern used to find matching EDT records."""
    display_suffix = model_name
    if voice:
        try:
            voice_str = str(voice).strip()
            if voice_str and voice_str.lower() not in str(display_suffix).lower():
                display_suffix = f"{display_suffix}-{voice_str}"
        except Exception:
            pass
    return f"{experiment_name}-{config_name}-{display_suffix}"


class Command(BaseCommand):
    help = "Convert inline prompt_config JSON to ExperimentPromptConfig records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be done without making changes.",
        )
        parser.add_argument(
            "--experiment-id",
            type=str,
            help="Migrate a specific experiment by ID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        experiment_id = options.get("experiment_id")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        queryset = ExperimentsTable.all_objects.filter(deleted=False).prefetch_related(
            "user_eval_template_ids"
        )
        if experiment_id:
            queryset = queryset.filter(id=experiment_id)

        total = queryset.count()
        self.stdout.write(f"Processing {total} experiments...")

        migrated = 0
        skipped = 0
        errors = 0

        for exp in tqdm(
            queryset.iterator(chunk_size=100),
            total=total,
            desc="Migrating experiments",
            unit="exp",
        ):
            try:
                result = self._migrate_experiment(exp, dry_run)
                if result == "migrated":
                    migrated += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(f"Error migrating experiment {exp.id}: {e}")
                )
                logger.exception(f"Error migrating experiment {exp.id}")

        self.stdout.write("")
        self.stdout.write(
            f"Results: {migrated} migrated, {skipped} skipped, {errors} errors"
        )
        if errors:
            self.stdout.write(
                self.style.ERROR(f"{errors} experiments failed to migrate.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Migration completed successfully."))

    def _migrate_experiment(self, exp, dry_run):
        """Migrate a single experiment. Returns 'migrated', 'skipped', or raises."""
        exp_id = str(exp.id)

        # Check if already migrated (has snapshot dataset)
        if exp.snapshot_dataset_id:
            self.stdout.write(
                f"  Experiment {exp_id}: already has snapshot_dataset, skipping"
            )
            return "skipped"

        # Check if already migrated (has EPC records)
        existing_epcs = ExperimentPromptConfig.all_objects.filter(
            experiment_dataset__experiment=exp, deleted=False
        ).count()
        if existing_epcs > 0:
            self.stdout.write(
                f"  Experiment {exp_id}: already has {existing_epcs} EPCs, skipping"
            )
            return "skipped"

        prompt_config = exp.prompt_config
        if not isinstance(prompt_config, list) or not prompt_config:
            self.stdout.write(
                f"  Experiment {exp_id}: empty/invalid prompt_config, skipping"
            )
            return "skipped"

        # Detect experiment type from model_type in prompt_config entries
        experiment_type = _detect_experiment_type(prompt_config)

        # Validate source dataset exists
        try:
            dataset = Dataset.objects.get(id=exp.dataset_id, deleted=False)
        except Dataset.DoesNotExist:
            self.stdout.write(
                f"  Experiment {exp_id}: source dataset deleted/missing, skipping"
            )
            return "skipped"

        if dry_run:
            total_epcs = sum(len(entry.get("model", [])) for entry in prompt_config)
            has_evals = bool(exp.user_eval_template_ids.all())
            self.stdout.write(
                f"  Experiment {exp_id}: type={experiment_type}, "
                f"{len(prompt_config)} configs -> {total_epcs} EPCs, "
                f"snapshot=will_create, evals={'yes' if has_evals else 'no'}"
            )
            return "migrated"

        with transaction.atomic():
            # Set experiment_type
            exp.experiment_type = experiment_type
            exp.save(update_fields=["experiment_type"])

            # Create dataset snapshot (copies ALL columns from source).
            # use_raw_sql_cells=True pushes the per-cell copy to Postgres
            # via INSERT ... SELECT, avoiding a Python round-trip per cell.
            snapshot_dataset, column_mapping, row_mapping = create_dataset_snapshot(
                dataset, exp, use_raw_sql_cells=True
            )

            # The source dataset may have experiment/eval columns from OTHER
            # experiments that also used it. Remove those from the snapshot —
            # only base data columns should remain. Experiment-specific sources
            # (experiment output, eval scores, eval reasons) get cleaned up;
            # base data sources (OTHERS, run_prompt) are preserved.
            # This experiment's own output/eval columns will be re-added by
            # _migrate_edt_columns below.
            exp_column_sources = [
                "experiment",
                "experiment_evaluation",
                "evaluation",
                "evaluation_reason",
            ]
            other_exp_cols = Column.objects.filter(
                dataset=snapshot_dataset,
                deleted=False,
                source__in=exp_column_sources,
            )

            # Delete cells for those columns first, then soft-delete the columns
            Cell.objects.filter(column__in=other_exp_cols, deleted=False).update(
                deleted=True
            )
            other_exp_cols.update(deleted=True)

            # Remove deleted columns from column_mapping
            column_mapping = {
                old_id: new_col
                for old_id, new_col in column_mapping.items()
                if new_col.source not in exp_column_sources
            }

            # Precompute snapshot column lookup by source_id (str of original
            # column ID). Used in _migrate_edt_columns to avoid a per-column
            # DB round-trip when checking if create_dataset_snapshot already
            # copied each EDT column.
            snapshot_cols_by_source = {
                c.source_id: c
                for c in Column.objects.filter(dataset=snapshot_dataset, deleted=False)
                if c.source_id
            }

            # Build string-based column mapping for message remapping
            # UUID -> snapshot UUID (for EPC messages used at runtime)
            col_id_str_mapping = {
                str(old_id): str(new_col.id)
                for old_id, new_col in column_mapping.items()
            }
            # UUID -> column name (for PromptVersion snapshot displayed in UI)
            col_id_to_name_mapping = {
                str(old_id): new_col.name for old_id, new_col in column_mapping.items()
            }

            # Remap experiment.column to snapshot column
            if exp.column_id and exp.column_id in column_mapping:
                exp.column = column_mapping[exp.column_id]
                exp.save(update_fields=["column"])

            order = 0
            for config_entry in prompt_config:
                self._migrate_config_entry(
                    exp,
                    config_entry,
                    experiment_type,
                    order,
                    snapshot_dataset,
                    column_mapping,
                    col_id_str_mapping,
                    col_id_to_name_mapping,
                    row_mapping,
                    snapshot_cols_by_source,
                )
                order += len(config_entry.get("model", []))

            # Remap existing eval metrics
            self._remap_eval_metrics(exp, snapshot_dataset, column_mapping)

            # Build canonical column ordering
            _build_and_save_v2_column_order(exp, snapshot_dataset)

        self.stdout.write(f"  Experiment {exp_id}: migrated (type={experiment_type})")
        return "migrated"

    def _migrate_config_entry(
        self,
        exp,
        config_entry,
        experiment_type,
        start_order,
        snapshot_dataset,
        column_mapping,
        col_id_str_mapping,
        col_id_to_name_mapping,
        row_mapping,
        snapshot_cols_by_source,
    ):
        """Migrate a single prompt_config entry (may produce multiple EPCs for multiple models)."""
        config_name = config_entry.get("name", "unnamed")
        messages = config_entry.get("messages", [])
        models = config_entry.get("model", [])
        configuration = config_entry.get("configuration", {})
        output_format = config_entry.get(
            "outputFormat", config_entry.get("output_format", "string")
        )
        voice = config_entry.get("voice")
        voice_input_column_id = config_entry.get(
            "voice_input_column"
        ) or config_entry.get("voiceInputColumn")

        # Remap column UUIDs in messages from original -> snapshot column UUIDs
        # (for EPC messages used at runtime by populate_placeholders)
        # Deep copy needed because update_column_id mutates inner content dicts
        remapped_messages = messages
        if messages and col_id_str_mapping:
            remapped_messages = [
                update_column_id(copy.deepcopy(msg), col_id_str_mapping)
                for msg in messages
            ]

        # Replace column UUIDs with column names for PromptVersion snapshot
        # (displayed in the prompt template UI as {{column_name}})
        named_messages = messages
        if messages and col_id_to_name_mapping:
            named_messages = [
                update_column_id(copy.deepcopy(msg), col_id_to_name_mapping)
                for msg in messages
            ]

        for i, model_spec in enumerate(models):
            model_name, display_name, run_prompt_config = _extract_model_info(
                model_spec
            )
            model_params = config_entry.get("model_params", {}).get(model_name, {})

            # Extract per-model voice from model config (TTS models have
            # different voices like alloy/echo/fable in config.voice)
            per_model_voice = voice or (run_prompt_config or {}).get("voice")

            # For LLM experiments, create PromptTemplate + PromptVersion per model
            # so each version has the correct model info in its snapshot
            prompt_template = None
            prompt_version = None
            if experiment_type == "llm" and named_messages:
                prompt_template, prompt_version = self._get_or_create_prompt_template(
                    exp,
                    config_name,
                    named_messages,
                    configuration,
                    output_format,
                    model_name,
                    display_name,
                    model_params,
                )

            # Build the column name including voice for uniqueness
            column_name = _build_column_name(
                exp.name, config_name, model_name, per_model_voice
            )

            # Find existing EDT by name (from V1 creation)
            edt = ExperimentDatasetTable.all_objects.filter(
                experiment=exp,
                name=column_name,
                deleted=False,
            ).first()

            if not edt:
                # Also try finding via M2M (for records not yet migrated to FK)
                edt = ExperimentDatasetTable.all_objects.filter(
                    experiments_datasets_created=exp,
                    name=column_name,
                    deleted=False,
                ).first()

            if not edt:
                # Create a new EDT
                edt = ExperimentDatasetTable.all_objects.create(
                    id=uuid.uuid4(),
                    name=column_name,
                    experiment=exp,
                    legacy_prompt_config=config_entry,
                    status=exp.status,
                )

            # Ensure FK is set
            if not edt.experiment_id:
                edt.experiment = exp
                edt.save(update_fields=["experiment_id"])

            # Resolve voice_input_column FK for STT — remap to snapshot column
            voice_input_column = None
            if voice_input_column_id:
                try:
                    original_uuid = uuid.UUID(str(voice_input_column_id))
                    voice_input_column = column_mapping.get(original_uuid)
                except (ValueError, AttributeError):
                    pass
                if not voice_input_column:
                    voice_input_column = Column.all_objects.filter(
                        id=voice_input_column_id, deleted=False
                    ).first()

            # Create ExperimentPromptConfig (idempotent)
            epc, created = ExperimentPromptConfig.all_objects.get_or_create(
                experiment_dataset=edt,
                defaults={
                    "id": uuid.uuid4(),
                    "name": (
                        f"{config_name}-{model_name}"
                        if len(models) > 1
                        else config_name
                    ),
                    "prompt_template": (
                        prompt_template if experiment_type == "llm" else None
                    ),
                    "prompt_version": (
                        prompt_version if experiment_type == "llm" else None
                    ),
                    "model": model_name,
                    "model_display_name": display_name,
                    "model_config": run_prompt_config or {},
                    "model_params": model_params,
                    "configuration": configuration,
                    "output_format": output_format,
                    "order": start_order + i,
                    "messages": remapped_messages if experiment_type != "llm" else None,
                    "voice_input_column": voice_input_column,
                },
            )

            if created:
                logger.info(
                    f"Created EPC {epc.id} for experiment {exp.id}, model {model_name}"
                )

            # Migrate existing EDT columns (output + eval) from original
            # dataset to snapshot dataset, copying their cells
            self._migrate_edt_columns(
                edt,
                snapshot_dataset,
                row_mapping,
                snapshot_cols_by_source,
            )

    def _remap_eval_metrics(self, exp, snapshot_dataset, column_mapping):
        """Remap existing eval metric mappings from original to snapshot column UUIDs."""
        for uem in exp.user_eval_template_ids.all():
            config = uem.config or {}
            update_fields = []

            if config.get("mapping") and column_mapping:
                config = {
                    **config,
                    "mapping": _translate_eval_mapping(
                        config["mapping"], column_mapping
                    ),
                }
                uem.config = config
                update_fields.append("config")

            # Point eval metric to snapshot dataset
            if uem.dataset_id != snapshot_dataset.id:
                uem.dataset = snapshot_dataset
                update_fields.append("dataset_id")

            if update_fields:
                uem.save(update_fields=update_fields)

    def _migrate_edt_columns(
        self,
        edt,
        snapshot_dataset,
        row_mapping,
        snapshot_cols_by_source,
        batch_size=5000,
    ):
        """Copy EDT columns from original dataset to snapshot dataset.

        For each column linked to this EDT that lives on the original dataset:
        1. Check if create_dataset_snapshot already copied it (via source_id)
        2. If yes, reuse the existing snapshot column
        3. If no, create a new column and copy cells
        4. Add snapshot column to EDT (keep originals for revert safety)
        """
        original_columns = list(
            edt.columns.filter(deleted=False).exclude(dataset=snapshot_dataset)
        )

        if not original_columns:
            return

        # Accumulate snapshot columns to link to this EDT; insert the M2M
        # rows in a single bulk_create at the end to avoid one round-trip
        # per column.
        columns_to_link = []

        for orig_col in original_columns:
            # Check if create_dataset_snapshot already copied this column
            # (it copies all source dataset columns and sets source_id to
            # the original column's ID). snapshot_cols_by_source is a
            # precomputed dict built once per experiment in _migrate_experiment
            # to avoid a per-column SELECT round-trip.
            existing_snapshot_col = snapshot_cols_by_source.get(str(orig_col.id))

            if existing_snapshot_col:
                # Reuse the column that create_dataset_snapshot already made
                columns_to_link.append(existing_snapshot_col)
                logger.info(
                    f"Reused snapshot column {existing_snapshot_col.id} "
                    f"(from {orig_col.id}) for EDT {edt.id}"
                )
                continue

            # Column wasn't copied by create_dataset_snapshot (e.g. it was
            # added after the source dataset was snapshotted). Create it now.
            new_col = Column.objects.create(
                id=uuid.uuid4(),
                name=orig_col.name,
                data_type=orig_col.data_type,
                source=orig_col.source,
                source_id=orig_col.source_id,
                metadata=orig_col.metadata,
                status=orig_col.status,
                dataset=snapshot_dataset,
            )

            # Copy cells from original column to snapshot, remapping rows
            cell_batch = []
            for cell in Cell.objects.filter(column=orig_col, deleted=False).iterator(
                chunk_size=batch_size
            ):
                new_row = row_mapping.get(cell.row_id)
                if not new_row:
                    continue

                cell_batch.append(
                    Cell(
                        id=uuid.uuid4(),
                        dataset=snapshot_dataset,
                        column=new_col,
                        row=new_row,
                        value=cell.value,
                        value_infos=cell.value_infos,
                        feedback_info=cell.feedback_info,
                        status=cell.status,
                        column_metadata=cell.column_metadata,
                        prompt_tokens=cell.prompt_tokens,
                        completion_tokens=cell.completion_tokens,
                        response_time=cell.response_time,
                    )
                )

                if len(cell_batch) >= batch_size:
                    Cell.objects.bulk_create(cell_batch, batch_size=batch_size)
                    cell_batch = []

            if cell_batch:
                Cell.objects.bulk_create(cell_batch, batch_size=batch_size)

            columns_to_link.append(new_col)

        if columns_to_link:
            Through = ExperimentDatasetTable.columns.through
            Through.objects.bulk_create(
                [
                    Through(experimentdatasettable_id=edt.id, column_id=col.id)
                    for col in columns_to_link
                ],
                ignore_conflicts=True,
            )

            logger.info(
                f"Migrated EDT column {orig_col.id} -> {new_col.id} for EDT {edt.id}"
            )

    def _get_or_create_prompt_template(
        self,
        exp,
        config_name,
        messages,
        configuration=None,
        output_format="string",
        model_name="",
        display_name=None,
        model_params=None,
    ):
        """Create or find PromptTemplate + PromptVersion for an LLM experiment config."""
        template_name = f"[Migrated] {exp.name} - {config_name} - {model_name}"

        # Get org/workspace from the experiment's dataset
        org = getattr(exp.dataset, "organization", None)
        workspace = (
            getattr(exp.dataset, "workspace", None)
            if hasattr(exp.dataset, "workspace")
            else None
        )

        # Try to find existing template first
        existing = PromptTemplate.all_objects.filter(
            name=template_name,
            organization=org,
            deleted=False,
        ).first()

        if existing:
            version = PromptVersion.all_objects.filter(
                original_template=existing,
                deleted=False,
            ).first()
            if version:
                return existing, version

        # Create new template
        template = PromptTemplate.all_objects.create(
            id=uuid.uuid4(),
            name=template_name,
            organization=org,
            workspace=workspace,
        )

        # Build prompt_config_snapshot as a dict with messages, placeholders,
        # and configuration — matching the format used by the prompt template UI
        config = configuration or {}
        params = model_params or {}
        snapshot = {
            "messages": messages,
            "placeholders": [],
            "configuration": {
                "model": model_name,
                "tools": config.get("tools", []),
                "modelType": "all",
                "toolChoice": config.get("tool_choice", ""),
                "modelDetail": {
                    "logoUrl": params.get("logo_url", ""),
                    "providers": params.get("providers", ""),
                    "modelName": display_name or model_name,
                    "isAvailable": True,
                },
                "outputFormat": output_format,
            },
        }

        # Create version with full snapshot dict
        version = PromptVersion.all_objects.create(
            id=uuid.uuid4(),
            original_template=template,
            prompt_config_snapshot=snapshot,
            template_version="v1",
            is_default=True,
        )

        return template, version
