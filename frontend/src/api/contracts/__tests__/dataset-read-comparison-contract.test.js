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
