import type {
  DatasetVersionItemReadApi,
  DatasetVersionReadApi,
  RunAggregateApi,
  RunCaseComparisonApi,
  RunCaseTraceReferencesApi,
  RunComparisonRequestApi,
  RunTraceReferenceApi,
} from "../../src/generated/api-contracts/api.schemas";

const item: DatasetVersionItemReadApi = {
  input: ["text", null],
  expected: null,
  metadata: { optional: null },
  external_id: null,
  legacy_row: null,
  source_trace_id: "",
  source_span_id: "",
};
const scalarItem: DatasetVersionItemReadApi = { input: false, expected: 42 };
const header: DatasetVersionReadApi = {
  parent_version: null,
  created_by: null,
  organization: null,
  workspace: null,
};
const aggregate: Pick<
  RunAggregateApi,
  "score_average" | "score_min" | "score_max" | "pass_count" | "pass_rate"
> = {
  score_average: null,
  score_min: null,
  score_max: null,
  pass_count: null,
  pass_rate: null,
};
const comparison: RunCaseComparisonApi = {
  example_id: "id",
  trial_number: 1,
  status: "missing",
  baseline_status: null,
  candidate_status: null,
  baseline_score: null,
  candidate_score: null,
  score_delta: null,
  missing_side: null,
};
const traces: RunCaseTraceReferencesApi = {
  example_id: "id",
  trial_number: 1,
  baseline: null,
  candidate: null,
};
const trace: RunTraceReferenceApi = {
  run_item_id: "id",
  trace_id: null,
  root_span_id: null,
};
const request: RunComparisonRequestApi = {
  baseline_run_id: "id",
  candidate_run_id: "id",
  metric_id: "id",
  passing_score: null,
};

const invalidScore: RunCaseComparisonApi = {
  ...comparison,
  // @ts-expect-error Scalar strings cannot replace a numeric score.
  baseline_score: "1",
};
// @ts-expect-error Input preserves the database's non-null boundary.
const invalidInput: DatasetVersionItemReadApi = { input: null };
// @ts-expect-error Metadata is an object, while its values may be arbitrary JSON.
const invalidMetadata: DatasetVersionItemReadApi = { metadata: "scalar" };
const invalidStatus: RunCaseComparisonApi = {
  ...comparison,
  // @ts-expect-error Comparison status remains a closed enum.
  status: "invented",
};

void [
  item,
  scalarItem,
  header,
  aggregate,
  comparison,
  traces,
  trace,
  request,
  invalidScore,
  invalidInput,
  invalidStatus,
  invalidMetadata,
];
