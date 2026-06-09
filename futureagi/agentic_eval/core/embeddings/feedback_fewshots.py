"""Shared feedback few-shot primitives (TH-5462).

Both the dataset eval path (``EvaluationRunner``) and the observe eval path
(``tracer/utils/eval.py`` span/trace/session handlers) integrate user feedback
the same way: feedback is embedded into the ``feedbacks`` vector store, then
retrieved at eval time as few-shot examples that get appended to the
evaluator's prompt.

The two paths differ only in *input preparation* (where the row_dict comes
from, and how eval field names map to the metadata keys feedback is indexed
by). The actual embed (write) and RAG retrieval (read) are identical, so they
live here and both paths call them.
"""

import structlog

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

logger = structlog.get_logger(__name__)

# Metadata keys the feedback content is stored under (consumed by
# ``process_examples`` when building the few-shot text).
FEEDBACK_COMMENT_COL = "feedback_comment"
FEEDBACK_VALUE_COL = "feedback_value"


def retrieve_feedback_fewshots(
    eval_id, inputs, input_cols, organization_id, workspace_id=None
):
    """Retrieve prior feedback for ``eval_id`` as few-shot examples.

    ``inputs``/``input_cols`` are positionally aligned: ``inputs`` are the
    values being evaluated (used for vector similarity), ``input_cols`` are the
    metadata keys feedback was indexed under (column UUIDs for dataset evals,
    field names for observe evals — the caller resolves this).

    Returns a list of few-shot example blocks (possibly empty). Best-effort:
    never raises into the eval path.
    """
    em = EmbeddingManager()
    examples_out = []
    try:
        if not organization_id:
            logger.warning(
                "retrieve_feedback_fewshots_skipped",
                reason="no organization_id",
                eval_id=str(eval_id),
            )
            return examples_out
        raw = em.retrieve_avg_rag_based_examples(
            eval_id=eval_id,
            inputs=inputs,
            input_cols=input_cols,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        processed = em.process_examples(
            raw,
            inputs=input_cols,
            feedback_col_name=FEEDBACK_COMMENT_COL,
            corrected_label_col_name=FEEDBACK_VALUE_COL,
        )
        if processed:
            examples_out.extend(processed)
    except Exception as e:
        logger.warning(
            "retrieve_feedback_fewshots_failed", eval_id=str(eval_id), error=str(e)
        )
    finally:
        try:
            em.close()
        except Exception:
            pass
    return examples_out
