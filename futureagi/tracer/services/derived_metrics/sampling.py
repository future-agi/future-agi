"""Version-one adapter for the frozen sampling cohort's persisted wire contract.

The producer lives in eval_tasks/sampling (TH-8689/TH-8690). Importing that sibling
is deliberately unnecessary: unweighted metrics work on an independent install;
weighted metrics require evidence in persisted rows, never a feature flag.
"""

METADATA_PATH = "'_task_selection', 'sampling'"


def sampling_field(name: str) -> str:
    if name not in {
        "cohort_id",
        "policy_version",
        "sampling_unit",
        "sampling_unit_id",
        "stratum",
    }:
        raise ValueError("Unknown sampling metadata field")
    return f"JSONExtractString(output_metadata, {METADATA_PATH}, '{name}')"


def probability_sql() -> str:
    return f"toFloat64OrNull(JSONExtractRaw(output_metadata, {METADATA_PATH}, 'inclusion_probability'))"


def sampling_scope_sql() -> str:
    cohort, policy = sampling_field("cohort_id"), sampling_field("policy_version")
    return f"if(notEmpty({cohort}) AND notEmpty({policy}), concat({cohort}, ':', {policy}), '')"
