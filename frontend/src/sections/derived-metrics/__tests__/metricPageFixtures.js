export const scope = {
  organizationId: "org-a",
  workspaceId: "ws-a",
  projectId: "project-a",
};
export const window = {
  window_start: "2026-11-01T00:00:00Z",
  window_end: "2026-11-08T00:00:00Z",
  rounding: "Rounded to 15 minutes",
};
export const metric = {
  id: "metric-a",
  project_id: "project-a",
  name: "Resolution rate",
  description: "Settled support conversations",
  current_version: 3,
  active_version: "version-2",
  snapshot_supported: true,
  definition: {
    id: "version-2",
    version: 2,
    kind: "share",
    level: "conversation",
    selection_unit: "session",
    value_type: "boolean",
    value_format: "percent",
    unit: "",
    weighting: "none",
    inputs: [
      {
        alias: "passed",
        source: "eval",
        config_id: "eval-a",
        value_type: "boolean",
        reducer: "latest",
      },
    ],
    field_spec: {
      conditions: [{ alias: "passed", operator: "eq", value: true }],
    },
    backfill_status: "complete",
    backfill_days_done: 7,
    backfill_days_total: 7,
    value_labels: { match: "Counts", other: "Doesn't count" },
  },
  draft_definition: {
    id: "version-3",
    version: 3,
    level: "span",
    backfill_status: "running",
    backfill_days_done: 2,
    backfill_days_total: 7,
  },
};
const percentileInput = {
  alias: "duration",
  source: "span",
  field: "latency_ms",
  value_type: "number",
  reducer: "mean",
};
export const percentileMetrics = [0.5, 0.95].map((percentile) => ({
  ...metric,
  id: `metric-p${percentile * 100}`,
  name: "Conversation duration",
  current_version: 1,
  active_version: "percentile-version-1",
  draft_definition: null,
  definition: {
    ...metric.definition,
    id: "percentile-version-1",
    version: 1,
    kind: "percentile",
    value_type: "number",
    value_format: "number",
    unit: "ms",
    value_labels: {},
    formula: "mean(duration)",
    inputs: [percentileInput],
    field_spec: {
      inputs: [percentileInput],
      value_alias: "duration",
      percentile,
    },
  },
}));

export const provenance = {
  manifest_id: "manifest-1",
  generation: 1,
  version_id: "version-2",
  version: 2,
  definition_hash: "hash",
  updated_at: "2026-11-08T00:00:00Z",
  backfill_days_done: 7,
  backfill_days_total: 7,
};
export const point = {
  value: 0.62,
  point_status: "ok",
  rate_eligible: true,
  weighted: true,
  unit: "",
  value_format: "percent",
  unit_noun: "conversations",
  value_mode: "materialized",
  eligible_count: 137,
  evaluated_count: 81,
  missing_count: 56,
  uncertainty: {
    status: "ok",
    reason: "",
    method: "wilson",
    ci_low: 0.51,
    ci_high: 0.7,
    confidence_level: 0.95,
  },
};
export const kpi = {
  provenance,
  effective_window: window,
  current: point,
  previous: { ...point, value: 0.5 },
  delta: 0.12,
  delta_uncertainty: {
    status: "not_estimable",
    reason: "paired_covariance_unavailable",
    ci_low: null,
    ci_high: null,
  },
};
export const trend = {
  provenance,
  effective_window: window,
  results: [
    {
      ...point,
      window_start: window.window_start,
      window_end: "2026-11-02T00:00:00Z",
      group_key: "",
    },
    {
      ...point,
      value: null,
      point_status: "missing_inputs",
      window_start: "2026-11-02T00:00:00Z",
      window_end: "2026-11-03T00:00:00Z",
      group_key: "",
    },
    {
      ...point,
      value: 0,
      window_start: "2026-11-03T00:00:00Z",
      window_end: "2026-11-04T00:00:00Z",
      group_key: "",
    },
  ],
};
export const field = {
  id: "metric-a",
  level: "conversation",
  kind: "share",
  value_type: "boolean",
  value_format: "percent",
  unit: "",
  value_labels: { match: "Counts", other: "Doesn't count" },
  projection: false,
};
export const driver = {
  unit_id: "session-a",
  status: "value",
  no_value_reason: "",
  value_number: null,
  value_bool: false,
  value_text: null,
  inputs: {},
  inputs_bool: { passed: false },
  inputs_text: { locale: "fr" },
  influence: -0.013,
  eligible_count: 1,
  evaluated_count: 1,
  missing_count: 0,
};
