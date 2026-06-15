"""Service layer for ground-truth operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from agentic_eval.core_evals.fi_evals.eval_type import LlmEvalTypeId
from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate
from model_hub.utils.eval_input_validation import is_empty_value


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

    @staticmethod
    def update_variable_mapping(
        *,
        gt: EvalGroundTruth,
        variable_mapping: dict[str, Any],
    ) -> dict[str, Any] | ServiceError:
        """Persist ``variable_mapping`` and stale-flag embeddings if changed."""
        missing = _first_missing_column(variable_mapping, gt.columns or [])
        if missing is not None:
            col, key = missing
            return ServiceError(
                f"Column '{col}' (mapped to variable '{key}') not found in "
                f"dataset columns: {gt.columns}",
                code="INVALID_COLUMN",
            )

        mapping_changed = (gt.variable_mapping or {}) != (variable_mapping or {})
        update_fields = ["variable_mapping", "updated_at"]
        gt.variable_mapping = variable_mapping

        embeddings_stale = False
        if mapping_changed and gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED:
            gt.embedding_status = EvalGroundTruth.EmbeddingStatus.PENDING
            update_fields.append("embedding_status")
            embeddings_stale = True

        gt.save(update_fields=update_fields)
        logger.info(
            "ground_truth_variable_mapping_updated",
            ground_truth_id=str(gt.id),
            embeddings_stale=embeddings_stale,
        )
        return {
            "id": str(gt.id),
            "variable_mapping": gt.variable_mapping,
            "embedding_status": gt.embedding_status,
            "embeddings_stale": embeddings_stale,
        }

    ALLOWED_ROLE_KEYS = frozenset(
        {"output", "explanation", "expected_output", "reasoning", "reason"}
    )

    @staticmethod
    def update_setup(
        *,
        gt: EvalGroundTruth,
        eval_template: EvalTemplate,
        variable_mapping: dict[str, Any],
        role_mapping: dict[str, Any],
        max_examples: int,
        similarity_threshold: float,
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

        if not (1 <= int(max_examples) <= 20):
            return ServiceError(
                "max_examples must be between 1 and 20.",
                code="INVALID_MAX_EXAMPLES",
            )
        if not (0.0 <= float(similarity_threshold) <= 1.0):
            return ServiceError(
                "similarity_threshold must be between 0 and 1.",
                code="INVALID_SIMILARITY_THRESHOLD",
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
        embeddings_stale = False

        with transaction.atomic():
            gt.variable_mapping = variable_mapping
            gt.role_mapping = role_mapping
            gt_update_fields = ["variable_mapping", "role_mapping", "updated_at"]
            if variable_mapping_changed and gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED:
                gt.embedding_status = EvalGroundTruth.EmbeddingStatus.PENDING
                gt_update_fields.append("embedding_status")
                embeddings_stale = True
            gt.save(update_fields=gt_update_fields)

            template_config = dict(eval_template.config or {})
            template_config["ground_truth"] = {
                "enabled": bool(enabled),
                "ground_truth_id": str(gt.id),
                "max_examples": int(max_examples),
                "similarity_threshold": float(similarity_threshold),
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
        eval_type_id: str = "",
    ) -> dict:
        """Mutate ``mapped`` with GT context when the template has GT enabled.

        ``CustomPromptEvaluator`` path: inject ``ground_truth_few_shot``
        (formatted text). Other evaluator paths (agent tool): inject
        ``ground_truth_config`` so the agent can expose the search tool.
        """
        from model_hub.utils.ground_truth_retrieval import (
            format_few_shot_examples,
            get_label_columns,
            has_usable_inputs_for_gt,
        )

        gt_config = GroundTruthService.load_config(eval_template)
        if not gt_config:
            return mapped

        try:
            gt = EvalGroundTruth.objects.filter(
                id=gt_config["ground_truth_id"], deleted=False
            ).first()
        except Exception:
            gt = None
        if gt is None:
            return mapped

        if not has_usable_inputs_for_gt(gt.variable_mapping, mapped):
            logger.debug(
                "ground_truth_skipped_no_usable_inputs",
                gt_id=str(gt.id),
                eval_type_id=eval_type_id,
                template_id=str(getattr(eval_template, "id", "") or ""),
            )
            return mapped

        gt_config = dict(gt_config)
        gt_config["embedding_status"] = gt.embedding_status

        if (
            eval_type_id == LlmEvalTypeId.CUSTOM_PROMPT_EVAL.value
            and gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED
        ):
            examples = GroundTruthService.retrieve_few_shot(
                gt=gt,
                inputs=mapped,
                max_results=int(gt_config.get("max_examples", 3)),
            )
            if examples:
                output_col, explanation_col = get_label_columns(gt.role_mapping)
                mapped["ground_truth_few_shot"] = format_few_shot_examples(
                    examples,
                    variable_mapping=gt.variable_mapping,
                    output_column=output_col,
                    explanation_column=explanation_col,
                    injection_format=gt_config.get("injection_format", "structured"),
                )
            logger.debug(
                "ground_truth_custom_prompt_injected",
                gt_id=str(gt.id),
                examples_count=len(examples),
            )
            return mapped

        mapped["ground_truth_config"] = gt_config
        logger.debug(
            "ground_truth_agent_config_injected",
            gt_id=str(gt.id),
            eval_type_id=eval_type_id,
        )
        return mapped

    @staticmethod
    def resolve_preview_examples(
        *, eval_template: EvalTemplate, eval_inputs: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Return retrieved GT rows for the playground; None when disabled."""
        try:
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

            rows = (
                GroundTruthService.retrieve_few_shot(
                    gt=gt,
                    inputs=eval_inputs,
                    max_results=int(gt_config.get("max_examples", 3)),
                )
                or []
            )

            variable_mapping = gt.variable_mapping or {}
            role_mapping = gt.role_mapping or {}
            return [
                {
                    "row": row,
                    "variable_mapping": variable_mapping,
                    "role_mapping": role_mapping,
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning(
                "preview_ground_truth_examples_lookup_failed",
                template_id=str(getattr(eval_template, "id", "") or ""),
                error=str(exc),
            )
            return None

    @staticmethod
    def embed_dataset(*, gt: EvalGroundTruth) -> EmbedDatasetResult:
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

        try:
            manager.parallel_process_metadata(
                eval_id=eval_id,
                metadatas=list(data),
                inputs_formater=mapped_columns,
                table_name=GROUND_TRUTH_TABLE_NAME,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            logger.exception(
                "ground_truth_embed_failed",
                ground_truth_id=str(gt.id),
                error=str(exc),
            )
            return _mark_failed(gt, f"Embedding failed: {exc}")

        rows_embedded = len(data)
        gt.embedded_row_count = rows_embedded
        gt.embedding_status = EvalGroundTruth.EmbeddingStatus.COMPLETED
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
        )
        return EmbedDatasetResult(
            ground_truth_id=str(gt.id),
            rows_embedded=rows_embedded,
            status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
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

        raw = EmbeddingManager().retrieve_avg_rag_based_examples(
            eval_id=str(gt.eval_template_id),
            inputs=values,
            input_cols=cols,
            table_name=GROUND_TRUTH_TABLE_NAME,
            organization_id=_organization_id_or_raise(gt),
            workspace_id=_workspace_id_or_none(gt),
            top_k=max_results,
        )

        # Each item-group from CH has one row per mapped input column of the
        # same source row; collapse back to one source-row dict.
        matches: list[dict[str, Any]] = []
        for group in raw or []:
            if not group:
                continue
            canonical = dict(group[0])
            for storage_key in ("item_id", "index_column", "input_type"):
                canonical.pop(storage_key, None)
            matches.append(canonical)

        logger.info(
            "ground_truth_fewshots_retrieved",
            ground_truth_id=str(gt.id),
            eval_id=str(gt.eval_template_id),
            queried_columns=cols,
            matches_returned=len(matches),
        )
        return matches

    @staticmethod
    def search(
        *,
        gt: EvalGroundTruth,
        inputs: dict[str, Any] | None,
        query: str | None,
        max_results: int,
        similarity_threshold: float = 0.0,  # noqa: ARG004
    ) -> dict[str, Any] | ServiceError:
        if gt.embedding_status != EvalGroundTruth.EmbeddingStatus.COMPLETED:
            return ServiceError(
                f"Embeddings not ready. Status: {gt.embedding_status}. "
                "Wait for embedding generation to complete.",
                code="EMBEDDINGS_NOT_READY",
            )

        resolved_inputs: dict[str, Any]
        if isinstance(inputs, dict) and inputs:
            resolved_inputs = inputs
        else:
            stripped = (query or "").strip()
            if not stripped:
                return ServiceError(
                    "Provide either a non-empty `query` string or an `inputs` "
                    "dict matching the rule prompt's template variables.",
                    code="EMPTY_INPUT",
                )
            # Project the legacy single-text query onto every mapped variable.
            mapped = _flatten_variable_mapping(gt.variable_mapping)
            if not mapped:
                return ServiceError(
                    "variable_mapping is empty; cannot route the legacy "
                    "`query` string to a column.",
                    code="VARIABLE_MAPPING_MISSING",
                )
            resolved_inputs = dict.fromkeys(mapped, stripped)

        results = GroundTruthService.retrieve_few_shot(
            gt=gt,
            inputs=resolved_inputs,
            max_results=max_results,
        )

        return {
            "query": (query or "").strip(),
            "inputs": resolved_inputs,
            "results": results,
            "total": len(results),
        }

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


