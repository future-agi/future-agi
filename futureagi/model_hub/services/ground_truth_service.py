"""Service layer for ground-truth operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from django.db import DatabaseError

from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate
from model_hub.utils.eval_input_validation import is_empty_value

if TYPE_CHECKING:
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager


logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


@dataclass(frozen=True)
class EmbedDatasetResult:
    """Outcome of a full-dataset embed pass."""

    ground_truth_id: str
    rows_embedded: int
    status: str
    error: str | None = None


class GroundTruthService:
    """Ground-truth business logic; views and Temporal activities call here."""

    ALLOWED_ROLE_KEYS = frozenset(
        {"output", "explanation", "expected_output", "reasoning", "reason"}
    )

    @staticmethod
    def create_from_upload(
        *,
        eval_template: EvalTemplate,
        name: str,
        description: str,
        file_name: str,
        columns: list[str],
        data: list[dict[str, Any]],
        variable_mapping: dict[str, Any] | None,
        role_mapping: dict[str, Any] | None,
        organization: Organization,
        workspace: Workspace | None,
    ) -> EvalGroundTruth:
        rows = [row for row in (data or []) if isinstance(row, dict)]
        return EvalGroundTruth.objects.create(
            eval_template=eval_template,
            name=name,
            description=description,
            file_name=file_name,
            columns=columns,
            data=rows,
            row_count=len(rows),
            variable_mapping=variable_mapping,
            role_mapping=role_mapping,
            embedding_status=EvalGroundTruth.EmbeddingStatus.PENDING,
            organization=organization,
            workspace=workspace,
        )

    @staticmethod
    def update_setup(
        *,
        gt: EvalGroundTruth,
        eval_template: EvalTemplate,
        variable_mapping: dict[str, Any],
        role_mapping: dict[str, Any],
        max_examples: int,
        injection_format: str = "structured",
        enabled: bool = True,
    ) -> dict[str, Any] | ServiceError:
        """Persist mappings + injection config atomically."""
        from django.db import transaction

        if is_empty_value(role_mapping.get("output")) and is_empty_value(
            role_mapping.get("expected_output")
        ):
            return ServiceError(
                "Expected output column is required. Pick a ground truth "
                "column that carries the labelled eval verdict.",
                code="EXPECTED_OUTPUT_REQUIRED",
            )

        if enabled and not (variable_mapping or {}):
            return ServiceError(
                "Map at least one eval variable to a ground truth column "
                "before enabling. Without a mapping the retriever has "
                "nothing to embed against.",
                code="VARIABLE_MAPPING_REQUIRED",
            )

        if not (1 <= int(max_examples) <= 20):
            return ServiceError(
                "max_examples must be between 1 and 20.",
                code="INVALID_MAX_EXAMPLES",
            )

        invalid_roles = {
            r for r in role_mapping if r not in GroundTruthService.ALLOWED_ROLE_KEYS
        }
        if invalid_roles:
            return ServiceError(
                f"Invalid role keys: {sorted(invalid_roles)}. "
                "Allowed keys: output, explanation.",
                code="INVALID_ROLE_KEY",
            )
        for source_mapping, label in (
            (variable_mapping, "variable"),
            (role_mapping, "role"),
        ):
            missing = _first_missing_column(source_mapping, gt.columns or [], label=label)
            if missing is not None:
                col, key = missing
                return ServiceError(
                    f"Column '{col}' (mapped to {label} '{key}') not found "
                    f"in dataset columns: {gt.columns}",
                    code="INVALID_COLUMN",
                )

        variable_mapping_changed = (gt.variable_mapping or {}) != (
            variable_mapping or {}
        )
        has_prior_vectors = (gt.embedded_row_count or 0) > 0
        embeddings_stale = variable_mapping_changed and has_prior_vectors

        with transaction.atomic():
            gt.variable_mapping = variable_mapping
            gt.role_mapping = role_mapping
            gt_update_fields = ["variable_mapping", "role_mapping", "updated_at"]
            if (
                variable_mapping_changed
                and gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED
            ):
                gt.embedding_status = EvalGroundTruth.EmbeddingStatus.PENDING
                gt_update_fields.append("embedding_status")
            gt.save(update_fields=gt_update_fields)

            template_config = dict(eval_template.config or {})
            template_config["ground_truth"] = {
                "enabled": bool(enabled),
                "ground_truth_id": str(gt.id),
                "max_examples": int(max_examples),
                "injection_format": injection_format,
            }
            eval_template.config = template_config
            eval_template.save(update_fields=["config", "updated_at"])

        logger.info(
            "ground_truth_setup_updated",
            ground_truth_id=str(gt.id),
            template_id=str(eval_template.id),
            embeddings_stale=embeddings_stale,
            variable_mapping_changed=variable_mapping_changed,
        )
        return {
            "id": str(gt.id),
            "template_id": str(eval_template.id),
            "variable_mapping": gt.variable_mapping,
            "role_mapping": gt.role_mapping,
            "embedding_status": gt.embedding_status,
            "embeddings_stale": embeddings_stale,
            "config": template_config["ground_truth"],
        }

    @staticmethod
    def load_config(eval_template: EvalTemplate) -> dict | None:
        """Return the GT config dict from the template, or ``None``.

        Treats ``enabled=False`` and missing ``ground_truth_id`` as
        "not configured"; callers should fall through silently.
        """
        config = (getattr(eval_template, "config", None) or {}).get("ground_truth")
        if not config or not config.get("enabled"):
            return None
        if not config.get("ground_truth_id"):
            return None
        return config

    @staticmethod
    def inject_context(
        mapped: dict,
        eval_template: EvalTemplate,
    ) -> dict:
        """Attach retrieved GT content blocks to ``mapped['ground_truth_blocks']`` when GT is configured; evaluators read the kwarg and concatenate into their content array without adding their own framing."""
        from model_hub.utils.ground_truth_retrieval import (
            build_ground_truth_blocks,
            has_usable_inputs_for_gt,
        )

        gt_config = GroundTruthService.load_config(eval_template)
        if not gt_config:
            return mapped

        try:
            gt = EvalGroundTruth.objects.filter(
                id=gt_config["ground_truth_id"], deleted=False
            ).first()
        except (DatabaseError, ValueError):
            gt = None
        if gt is None:
            return mapped

        if gt.embedding_status != EvalGroundTruth.EmbeddingStatus.COMPLETED:
            return mapped

        if not has_usable_inputs_for_gt(gt.variable_mapping, mapped):
            logger.debug(
                "ground_truth_skipped_no_usable_inputs",
                gt_id=str(gt.id),
                template_id=str(getattr(eval_template, "id", "") or ""),
            )
            return mapped

        examples = GroundTruthService.retrieve_few_shot(
            gt=gt,
            inputs=mapped,
            max_results=int(gt_config.get("max_examples", 3)),
        )
        if not examples:
            return mapped

        blocks = build_ground_truth_blocks(
            examples,
            variable_mapping=gt.variable_mapping,
            role_mapping=gt.role_mapping,
        )
        if blocks:
            mapped["ground_truth_blocks"] = blocks
            logger.debug(
                "ground_truth_blocks_injected",
                gt_id=str(gt.id),
                examples_count=len(examples),
                block_count=len(blocks),
            )
        return mapped

    @staticmethod
    def resolve_preview_examples(
        *, eval_template: EvalTemplate, eval_inputs: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Return retrieved GT rows for the playground; None when disabled."""
        gt_config = GroundTruthService.load_config(eval_template)
        if not gt_config:
            return None
        if not isinstance(eval_inputs, dict) or not eval_inputs:
            return None

        gt = (
            EvalGroundTruth.objects.filter(
                eval_template=eval_template,
                id=gt_config.get("ground_truth_id"),
                deleted=False,
            )
            .only("variable_mapping", "role_mapping", "embedding_status")
            .first()
        )
        if gt is None:
            return []

        # Retrieval reaches PG + CH. Soft-fail this call only: a transient
        # store failure should not tank the eval-run response; programmer
        # errors above (malformed config, bad ORM call) still propagate.
        try:
            rows = (
                GroundTruthService.retrieve_few_shot(
                    gt=gt,
                    inputs=eval_inputs,
                    max_results=int(gt_config.get("max_examples", 3)),
                )
                or []
            )
        except (DatabaseError, ConnectionError) as exc:
            logger.warning(
                "preview_ground_truth_examples_lookup_failed",
                template_id=str(getattr(eval_template, "id", "") or ""),
                error=str(exc),
            )
            return None

        variable_mapping = gt.variable_mapping or {}
        role_mapping = gt.role_mapping or {}
        from model_hub.utils.ground_truth_retrieval import (
            detect_input_column_types,
        )

        column_types = detect_input_column_types(rows, variable_mapping)
        return [
            {
                "row": row,
                "variable_mapping": variable_mapping,
                "role_mapping": role_mapping,
                "column_types": column_types,
            }
            for row in rows
        ]

    @staticmethod
    def embed_dataset(
        *,
        gt: EvalGroundTruth,
        heartbeat: Callable[[int], None] | None = None,
    ) -> EmbedDatasetResult:
        """Embed every row of ``gt`` into the CH ``ground_truths`` table."""
        from agentic_eval.core.embeddings.embedding_manager import (
            GROUND_TRUTH_TABLE_NAME,
            EmbeddingManager,
        )

        data = gt.data or []
        if not data:
            return _mark_failed(gt, "Ground truth has no rows to embed.")

        mapped_columns = _mapped_column_order(gt.variable_mapping)
        if not mapped_columns:
            return _mark_failed(
                gt,
                "variable_mapping is empty - at least one mapped column is "
                "required before embedding.",
            )

        organization_id = _organization_id_or_raise(gt)
        workspace_id = _workspace_id_or_none(gt)
        eval_id = str(gt.eval_template_id)
        mapping_at_start = dict(gt.variable_mapping or {})

        gt.embedding_status = EvalGroundTruth.EmbeddingStatus.PROCESSING
        gt.embedded_row_count = 0
        gt.save(update_fields=["embedding_status", "embedded_row_count", "updated_at"])

        logger.info(
            "ground_truth_embed_start",
            ground_truth_id=str(gt.id),
            eval_id=eval_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            rows=len(data),
            mapped_columns=mapped_columns,
        )

        manager = EmbeddingManager()
        manager.soft_delete_vectors(
            table_name=GROUND_TRUTH_TABLE_NAME,
            eval_id=eval_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )

        gt_id_for_callback = gt.id
        rows_done_max = [0]

        def _persist_progress(rows_done: int) -> None:
            rows_done_max[0] = max(rows_done_max[0], rows_done)
            if heartbeat is not None:
                try:
                    heartbeat(rows_done)
                except RuntimeError:
                    # heartbeat() raises RuntimeError when called outside an
                    # activity context (eg. backfill / management command).
                    # Cancellation is async and is not caught here.
                    pass
            try:
                EvalGroundTruth.objects.filter(id=gt_id_for_callback).update(
                    embedded_row_count=rows_done,
                )
            except Exception as save_exc:
                logger.warning(
                    "ground_truth_progress_persist_failed",
                    ground_truth_id=str(gt_id_for_callback),
                    rows_done=rows_done,
                    error=str(save_exc),
                )

        try:
            manager.parallel_process_metadata(
                eval_id=eval_id,
                metadatas=list(data),
                inputs_formater=mapped_columns,
                table_name=GROUND_TRUTH_TABLE_NAME,
                organization_id=organization_id,
                workspace_id=workspace_id,
                progress_callback=_persist_progress,
            )
        except Exception as exc:
            logger.exception(
                "ground_truth_embed_failed",
                ground_truth_id=str(gt.id),
                error=str(exc),
            )
            return _mark_failed(gt, f"Embedding failed: {exc}")

        rows_embedded = rows_done_max[0]
        rows_expected = len(data)
        if rows_embedded < rows_expected:
            logger.error(
                "ground_truth_embed_partial",
                ground_truth_id=str(gt.id),
                rows_embedded=rows_embedded,
                rows_expected=rows_expected,
            )
            return _mark_failed(
                gt,
                f"Embedding failed: only {rows_embedded} of {rows_expected} rows were written. "
                "Check the embedding-serving service availability.",
            )

        gt.refresh_from_db(fields=["variable_mapping"])
        mapping_changed_during_embed = (
            dict(gt.variable_mapping or {}) != mapping_at_start
        )
        terminal_status = (
            EvalGroundTruth.EmbeddingStatus.PENDING
            if mapping_changed_during_embed
            else EvalGroundTruth.EmbeddingStatus.COMPLETED
        )
        gt.embedded_row_count = rows_embedded
        gt.embedding_status = terminal_status
        gt.save(
            update_fields=[
                "embedding_status",
                "embedded_row_count",
                "updated_at",
            ]
        )
        logger.info(
            "ground_truth_embed_done",
            ground_truth_id=str(gt.id),
            rows_embedded=rows_embedded,
            terminal_status=terminal_status,
            mapping_changed_during_embed=mapping_changed_during_embed,
        )
        return EmbedDatasetResult(
            ground_truth_id=str(gt.id),
            rows_embedded=rows_embedded,
            status=terminal_status,
        )

    @staticmethod
    def retrieve_few_shot(
        *,
        gt: EvalGroundTruth,
        inputs: dict[str, Any],
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        """Return GT example rows most similar to ``inputs``."""
        if gt.embedding_status != EvalGroundTruth.EmbeddingStatus.COMPLETED:
            logger.debug(
                "ground_truth_retrieve_skipped_not_ready",
                ground_truth_id=str(gt.id),
                status=gt.embedding_status,
            )
            return []

        input_cols = _flatten_variable_mapping(gt.variable_mapping)
        if not input_cols:
            return []

        # Pair each runtime input with its GT column. Skip empty values and
        # any variable that isn't mapped; both would poison the CH query.
        values: list[Any] = []
        cols: list[str] = []
        for var, value in inputs.items():
            if is_empty_value(value):
                continue
            column = input_cols.get(var)
            if not column:
                continue
            values.append(value)
            cols.append(column)

        if not values:
            return []

        from agentic_eval.core.embeddings.embedding_manager import (
            GROUND_TRUTH_TABLE_NAME,
            EmbeddingManager,
        )

        manager = EmbeddingManager()
        raw = manager.retrieve_avg_rag_based_examples(
            eval_id=str(gt.eval_template_id),
            inputs=values,
            input_cols=cols,
            table_name=GROUND_TRUTH_TABLE_NAME,
            organization_id=_organization_id_or_raise(gt),
            workspace_id=_workspace_id_or_none(gt),
            top_k=max_results,
        )

        dataset_columns = set(gt.columns or [])
        matches: list[dict[str, Any]] = []
        for group in raw or []:
            if not group:
                continue
            row = _row_from_ch_metadata(group[0], dataset_columns, manager)
            if row:
                matches.append(row)

        logger.info(
            "ground_truth_fewshots_retrieved",
            ground_truth_id=str(gt.id),
            eval_id=str(gt.eval_template_id),
            queried_columns=cols,
            matches_returned=len(matches),
        )
        return matches


def _first_missing_column(
    mapping: dict[str, Any],
    available_columns: list[str],
    label: str = "variable",
) -> tuple[str, str] | None:
    """Return the first (column, key) pair whose column isn't in the dataset.

    ``label`` is only used to disambiguate variable vs role in the
    error path; the (column, key) shape is identical either way.
    """
    available = set(available_columns)
    for key, col in (mapping or {}).items():
        for c in col if isinstance(col, list) else [col]:
            if c not in available:
                return c, key
    return None


def _mapped_column_order(variable_mapping: dict[str, Any] | None) -> list[str]:
    """Flatten ``variable_mapping`` values into the GT column order used at
    write time. List-of-columns mappings (multimodal) expand into multiple
    entries while preserving order and dropping duplicates."""
    if not variable_mapping:
        return []
    seen: set[str] = set()
    columns: list[str] = []
    for value in variable_mapping.values():
        candidates = value if isinstance(value, list) else [value]
        for col in candidates:
            if col and col not in seen:
                seen.add(col)
                columns.append(col)
    return columns


def _flatten_variable_mapping(
    variable_mapping: dict[str, Any] | None,
) -> dict[str, str]:
    """Pick a single GT column per template variable for the retrieval side.

    Reads expect ``{template_var: gt_column}`` (one column per variable);
    if a variable maps to a list, we take the first. This is a
    documented simplification - the writer ingests every column in the
    list, but the reader's per-column search is one query per template
    variable.
    """
    if not variable_mapping:
        return {}
    flat: dict[str, str] = {}
    for var, value in variable_mapping.items():
        if isinstance(value, list):
            first = next((c for c in value if c), None)
            if first:
                flat[var] = first
        elif value:
            flat[var] = value
    return flat


def _organization_id_or_raise(gt: EvalGroundTruth) -> str:
    """Refuse to embed a row without an organization; silent cross-tenant leak otherwise."""
    organization_id = getattr(gt, "organization_id", None) or (
        gt.organization.id if getattr(gt, "organization", None) else None
    )
    if not organization_id:
        raise ValueError(
            f"EvalGroundTruth {gt.id} has no organization; refusing to embed."
        )
    return str(organization_id)


def _workspace_id_or_none(gt: EvalGroundTruth) -> str | None:
    workspace_id = getattr(gt, "workspace_id", None) or (
        gt.workspace.id if getattr(gt, "workspace", None) else None
    )
    return str(workspace_id) if workspace_id else None


_INTERNAL_CH_KEYS = frozenset(
    {
        "item_id",
        "input_type",
        "column_name",
        "index_column",
        "organization_id",
        "workspace_id",
    }
)


def _row_from_ch_metadata(
    meta: dict[str, Any],
    dataset_columns: set[str],
    manager: EmbeddingManager,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if key in _INTERNAL_CH_KEYS:
            continue
        if dataset_columns and key not in dataset_columns:
            continue
        if isinstance(value, str) and value:
            try:
                decoded = manager.decode_path(value)
            except (ValueError, UnicodeDecodeError):
                decoded = value
            if decoded.startswith(("http://", "https://", "s3://")):
                row[key] = decoded
                continue
        row[key] = value
    return row


def _mark_failed(gt: EvalGroundTruth, reason: str) -> EmbedDatasetResult:
    """Persist a failed embed pass on the PG row and return the typed result."""
    gt.embedding_status = EvalGroundTruth.EmbeddingStatus.FAILED
    gt.embedded_row_count = 0
    gt.save(update_fields=["embedding_status", "embedded_row_count", "updated_at"])
    logger.warning(
        "ground_truth_embed_marked_failed",
        ground_truth_id=str(gt.id),
        reason=reason,
    )
    return EmbedDatasetResult(
        ground_truth_id=str(gt.id),
        rows_embedded=0,
        status=EvalGroundTruth.EmbeddingStatus.FAILED,
        error=reason,
    )


