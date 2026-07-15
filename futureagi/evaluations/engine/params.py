"""
Run params preparation for non-dataset callers.

Handles: few-shot injection, criteria, eval_name, required_keys,
param_modalities, config_params_desc — the common parts of
EvaluationRunner.map_fields() used by standalone, tracer, simulate, playground.

Does NOT handle dataset cell resolution — that stays in EvaluationRunner.

Extracted from EvaluationRunner.map_fields() (eval_runner.py:981).
"""

import structlog

from evaluations.constants import FUTUREAGI_EVAL_TYPES

logger = structlog.get_logger(__name__)


def prepare_run_params(
    inputs,
    eval_template,
    is_futureagi=False,
    criteria=None,
    organization_id=None,
    workspace_id=None,
):
    """
    Prepare the final run_params dict for eval_instance.run().

    Takes pre-resolved inputs (key→value mapping) and injects the extra
    params that evaluators expect: few_shots, criteria, eval_name,
    required_keys, param_modalities, config_params_desc.

    Args:
        inputs: dict of pre-resolved key→value pairs (from caller)
        eval_template: EvalTemplate instance
        is_futureagi: whether this is a FutureAGI eval type
        criteria: criteria override (from version resolution or config)
        organization_id: for few-shot RAG retrieval
        workspace_id: for few-shot RAG retrieval

    Returns:
        dict: The complete run_params ready for eval_instance.run()
    """
    # Work on a copy to avoid mutating caller's data
    run_params = dict(inputs)

    eval_type_id = eval_template.config.get("eval_type_id", "")

    # FutureAGI evals: inject few-shots, criteria, eval_name, required_keys
    if is_futureagi:
        # Few-shot retrieval via RAG
        few_shots = _get_few_shot_examples(
            eval_template=eval_template,
            inputs=run_params,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        run_params["few_shots"] = few_shots

        # Criteria
        if "criteria" not in run_params:
            run_params["criteria"] = criteria if criteria else eval_template.criteria

        # Eval name
        run_params["eval_name"] = eval_template.name

        # Required keys
        run_params["required_keys"] = eval_template.config.get("required_keys", [])

        # Param modalities for validation
        if "param_modalities" in eval_template.config:
            run_params["param_modalities"] = eval_template.config["param_modalities"]

        # Parameter descriptions for deterministic evaluator
        if "config_params_desc" in eval_template.config:
            run_params["config_params_desc"] = eval_template.config[
                "config_params_desc"
            ]

    # CustomPromptEvaluator and AgentEvaluator: inject required_keys
    if eval_type_id in ("CustomPromptEvaluator", "AgentEvaluator"):
        if "required_keys" not in run_params:
            run_params["required_keys"] = eval_template.config.get("required_keys", [])

    return run_params


def _get_few_shot_examples(
    eval_template,
    inputs,
    organization_id=None,
    workspace_id=None,
):
    """
    Retrieve few-shot examples via RAG embedding similarity.

    Returns a list of processed few-shot examples for the evaluator.
    Returns empty list if retrieval fails or no org context.
    """
    if not organization_id:
        # print(f"[FEEDBACK RETRIEVAL] Skipped — no organization_id for eval_template={eval_template.id}", flush=True)
        return []

    try:
        from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

        embedding_manager = EmbeddingManager()

        try:
            input_keys = list(inputs.keys())
            input_values = list(inputs.values())

            # print(f"[FEEDBACK RETRIEVAL] Fetching few-shots for eval_template={eval_template.id} org={organization_id} input_keys={input_keys}", flush=True)
            examples = embedding_manager.retrieve_avg_rag_based_examples(
                eval_id=eval_template.id,
                inputs=input_values,
                input_cols=input_keys,
                organization_id=organization_id,
                # Retrieval is org-scoped for now; the write path still
                # tags rows with workspace_id so we can flip to workspace
                # -aware later without a backfill.
                workspace_id=None,
            )
            # print(f"[FEEDBACK RETRIEVAL] RAG returned {len(examples)} examples for eval_template={eval_template.id}", flush=True)

            few_shots = embedding_manager.process_examples(
                examples,
                inputs=input_keys,
                feedback_col_name="feedback_comment",
                corrected_label_col_name="feedback_value",
            )

            shot_count = (
                len(few_shots)
                if isinstance(few_shots, list)
                else (1 if few_shots else 0)
            )
            # print(f"[FEEDBACK RETRIEVAL] Processed {shot_count} few-shot content blocks for eval_template={eval_template.id}", flush=True)
            return few_shots
        finally:
            embedding_manager.close()

    except Exception as e:
        # print(f"[FEEDBACK RETRIEVAL] Failed for eval_template={eval_template.id}: {e}", flush=True)
        logger.info(f"few_shot_retrieval_failed: {e}")
        return []
