"""Vetted example queries shown in the catalog panel and returned by the catalog API.

Each example is ordinary user SQL: it goes through the same compiler, scope
rewrite and locked runner as any typed query, so an example can never read
more than its author could. Every example is compiled by ``test_compiler`` and
executed on seeded ClickHouse by ``test_analytics_clickhouse``, which checks
its columns and values against results computed in Python.
"""

from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True)
class Example:
    id: str
    title: str
    description: str
    sql: str


EXAMPLES = (
    Example(
        "cost-tokens-by-model",
        "Cost and tokens by model",
        "Spans, total tokens and total cost per model.",
        """SELECT model,
  count() AS span_count,
  sum(total_tokens) AS tokens,
  round(sum(cost), 6) AS total_cost
FROM spans
GROUP BY model
ORDER BY total_cost DESC, model""",
    ),
    Example(
        "latency-percentiles-by-day",
        "Latency p50 / p95 / p99 by day",
        "Span latency percentiles per UTC day (linear interpolation).",
        """SELECT toDate(start_time) AS day,
  count() AS span_count,
  quantile(0.5)(latency_ms) AS p50_ms,
  quantile(0.95)(latency_ms) AS p95_ms,
  quantile(0.99)(latency_ms) AS p99_ms
FROM spans
GROUP BY day
ORDER BY day""",
    ),
    Example(
        "error-rate-by-day",
        "Error rate by day",
        "Share of spans with status ERROR per UTC day.",
        """SELECT toDate(start_time) AS day,
  count() AS span_count,
  countIf(status = 'ERROR') AS errors,
  round(countIf(status = 'ERROR') / count(), 4) AS error_rate
FROM spans
GROUP BY day
ORDER BY day""",
    ),
    Example(
        "slowest-traces",
        "Slowest traces",
        "Traces by wall-clock duration, from the first span start to the last span end.",
        """SELECT trace_id,
  count() AS span_count,
  dateDiff('millisecond', min(start_time), max(end_time)) AS duration_ms,
  round(sum(cost), 6) AS total_cost
FROM spans
GROUP BY trace_id
ORDER BY duration_ms DESC, trace_id
LIMIT 20""",
    ),
    Example(
        "sessions-per-user",
        "Sessions per user",
        "Distinct sessions and spans per end user; the external user id appears "
        "when the user was first seen in the selected window.",
        """SELECT s.user_id AS user_id,
  u.external_id AS external_user_id,
  count(DISTINCT s.session_id) AS session_count,
  count() AS span_count
FROM spans AS s
LEFT JOIN users AS u ON s.user_id = u.id
WHERE s.user_id IS NOT NULL
GROUP BY s.user_id, u.external_id
ORDER BY session_count DESC, span_count DESC, s.user_id
LIMIT 100""",
    ),
    Example(
        "eval-score-by-evaluator-and-locale",
        "Eval score by evaluator and locale",
        "Results, average score and pass rate per evaluation configuration and "
        "session locale.",
        """SELECT e.config_id AS config_id,
  s.locale AS locale,
  count() AS results,
  round(avg(e.score), 4) AS avg_score,
  round(avg(e.passed), 4) AS pass_rate
FROM eval_results AS e
INNER JOIN sessions AS s ON e.session_id = s.id
GROUP BY e.config_id, s.locale
ORDER BY e.config_id, s.locale""",
    ),
    Example(
        "annotation-agreement",
        "Human annotation vs. evaluator agreement",
        "One comparison per span for each human-label/evaluator pair, using the "
        "latest human annotation and non-null span pass result in the window "
        "(created_at, then id). Shares are within each label/evaluator pair.",
        """WITH latest_annotations AS (
  SELECT label_id, trace_id, span_id,
    argMax(value, (created_at, id)) AS human_label
  FROM annotations
  WHERE score_source = 'human' AND span_id IS NOT NULL
  GROUP BY label_id, trace_id, span_id
), latest_evaluations AS (
  SELECT config_id, trace_id, span_id,
    argMax(passed, (created_at, id)) AS eval_passed
  FROM eval_results
  WHERE target_type = 'span' AND passed IS NOT NULL
  GROUP BY config_id, trace_id, span_id
)
SELECT a.label_id AS label_id,
  e.config_id AS config_id,
  a.human_label AS human_label,
  e.eval_passed AS eval_passed,
  count() AS spans_compared,
  round(count() / sum(count()) OVER (PARTITION BY a.label_id, e.config_id), 4) AS share
FROM latest_annotations AS a
INNER JOIN latest_evaluations AS e ON a.span_id = e.span_id AND a.trace_id = e.trace_id
GROUP BY a.label_id, e.config_id, a.human_label, e.eval_passed
ORDER BY label_id, config_id, human_label, eval_passed""",
    ),
    Example(
        "derived-metric-over-time",
        "Derived metric over time",
        "Latest committed value of each derived metric per window. Needs the "
        "derived_metrics dataset.",
        """SELECT metric_id,
  window_start,
  argMax(value, as_of) AS latest_value,
  count() AS attempts
FROM derived_metrics
WHERE status = 'ok'
GROUP BY metric_id, window_start
ORDER BY metric_id, window_start""",
    ),
    Example(
        "lowest-scoring-sessions",
        "Lowest-scoring sessions",
        "Session-level eval results joined to their sessions, lowest average "
        "score first.",
        """SELECT s.external_id AS session_external_id,
  s.locale AS locale,
  count() AS results,
  round(avg(e.score), 4) AS avg_score
FROM eval_results AS e
INNER JOIN sessions AS s ON e.session_id = s.id
WHERE e.target_type = 'session'
GROUP BY s.external_id, s.locale
ORDER BY avg_score, session_external_id
LIMIT 50""",
    ),
    Example(
        "costly-users-ranked",
        "Costliest users, ranked",
        "Window functions over per-user spend: dense rank and share of the "
        "project's total cost.",
        """WITH spend AS (
  SELECT user_id, sum(cost) AS user_cost, count() AS span_count
  FROM spans
  WHERE user_id IS NOT NULL
  GROUP BY user_id
)
SELECT user_id,
  round(user_cost, 6) AS total_cost,
  span_count,
  dense_rank() OVER (ORDER BY user_cost DESC) AS cost_rank,
  round(user_cost / sum(user_cost) OVER (), 4) AS cost_share
FROM spend
ORDER BY cost_rank, user_id
LIMIT 50""",
    ),
    Example(
        "sampled-vs-all-by-model",
        "Sampled vs. all spans by model",
        "All spans against distinct evaluated spans with known inclusion probabilities. "
        "The weighted count uses each span's latest known probability; interpret "
        "it only within one sampling design, not as an unbiased pooled estimate "
        "across runs. Unknown probabilities are excluded.",
        """WITH sampled AS (
  SELECT span_id, toString(trace_id) AS target_trace_id,
    argMax(inclusion_probability, (created_at, id)) AS inclusion_probability
  FROM eval_results
  WHERE target_type = 'span' AND inclusion_probability IS NOT NULL
  GROUP BY span_id, target_trace_id
)
SELECT s.model AS model,
  count() AS all_spans,
  count(x.span_id) AS sampled_spans,
  round(sum(if(x.span_id IS NULL, 0, 1 / x.inclusion_probability)), 4) AS estimated_spans
FROM spans AS s
LEFT JOIN sampled AS x ON s.id = x.span_id AND s.trace_id = x.target_trace_id
GROUP BY s.model
ORDER BY s.model""",
    ),
    Example(
        "retention-cohorts",
        "Retention-style user cohorts",
        "Users grouped by the UTC day they were first seen and how many days "
        "later they had spans.",
        """SELECT toDate(u.first_seen) AS cohort_day,
  dateDiff('day', toDate(u.first_seen), toDate(s.start_time)) AS days_later,
  count(DISTINCT u.id) AS active_users
FROM users AS u
INNER JOIN spans AS s ON s.user_id = u.id
GROUP BY cohort_day, days_later
ORDER BY cohort_day, days_later""",
    ),
)


@cache
def examples():
    """The wire form; ``datasets`` comes from the compiler's own validation."""
    from .compiler import validate

    return tuple(
        {
            "id": example.id,
            "title": example.title,
            "description": example.description,
            "sql": example.sql,
            "datasets": sorted({table.name for table in validate(example.sql)[1]}),
        }
        for example in EXAMPLES
    )
