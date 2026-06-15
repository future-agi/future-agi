"""Service layer for ground-truth operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate

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
        bad = _first_missing_column(variable_mapping, gt.columns or [])
        if bad is not None:
            col, key = bad
            return ServiceError(
                f"Column '{col}' (mapped to variable '{key}') not found in "
                f"dataset columns: {gt.columns}",
                code="INVALID_COLUMN",
            )

        mapping_changed = (gt.variable_mapping or {}) != (variable_mapping or {})
        update_fields = ["variable_mapping", "updated_at"]
        gt.variable_mapping = variable_mapping

        embeddings_stale = False
        if mapping_changed and gt.embedding_status == "completed":
            gt.embedding_status = "pending"
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
    def update_role_mapping(
        *,
        gt: EvalGroundTruth,
        role_mapping: dict[str, Any],
    ) -> dict[str, Any] | ServiceError:
        """Persist ``role_mapping`` without invalidating embeddings.

        Role-mapped columns (``output`` / ``explanation``) are NOT
        embedded - they're rendered verbatim as labels in the few-shot
        examples at prompt-build time. Changing the mapping just swaps
        which column supplies the label string; the per-row vectors
        produced by ``variable_mapping`` columns stay valid. Compare
        with :meth:`update_variable_mapping`, which DOES stale-flag
        embeddings because its columns drive the embedded text.

        Canonical keys are ``output`` (required at use time) and
        ``explanation`` (optional). Legacy ``expected_output`` /
        ``reasoning`` / ``reason`` keys are accepted for back-compat and
        normalized to the canonical pair at read time elsewhere.
        """
        invalid = {
            r for r in role_mapping if r not in GroundTruthService.ALLOWED_ROLE_KEYS
        }
        if invalid:
            return ServiceError(
                f"Invalid role keys: {sorted(invalid)}. "
                "Allowed keys: output, explanation.",
                code="INVALID_ROLE_KEY",
            )

        bad = _first_missing_column(role_mapping, gt.columns or [], label="role")
        if bad is not None:
            col, key = bad
            return ServiceError(
                f"Column '{col}' (mapped to role '{key}') not found in dataset "
                f"columns: {gt.columns}",
                code="INVALID_COLUMN",
            )

        gt.role_mapping = role_mapping
        gt.save(update_fields=["role_mapping", "updated_at"])
        logger.info(
            "ground_truth_role_mapping_updated",
            ground_truth_id=str(gt.id),
        )
        return {
            "id": str(gt.id),
            "role_mapping": gt.role_mapping,
            "embedding_status": gt.embedding_status,
            # Stale only if a prior variable_mapping change put the
            # dataset into a non-terminal state. Role-mapping changes
            # never set this themselves.
            "embeddings_stale": bool(
                gt.embedded_row_count > 0 and gt.embedding_status != "completed"
            ),
        }

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
        """Persist mappings + injection config atomically.

        Rules:
        * ``role_mapping["output"]`` (or legacy ``expected_output``) is
          mandatory and must point at a real GT column.
        * ``role_mapping["explanation"]`` (or legacy ``reasoning`` /
          ``reason``) is optional; when set must be a real column.
        * Every ``variable_mapping`` value must be a real GT column.
        * ``max_examples`` in [1, 20], ``similarity_threshold`` in [0, 1].
        """
        from django.db import transaction

        if not _has_value(role_mapping.get("output")) and not _has_value(
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
            bad = _first_missing_column(source_mapping, gt.columns or [], label=label)
            if bad is not None:
                col, key = bad
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
            if variable_mapping_changed and gt.embedding_status == "completed":
                gt.embedding_status = "pending"
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
    def resolve_preview_examples(
        *, eval_template: EvalTemplate, eval_inputs: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Return retrieved GT rows for the playground response.

        Tri-state contract for the FE panel: ``None`` when GT is off or
        unconfigured (panel hides); ``[]`` when enabled with zero
        matches (panel renders empty state); ``[...]`` with matches.
        Falls back to ``None`` on internal errors so the eval verdict
        is not blocked.
        """
        try:
            from model_hub.utils.ground_truth_retrieval import (
                get_ground_truth_few_shot_examples,
                load_ground_truth_config,
            )

            gt_config = load_ground_truth_config(eval_template)
            if not gt_config:
                return None
            if not isinstance(eval_inputs, dict) or not eval_inputs:
                return None

            rows = get_ground_truth_few_shot_examples(gt_config, eval_inputs) or []

            gt = (
                EvalGroundTruth.objects.filter(
                    eval_template=eval_template,
                    id=gt_config.get("ground_truth_id"),
                    deleted=False,
                )
                .only("variable_mapping", "role_mapping")
                .first()
            )
            variable_mapping = (gt.variable_mapping if gt else {}) or {}
            role_mapping = (gt.role_mapping if gt else {}) or {}
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
        """Embed every row of ``gt`` into the CH ``ground_truths`` table.

        Drives the Temporal activity, the explicit re-embed endpoint, and
        the management command roundtrip test. The PG ``EvalGroundTruth``
        row is the source of truth for status (``pending`` → ``processing``
        → ``completed`` / ``failed``); CH only stores the vectors.

        Idempotency: the writer soft-deletes any existing vectors for the
        same ``(eval_template_id, organization_id, workspace_id)`` triple
        before re-embedding, so re-runs replace cleanly rather than
        accumulate. Single GT per (template, org, workspace) is the
        product invariant (FE only surfaces one).
        """
        from agentic_eval.core.embeddings.embedding_manager import (
            EmbeddingManager,
            GROUND_TRUTH_TABLE_NAME,
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

        gt.embedding_status = "processing"
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
        _soft_delete_prior_vectors(
            manager=manager,
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
        gt.embedding_status = "completed"
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
            status="completed",
        )

    @staticmethod
    def retrieve_few_shot(
        *,
        gt: EvalGroundTruth,
        inputs: dict[str, Any],
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        """Return GT example rows most similar to ``inputs``.

        Wraps :func:`retrieve_ground_truth_fewshots` with the GT's stored
        ``variable_mapping`` (template-var → GT-column) and tenant
        identifiers. Returns the raw source-row dicts in rank order -
        callers downstream are responsible for projecting them into
        whatever surface they need (few-shot prompt text, agent-tool
        output, FE Match cards).
        """
        from agentic_eval.core.embeddings.ground_truth_fewshots import (
            GroundTruthFewShotRequest,
            retrieve_ground_truth_fewshots,
        )

        if gt.embedding_status != "completed":
            logger.debug(
                "ground_truth_retrieve_skipped_not_ready",
                ground_truth_id=str(gt.id),
                status=gt.embedding_status,
            )
            return []

        input_cols = _flatten_variable_mapping(gt.variable_mapping)
        if not input_cols:
            return []

        request = GroundTruthFewShotRequest(
            eval_id=str(gt.eval_template_id),
            inputs=inputs,
            input_cols=input_cols,
            organization_id=_organization_id_or_raise(gt),
            workspace_id=_workspace_id_or_none(gt),
            max_results=max_results,
        )
        matches = retrieve_ground_truth_fewshots(request)
        return [match.row for match in matches]

    @staticmethod
    def search(
        *,
        gt: EvalGroundTruth,
        inputs: dict[str, Any] | None,
        query: str | None,
        max_results: int,
        # Kept for API compatibility; per-column intersection already
        # gates noise. Honoured only if the underlying retrieval helper
        # is extended to surface similarity (TODO cross-feature).
        similarity_threshold: float = 0.0,  # noqa: ARG004
    ) -> dict[str, Any] | ServiceError:
        if gt.embedding_status != "completed":
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
            # Legacy single-text-box callers: project the query string
            # onto every mapped template variable so per-column search
            # still has something to compare against.
            mapped = _flatten_variable_mapping(gt.variable_mapping)
            if not mapped:
                return ServiceError(
                    "variable_mapping is empty; cannot route the legacy "
                    "`query` string to a column.",
                    code="VARIABLE_MAPPING_MISSING",
                )
            resolved_inputs = {var: stripped for var in mapped}

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

    @staticmethod
    def validate_output(
        *,
        template: EvalTemplate,
        value: Any,
    ) -> dict[str, Any]:
        from model_hub.utils.ground_truth_retrieval import validate_output_value

        ok, error = validate_output_value(
            value,
            output_type_normalized=getattr(template, "output_type_normalized", None),
            choice_scores=getattr(template, "choice_scores", None),
            pass_threshold=getattr(template, "pass_threshold", None),
        )
        return {"ok": ok, "error": error or ""}


def _has_value(value: Any) -> bool:
    """Treat blank strings, None and empty containers as unset."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


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
    """Tenant-scoped CH writes require an organization. Refuse to embed
    a row without one - the alternative is a silent cross-tenant leak."""
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
    gt.embedding_status = "failed"
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
        status="failed",
        error=reason,
    )


def _soft_delete_prior_vectors(
    *,
    manager,
    eval_id: str,
    organization_id: str,
    workspace_id: str | None,
) -> None:
    """Mark any existing CH rows for this (eval, org, workspace) as deleted.

    The CH writer doesn't dedup on its own - re-running the embed pass
    would accumulate stale vectors otherwise. Soft-delete via
    ``ALTER … UPDATE deleted=1`` keeps the historical data recoverable
    if we ever need to audit.
    """
    from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
    from agentic_eval.core.embeddings.embedding_manager import (
        GROUND_TRUTH_TABLE_NAME,
    )

    db = ClickHouseVectorDB()
    try:
        db.create_table(GROUND_TRUTH_TABLE_NAME)
        clauses = [
            f"eval_id = '{eval_id}'",
            "has(metadata.key, 'organization_id')",
            f"metadata.value[indexOf(metadata.key, 'organization_id')] = '{organization_id}'",
        ]
        if workspace_id:
            clauses.append("has(metadata.key, 'workspace_id')")
            clauses.append(
                f"metadata.value[indexOf(metadata.key, 'workspace_id')] = '{workspace_id}'"
            )
        where = " AND ".join(clauses)
        # ``ALTER ... UPDATE`` is async on MergeTree; this returns once
        # the mutation is queued. Subsequent inserts use fresh UUIDs so
        # there is no item_id collision with the soft-deleted rows; reads
        # filter ``deleted=0`` so old rows fall out as the mutation
        # catches up.
        db.client.execute(
            f"ALTER TABLE {GROUND_TRUTH_TABLE_NAME} UPDATE deleted = 1 WHERE {where}"
        )
        logger.info(
            "ground_truth_prior_vectors_soft_deleted",
            eval_id=eval_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
    finally:
        db.close()
