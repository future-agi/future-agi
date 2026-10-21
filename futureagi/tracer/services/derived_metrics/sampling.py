"""Version-one adapter for persisted sampling lineage, without a sibling import."""

METADATA_PATH = "'_task_selection', 'sampling'"


def sampling_field(name: str) -> str:
    if name not in {
        "cohort_id",
        "policy_version",
        "sampling_unit",
        "sampling_unit_id",
        "stratum",
        "membership_mode",
        "cohort_frozen_at",
    }:
        raise ValueError("Unknown sampling metadata field")
    return f"JSONExtractString(output_metadata, {METADATA_PATH}, '{name}')"


def probability_sql() -> str:
    return f"toFloat64OrNull(JSONExtractRaw(output_metadata, {METADATA_PATH}, 'inclusion_probability'))"


def _window_time(name: str) -> str:
    return f"parseDateTime64BestEffortOrNull(JSONExtractString(output_metadata, {METADATA_PATH}, 'window', '{name}'), 6, 'UTC')"


def sampling_lineage_valid_sql() -> str:
    start, end = _window_time("start"), _window_time("end")
    mode = sampling_field("membership_mode")
    frozen = f"parseDateTime64BestEffortOrNull({sampling_field('cohort_frozen_at')}, 6, 'UTC')"
    population = f"toInt64OrNull(JSONExtractRaw(output_metadata, {METADATA_PATH}, 'population_size'))"
    open_end = (
        f"JSONExtractRaw(output_metadata, {METADATA_PATH}, 'window', 'end') = 'null'"
    )
    finite_window = f"isNotNull({end}) AND {start} < {end} AND {end} >= toDateTime64(%(end)s, 6, 'UTC')"
    return f"""ifNull(isNotNull({start}) AND {start} <= toDateTime64(%(start)s, 6, 'UTC')
        AND (({mode} = 'live' AND empty({sampling_field("cohort_frozen_at")})
                AND ({open_end} OR ({finite_window})))
            OR ({mode} = 'closed' AND ({finite_window}) AND isNotNull({frozen}) AND {frozen} >= {end}
                AND {frozen} <= toDateTime64(%(as_of)s, 6, 'UTC') AND {population} > 0)), 0)"""


def sampling_scope_sql() -> str:
    cohort, policy = sampling_field("cohort_id"), sampling_field("policy_version")
    frozen = f"parseDateTime64BestEffortOrNull({sampling_field('cohort_frozen_at')}, 6, 'UTC')"
    # Normalize equivalent ISO offsets before hashing. Cohort/policy alone do
    # not excuse contradictory window, membership-mode or freeze provenance.
    lineage = f"toJSONString(tuple({sampling_field('membership_mode')}, ifNull(toString({frozen}), ''), ifNull(toString({_window_time('start')}), ''), ifNull(toString({_window_time('end')}), '')))"
    return f"if(notEmpty({cohort}) AND notEmpty({policy}), concat({cohort}, ':', {policy}, ':', hex(SHA256({lineage}))), '')"
