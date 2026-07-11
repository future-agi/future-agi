"""Seed ids for Packet B detail-GET tools (verify_bridges.py SEED_IDS).

Ids harvested from the ws1 dev DB (org of kartik.nvj@futureagi.com) on
2026-06-10. Values are either a bare id (passed as the tool's pk_field) or a
full params dict for tools that also need path_kwargs / required query
params.

NOTE (harness gap, for the integrator): verify_bridges resolves ids in the
order id_source > sibling-list > SEED_IDS, so for tools that need MORE than
the bare pk (path_kwargs like get_prompt_sdk_code's `language`, or required
query params like get_prompt_evaluations' `versions`), the bare id resolved
from id_source wins and the call fails validation before the dict seed below
is consulted. Until verify_bridges prefers a dict SEED for such tools, expect
ERR rather than NODATA for those four entries — the dicts below are correct
and can be replayed manually via registry.get(name).run(params, ctx).
"""

_TEMPLATE_ID = "0a76ccea-5224-4c34-b34c-5aaf9753395b"  # 'llm_prompt_node_1'
_PROMPT_VERSION_ID = "b71f61ef-59d4-42bf-89e8-d36c7d43aa0e"
_EXPERIMENT_ID = "649afe76-7e86-4eec-8c72-195398c46132"  # Completed, V2
_EVAL_METRIC_ID = "037e6af0-fdf9-4181-bdd0-1532aa76cbbd"
_DATASET_ID = "789319c3-5208-44e0-aa4b-9823db84cd84"

SEED_IDS = {
    # PromptHistoryExecutionViewSet — no sibling list tool, no id_source.
    "get_prompt_execution_details": _PROMPT_VERSION_ID,
    # pk + path_kwargs / required query params (see harness note above).
    "get_prompt_sdk_code": {
        "template_id": _TEMPLATE_ID,
        "language": "python",
    },
    "get_prompt_evaluations": {
        "template_id": _TEMPLATE_ID,
        "versions": '["v1"]',
    },
    "get_experiment_evaluation_stats": {
        "experiment_id": _EXPERIMENT_ID,
        "evaluation_id": _EVAL_METRIC_ID,
    },
    "get_experiment_feedback_template": {
        "experiment_id": _EXPERIMENT_ID,
        "user_eval_metric_id": _EVAL_METRIC_ID,
    },
    # Plain detail-GETs (also reachable via id_source; kept as fallback).
    "suggest_experiment_name": _DATASET_ID,
    "export_experiments_csv": _EXPERIMENT_ID,
}
