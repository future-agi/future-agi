# Simulation run analytics

The v3 run analytics response now includes a typed `dashboard` read model for
the run analytics page. Existing API routes and legacy analytics APIs are unchanged.
The analytics implementation adds no migrations or database indexes. The merged
environment integration includes its own migrations.

## Available widgets

- Fourteen summary metrics with recorded-data coverage.
- Success, goal outcome, sentiment and disconnection breakdowns.
- Evaluation pass rates, voice latency percentiles and interruptions.
- Pipeline cost and task latency charts; latency percentile curve.
- Five metric distributions, CSAT histogram and agent response-time histogram.
- Weakest use cases, tool volumes and tool failure rates.
- Eight slowest and most expensive calls, with call-detail links.
- Exact chart-guide help text, success-chart links to server-filtered calls,
  hide/restore widgets, named browser-local views and browser print/save as PDF.

Filtering and aggregation stay server-side. The frontend formats and renders the
returned values. Saved views contain display preferences only, are scoped to a
run in the current browser, and are not shared between devices or users.

## Data semantics and limitations

Missing measurements remain null, not zero. Task success uses the same outcome as
the calls list. Provider verdicts are separate: agreement compares only calls with
both a provider verdict and a conclusive evaluation verdict. Every applicable
grader must pass for an evaluation pass; partial results remain inconclusive.
Explicit handoffs appear separately as Escalated in the goal breakdown.
Breakdown shares use all calls and retain unknown outcomes; they do not reproduce
the inconsistent sample denominator in the printed reference. Tool failure rates use
only explicit invocation verdicts, independently of whether the enclosing call
passed. Calls with messages or an available transcript count as connected.

Agent/customer speaking share is derived from the recorded talk ratio. End-to-end
latency uses task wall-clock duration, separately from agent response latency.
Cost per pass requires complete run-cost coverage. Voice SLO percentiles describe
recorded per-call pipeline timings, not an inferred per-turn timing distribution.

Runs with at most 100 calls show individual calls. Larger runs show up to 100
server-generated time buckets (mean duration and summed component costs). Calls
without a start timestamp remain in summary metrics but cannot enter time buckets.
Top-tool lists are limited to 20; performance tails to eight; risk to seven goals.
CSAT uses recorded scores within 0–10, rounding fractional values to the nearest
histogram score. Response-time bins keep 550 ms on an exact boundary; values at
or above that threshold count as breaches. The server returns counts, coverage,
percentages, p50 and p95. Neither chart triggers new evaluations or provider calls.

### Unavailable reference features

- Error Feed failure attribution/retry policies and the critical-failure banner
  are not integrated: simulation call records do not carry those classifications.
- First-word timing and ASR word-error measurements are not emitted by the current
  data pipeline. The page reports them as unmeasured rather than fabricating values.
- A distinct transport-cost component is unavailable. The chart labels the
  recorded storage-cost component as storage, not transport.

## Function-call transcript cards

Voice transcripts render structured function calls with a separate tool header,
recorded duration when available, and monospace arguments and results. Copy and
search retain the full content. Unknown timings are not fabricated: tool calls
without usable timestamps remain after the timed transcript. The hosted producer
must emit numeric epoch-second timestamps; its reader can coerce non-numeric
values to zero before storing the original tool-trace artifact.

## Verification

- Targeted frontend analytics, run-detail and call-table tests: 22 passed.
- Frontend generated-contract checks passed; pre-existing API paths unchanged.
- Isolated PostgreSQL ORM checks passed for schema validation, missing metrics,
  success verdicts, tool verdicts, per-call series, tails and percentiles.
- An isolated browser check with synthetic data verified 17 widgets, chart
  rendering, hide/restore, call drilldown, PDF chart/style cloning and cleanup,
  and desktop/mobile rendering. This is not a full-stack integration test.
- An earlier synthetic 50,006-call PostgreSQL baseline measured the dashboard builder
  at 255–268 ms over three runs. This excludes the existing endpoint queries,
  authentication, joins used to annotate the real queryset, and network transfer;
  it also predates the histogram extensions and does not establish subsecond
  latency for the current complete endpoint.
- Function-call rendering, search/copy, normalization and drawer tests: 12 passed.

Full application backend tests and managed E2E runs remain unverified. The managed
E2E stack does not include the simulation runner needed for the complete flow.
