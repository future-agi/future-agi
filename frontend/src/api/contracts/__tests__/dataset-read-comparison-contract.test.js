import { describe, expect, it } from "vitest";

import {
  ExperimentRunsCompareBody,
  ExperimentRunsCompareResponse,
  ModelHubDatasetsVersionsListResponse,
  ModelHubDatasetsVersionsReadResponse,
} from "src/generated/api-contracts/api.zod";

import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";
import detail from "./fixtures/dataset-version-detail.json";
import listing from "./fixtures/dataset-version-list.json";
import assessed from "./fixtures/run-comparison-assessed.json";
import missing from "./fixtures/run-comparison-missing.json";
import unassessed from "./fixtures/run-comparison-unassessed.json";

const detailUrl = `/model-hub/datasets/${detail.result.version.dataset}/versions/${detail.result.version.id}/`;
const listUrl = `/model-hub/datasets/${detail.result.version.dataset}/versions/`;
const comparisonUrl = "/model-hub/experiment-runs/compare/";
const runtime = (data, url, method) =>
  validateContractedResponse({
    status: 200,
    data,
    config: { url, method },
  }).ok;

it.each([
  ["detail", detail, ModelHubDatasetsVersionsReadResponse, detailUrl, "get"],
  ["list", listing, ModelHubDatasetsVersionsListResponse, listUrl, "get"],
  ["assessed", assessed, ExperimentRunsCompareResponse, comparisonUrl, "post"],
  [
    "unassessed",
    unassessed,
    ExperimentRunsCompareResponse,
    comparisonUrl,
    "post",
  ],
  [
    "missing on both sides",
    missing,
    ExperimentRunsCompareResponse,
    comparisonUrl,
    "post",
  ],
])(
  "accepts actual %s API response without dropping values",
  (_name, data, schema, url, method) => {
    expect(schema.parse(data)).toEqual(data);
    expect(runtime(data, url, method)).toBe(true);
  },
);

describe.each([
  [
    "generated",
    (data) => ModelHubDatasetsVersionsReadResponse.safeParse(data).success,
  ],
  ["runtime", (data) => runtime(data, detailUrl, "get")],
])("dataset read %s contract", (_name, accepts) => {
  it.each([
    ["id", "not-a-uuid"],
    ["legacy_row", "not-a-uuid"],
    ["input", null],
    ["source_trace_id", 42],
    ["metadata", "scalar"],
  ])("rejects malformed %s", (field, value) => {
    const response = structuredClone(detail);
    response.result.items[0][field] = value;
    expect(accepts(response)).toBe(false);
  });
});

describe.each([
  [
    "generated",
    (data) => ExperimentRunsCompareResponse.safeParse(data).success,
  ],
  ["runtime", (data) => runtime(data, comparisonUrl, "post")],
])("comparison %s contract", (_name, accepts) => {
  it.each([
    ["example_id", "not-a-uuid"],
    ["status", "invented"],
    ["missing_side", "both"],
    ["baseline_score", "0.8"],
  ])("rejects malformed case %s", (field, value) => {
    const response = structuredClone(assessed);
    response.result.comparison.cases[0][field] = value;
    expect(accepts(response)).toBe(false);
  });
  it("rejects malformed missing identity and trace reference", () => {
    const response = structuredClone(missing);
    response.result.missing_by_side.baseline[0] = ["case", 1];
    expect(accepts(response)).toBe(false);
    const traced = structuredClone(assessed);
    traced.result.trace_refs[0].baseline.run_item_id = "invalid";
    expect(accepts(traced)).toBe(false);
  });
});

it("preserves optional null passing_score in both request validators", () => {
  const body = {
    baseline_run_id: assessed.result.baseline_run_id,
    candidate_run_id: assessed.result.candidate_run_id,
    metric_id: assessed.result.metric_id,
    passing_score: null,
  };
  expect(ExperimentRunsCompareBody.safeParse(body).success).toBe(true);
  expect(
    validateContractedRequestConfig({
      url: comparisonUrl,
      method: "post",
      data: body,
    }).ok,
  ).toBe(true);
});

it("accepts and preserves a case-level release gate report", () => {
  const body = {
    baseline_run_id: assessed.result.baseline_run_id,
    candidate_run_id: assessed.result.candidate_run_id,
    metric_id: assessed.result.metric_id,
    release_gate: {
      max_regressions: 0,
      critical_cases: [
        { example_id: assessed.result.comparison.cases[0].example_id, trial_number: 1 },
      ],
    },
  };
  expect(ExperimentRunsCompareBody.safeParse(body).success).toBe(true);
  const response = structuredClone(assessed);
  response.result.release_gate = {
    verdict: "fail",
    expected_count: 2,
    comparable_count: 2,
    regression_count: 1,
    policy: body.release_gate,
    passing_score: null,
    higher_is_better: true,
    blockers: [{ ...body.release_gate.critical_cases[0], reason: "critical_regression" }],
  };
  expect(ExperimentRunsCompareResponse.safeParse(response).success).toBe(true);
  expect(runtime(response, comparisonUrl, "post")).toBe(true);
});

it("accepts and preserves optional repeated-trial stability analysis", () => {
  const response = structuredClone(assessed);
  const exampleId = response.result.comparison.cases[0].example_id;
  response.result.trial_stability = [
    {
      example_id: exampleId,
      expected_trial_count: 2,
      baseline: {
        recorded_trial_count: 2,
        missing_trial_count: 0,
        assessed_trial_count: 2,
        score_average: 0.5,
        score_min: 0,
        score_max: 1,
        score_stddev: 0.5,
      },
      candidate: {
        recorded_trial_count: 2,
        missing_trial_count: 0,
        assessed_trial_count: 2,
        score_average: 0.5,
        score_min: 0.5,
        score_max: 0.5,
        score_stddev: 0,
      },
      paired: { paired_trial_count: 2, delta_average: 0, delta_stddev: 0.5 },
    },
  ];
  expect(ExperimentRunsCompareResponse.parse(response)).toEqual(response);
  expect(runtime(response, comparisonUrl, "post")).toBe(true);

  response.result.trial_stability[0].baseline.score_stddev = "0.5";
  expect(ExperimentRunsCompareResponse.safeParse(response).success).toBe(false);
  expect(runtime(response, comparisonUrl, "post")).toBe(false);

  response.result.trial_stability[0].baseline = {
    recorded_trial_count: 0,
    missing_trial_count: 2,
    assessed_trial_count: 0,
    score_average: null,
    score_min: null,
    score_max: null,
    score_stddev: null,
  };
  response.result.trial_stability[0].paired = {
    paired_trial_count: 0,
    delta_average: null,
    delta_stddev: null,
  };
  expect(ExperimentRunsCompareResponse.parse(response)).toEqual(response);
  expect(runtime(response, comparisonUrl, "post")).toBe(true);
  const body = {
    baseline_run_id: assessed.result.baseline_run_id,
    candidate_run_id: assessed.result.candidate_run_id,
    metric_id: assessed.result.metric_id,
    include_trial_stability: true,
  };
  expect(ExperimentRunsCompareBody.parse(body).include_trial_stability).toBe(
    true,
  );
  expect(
    validateContractedRequestConfig({
      url: comparisonUrl,
      method: "post",
      data: body,
    }).ok,
  ).toBe(true);
});

describe.each([
  ["generated", (data) => ExperimentRunsCompareBody.safeParse(data).success],
  ["runtime", (data) => validateContractedRequestConfig({ url: comparisonUrl, method: "post", data }).ok],
])("release gate %s request contract", (_name, accepts) => {
  const identity = { example_id: assessed.result.comparison.cases[0].example_id, trial_number: 1 };
  it.each([
    null, [], { unexpected: true }, { max_regressions: true }, { max_regressions: -1 }, { max_regressions: 0.5 },
    { critical_cases: [{ ...identity, trial_number: 0 }] },
    { critical_cases: [{ ...identity, extra: true }] },
  ])("rejects malformed gate policy %j", (release_gate) => {
    expect(accepts({ baseline_run_id: assessed.result.baseline_run_id, candidate_run_id: assessed.result.candidate_run_id, metric_id: assessed.result.metric_id, release_gate })).toBe(false);
  });
});

it("preserves explicit null identity in an empty-plan blocker", () => {
  const response = structuredClone(missing);
  response.result.release_gate = {
    verdict: "fail", expected_count: 0, comparable_count: 0, regression_count: 0,
    policy: { max_regressions: 0, critical_cases: [] }, passing_score: null,
    higher_is_better: true, blockers: [{ example_id: null, trial_number: null, reason: "empty_plan" }],
  };
  expect(ExperimentRunsCompareResponse.parse(response)).toEqual(response);
  expect(runtime(response, comparisonUrl, "post")).toBe(true);
  response.result.release_gate.verdict = "unknown";
  expect(ExperimentRunsCompareResponse.safeParse(response).success).toBe(false);
  expect(runtime(response, comparisonUrl, "post")).toBe(false);
});
