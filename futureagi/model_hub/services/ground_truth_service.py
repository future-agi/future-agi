"""Service layer for ground-truth operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from django.db import DatabaseError, transaction

from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate
from model_hub.utils.eval_input_validation import is_empty_value

if TYPE_CHECKING:
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

TenantId = str | UUID | None


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
        enabled: bool = True,
    ) -> dict[str, Any] | ServiceError:
        """Persist mappings and runtime knobs as the active GT for the tenant."""
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
            EvalGroundTruth.objects.filter(
                eval_template=gt.eval_template,
                organization=gt.organization,
                workspace=gt.workspace,
                deleted=False,
                is_active=True,
            ).exclude(id=gt.id).update(is_active=False)

            gt.variable_mapping = variable_mapping
            gt.role_mapping = role_mapping
            gt.is_active = True
            gt.enabled = bool(enabled)
            gt.max_examples = int(max_examples)
            update_fields = [
                "variable_mapping",
                "role_mapping",
                "is_active",
                "enabled",
                "max_examples",
                "updated_at",
            ]
            if (
                variable_mapping_changed
                and gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED
            ):
                gt.embedding_status = EvalGroundTruth.EmbeddingStatus.PENDING
                update_fields.append("embedding_status")
            gt.save(update_fields=update_fields)

        logger.info(
            "ground_truth_setup_updated",
            ground_truth_id=str(gt.id),
            template_id=str(eval_template.id),
            organization_id=str(gt.organization_id) if gt.organization_id else None,
            workspace_id=str(gt.workspace_id) if gt.workspace_id else None,
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
            "config": {
                "enabled": bool(gt.enabled),
                "ground_truth_id": str(gt.id),
                "max_examples": int(gt.max_examples),
                "similarity_threshold": float(gt.similarity_threshold),
            },
        }

    @staticmethod
    def load_active_gt(
        *,
        eval_template: EvalTemplate,
        organization_id: TenantId,
        workspace_id: TenantId,
    ) -> EvalGroundTruth | None:
        """Return the active, enabled GT row for ``(template, org, ws)`` or ``None``."""
        if not organization_id:
            return None
        return EvalGroundTruth.objects.filter(
            eval_template=eval_template,
            organization_id=organization_id,
            workspace_id=workspace_id,
            deleted=False,
            is_active=True,
            enabled=True,
        ).order_by("-created_at").first()

    @staticmethod
    def inject_context(
        mapped: dict,
        eval_template: EvalTemplate,
        *,
        organization_id: TenantId = None,
        workspace_id: TenantId = None,
    ) -> dict:
        """Attach retrieved GT content blocks to ``mapped['ground_truth_blocks']`` when GT is configured; fail-open on any error so the eval-run path is never blocked by GT."""
        from model_hub.utils.ground_truth_retrieval import (
            build_ground_truth_blocks,
            has_usable_inputs_for_gt,
        )

        try:
            gt = GroundTruthService.load_active_gt(
                eval_template=eval_template,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
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

            examples, column_types = GroundTruthService.retrieve_few_shot(
                gt=gt,
                inputs=mapped,
                max_results=int(gt.max_examples or 3),
            )
            if not examples:
                return mapped

            blocks = build_ground_truth_blocks(
                examples,
                variable_mapping=gt.variable_mapping,
                role_mapping=gt.role_mapping,
                column_types=column_types,
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
        except Exception as exc:
            logger.warning(
                "ground_truth_inject_failed",
                template_id=str(getattr(eval_template, "id", "") or ""),
                organization_id=str(organization_id) if organization_id else None,
                workspace_id=str(workspace_id) if workspace_id else None,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return mapped

    @staticmethod
    def resolve_preview_examples(
        *,
        eval_template: EvalTemplate,
        eval_inputs: dict[str, Any],
        organization_id: TenantId = None,
        workspace_id: TenantId = None,
    ) -> list[dict[str, Any]] | None:
        """Return retrieved GT rows for the playground; None when disabled."""
        if not isinstance(eval_inputs, dict) or not eval_inputs:
            return None

        gt = GroundTruthService.load_active_gt(
            eval_template=eval_template,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        if gt is None:
            return None

        # Retrieval reaches PG + CH. Soft-fail this call only: a transient
        # store failure should not tank the eval-run response; programmer
        # errors above (malformed config, bad ORM call) still propagate.
        try:
            rows, column_types = GroundTruthService.retrieve_few_shot(
                gt=gt,
                inputs=eval_inputs,
                max_results=int(gt.max_examples or 3),
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
        if not column_types and rows:
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
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Return ``(rows, column_types)`` for GT exemplars most similar to ``inputs``.

        ``column_types`` is ``{gt_column: modality}`` reconstructed from the
        ``input_type`` stamped onto each CH vector at embed time, so the
        rendering side does not need to re-sniff modalities at eval runtime.
        Legacy vectors written before ``input_type`` was stored fall through
        to an empty map; the caller's sniff fallback then takes over.
        """
        if gt.embedding_status != EvalGroundTruth.EmbeddingStatus.COMPLETED:
            logger.debug(
                "ground_truth_retrieve_skipped_not_ready",
                ground_truth_id=str(gt.id),
                status=gt.embedding_status,
            )
            return [], {}

        input_cols = _flatten_variable_mapping(gt.variable_mapping)
        if not input_cols:
            return [], {}

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
            return [], {}

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
        column_types: dict[str, str] = {}
        for group in raw or []:
            if not group:
                continue
            for entry in group:
                if not isinstance(entry, dict):
                    continue
                col = entry.get("column_name")
                itype = entry.get("input_type")
                if col and itype:
                    column_types[col] = itype
            row = _row_from_ch_metadata(group[0] or {}, dataset_columns, manager)
            if row:
                matches.append(row)

        logger.info(
            "ground_truth_fewshots_retrieved",
            ground_truth_id=str(gt.id),
            eval_id=str(gt.eval_template_id),
            queried_columns=cols,
            matches_returned=len(matches),
        )
        return matches, column_types


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
