import { describe, expect, it } from "vitest";

import {
  ExternalExperimentRunResultsIngestBody,
  ExternalExperimentRunResultsIngestResponse,
  ExternalExperimentRunsCreateBody,
  ExternalExperimentRunsCreateResponse,
} from "src/generated/api-contracts/api.zod";

import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";
import createdRun from "./fixtures/external-run-created.json";
import ingestedRun from "./fixtures/external-run-results.json";

const identity = createdRun.result.expected_items[0];
const createBody = {
  run_id: createdRun.result.id,
  dataset_version_id: createdRun.result.dataset_version_id,
  expected_items: [identity],
};
const resultItem = { ...identity, receipt_key: "test", status: "completed" };
const createPath = "/model-hub/experiment-runs/";
const resultsPath = `/model-hub/experiment-runs/${createdRun.result.id}/results/`;
const runtimeRequest = (url, data) =>
  validateContractedRequestConfig({ url, method: "post", data }).ok;

it.each([
  ["create", createdRun, ExternalExperimentRunsCreateResponse, createPath],
  [
    "ingest",
    ingestedRun,
    ExternalExperimentRunResultsIngestResponse,
    resultsPath,
  ],
])(
  "accepts the actual %s 200 response in generated and runtime contracts",
  (_name, data, schema, url) => {
    expect(schema.safeParse(data).success).toBe(true);
    expect(
      validateContractedResponse({
        status: 200,
        data,
        config: { url, method: "post" },
      }).ok,
    ).toBe(true);
  },
);

it("preserves declared nullable evaluator and header fields", () => {
  const response = structuredClone(createdRun);
  response.result.experiment_id = null;
  response.result.started_at = null;
  response.result.completed_at = null;
  for (const field of [
    "binding_config",
    "binding_model",
    "prompt_messages",
    "config_snapshot",
    "criteria",
    "model",
    "output_type_normalized",
    "pass_threshold",
    "choice_scores",
  ]) {
    response.result.evaluator_bindings[0][field] = null;
  }
  expect(ExternalExperimentRunsCreateResponse.safeParse(response).success).toBe(
    true,
  );
  expect(
    validateContractedResponse({
      status: 200,
      data: response,
      config: { url: createPath, method: "post" },
    }).ok,
  ).toBe(true);
});

describe.each([
  [
    "generated",
    (data) => ExternalExperimentRunsCreateBody.safeParse(data).success,
  ],
  ["runtime", (data) => runtimeRequest(createPath, data)],
])("external creation %s contract", (_name, accepts) => {
  it("allows nullable experiment and nested JSON config", () => {
    expect(
      accepts({
        ...createBody,
        experiment_id: null,
        metadata: { tags: ["tag", null] },
        model_config: { model: null, temperature: 0 },
      }),
    ).toBe(true);
  });
  it.each([
    { ...createBody, workspace_id: "no-override" },
    { ...createBody, expected_items: [{ ...identity, other: true }] },
    { ...createBody, expected_items: [] },
    { ...createBody, model_config: [] },
    { ...createBody, expected_items: Array(10001).fill(identity) },
  ])("rejects fields outside the bounded create contract", (data) =>
    expect(accepts(data)).toBe(false),
  );
});

describe.each([
  [
    "generated",
    (data) => ExternalExperimentRunResultsIngestBody.safeParse(data).success,
  ],
  ["runtime", (data) => runtimeRequest(resultsPath, data)],
])("external results %s contract", (_name, accepts) => {
  it.each([null, "answer", 1, false, ["answer", null], { nested: null }])(
    "accepts arbitrary JSON output %j and nullable metrics",
    (output) => {
      expect(
        accepts({
          items: [
            {
              ...resultItem,
              output,
              error: null,
              token_usage: null,
              trace_id: null,
              root_span_id: null,
              latency_ms: null,
              cost: null,
            },
          ],
        }),
      ).toBe(true);
    },
  );
  it.each([
    { items: [] },
    { items: Array(101).fill(resultItem) },
    { items: [resultItem], organization_id: "no-override" },
    { items: [{ ...resultItem, workspace_id: "no-override" }] },
    {
      items: [
        {
          ...resultItem,
          score_refs: [
            {
              metric_id: createdRun.result.evaluator_bindings[0].metric_id,
              evaluator_version_id:
                createdRun.result.evaluator_bindings[0].evaluator_version_id,
              status: "unknown",
              other: true,
            },
          ],
        },
      ],
    },
  ])("rejects fields outside the bounded result contract", (data) =>
    expect(accepts(data)).toBe(false),
  );
});
