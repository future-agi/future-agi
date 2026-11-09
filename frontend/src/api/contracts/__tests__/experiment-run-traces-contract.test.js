/**
 * The runtime response contract for the two reads the run panel makes
 * (ExperimentRunTracesPanel, Ruling 95): GET /model-hub/experiment-runs/{id}/
 * (ExperimentRunRead) and GET /model-hub/experiment-runs/{id}/traces/
 * (ExperimentRunTracesResult), with every nullable field null.
 *
 * The panel's own tests mock the generated client, so they never reach this
 * validator. And the generated TypeScript types and zod schemas
 * (src/generated/api-contracts/api.schemas.ts, api.zod.ts) are not the
 * authority for nullability here: orval ignores Swagger 2's `x-nullable`, so
 * they declare ordinal, label and app_url non-nullable. The runtime validator
 * (openapi-contract.js) reads the Swagger contract and honours `x-nullable`;
 * these tests hold it to that, in strict mode, so a run made before ordinals
 * or on a deployment with no frontend URL still renders.
 */
import { describe, it, expect } from "vitest";
import {
  handleApiContractValidation,
  validateContractedResponse,
} from "../openapi-contract";

const RUN = "11111111-1111-4111-8111-111111111111";
const DATASET_VERSION = "55555555-5555-4555-8555-555555555555";
const EXAMPLE = "44444444-4444-4444-8444-444444444444";
const NOW = "2026-10-24T07:00:00Z";

const runRead = (extra = {}) => ({
  id: RUN,
  ordinal: null,
  label: null,
  app_url: null,
  dataset_version_id: DATASET_VERSION,
  experiment_id: null,
  task_kind: "external",
  status: "completed",
  expected_items: [],
  evaluator_bindings: [],
  model_config: {},
  metadata: {},
  started_at: null,
  completed_at: null,
  created_at: NOW,
  updated_at: NOW,
  plan_size: 0,
  item_counts: {
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    skipped: 0,
    not_started: 0,
  },
  ...extra,
});

const response = (url, result) => ({
  config: { url, method: "get" },
  status: 200,
  data: { status: true, result },
});

// strict: what the app does when response contracts are enforced
const strictly = (result) => () =>
  handleApiContractValidation(result, { strict: true });

describe("experiment run read and traces response contracts", () => {
  it("accepts a run read whose ordinal, label and app_url are null, in strict mode", () => {
    const result = validateContractedResponse(
      response(`/model-hub/experiment-runs/${RUN}/`, runRead()),
    );
    expect(result.skipped).toBeUndefined();
    expect(result.endpoint).toBeTruthy();
    expect(result).toMatchObject({ ok: true });
    expect(strictly(result)).not.toThrow();
  });

  it("accepts the same run read with those fields set", () => {
    const result = validateContractedResponse(
      response(
        `/model-hub/experiment-runs/${RUN}/`,
        runRead({
          ordinal: 2,
          label: "#2",
          app_url: `http://127.0.0.1:3031/dashboard/develop/x?tab=experiments&run=${RUN}`,
          started_at: NOW,
          completed_at: NOW,
        }),
      ),
    );
    expect(result).toMatchObject({ ok: true });
    expect(strictly(result)).not.toThrow();
  });

  it("still rejects a run read missing a required field, in strict mode", () => {
    const { ordinal: _drop, ...missing } = runRead();
    const result = validateContractedResponse(
      response(`/model-hub/experiment-runs/${RUN}/`, missing),
    );
    expect(result.ok).toBe(false);
    expect(strictly(result)).toThrow(/response contract validation failed/);
  });

  it("accepts a traces page whose nullable fields are null, in strict mode", () => {
    const row = {
      trace_id: "trace-a1",
      root_span_id: null,
      example_id: EXAMPLE,
      trial_number: 1,
      item_status: "completed",
      trace_run_id: null,
      project_id: null,
      root_span_name: null,
      start_time: null,
      span_count: 1,
      trace_example_id: null,
      trace_trial: null,
    };
    const result = validateContractedResponse(
      response(`/model-hub/experiment-runs/${RUN}/traces/?limit=50&offset=0`, {
        run_id: RUN,
        traces: [row],
        not_joined: [
          {
            trace_id: "trace-x",
            root_span_id: null,
            example_id: EXAMPLE,
            trial_number: 1,
            item_status: "completed",
            trace_run_id: null,
            reason: "not_found",
          },
        ],
        items_with_trace: 2,
        limit: 50,
        offset: 0,
        has_more: false,
      }),
    );
    expect(result.skipped).toBeUndefined();
    expect(result).toMatchObject({ ok: true });
    expect(strictly(result)).not.toThrow();
  });
});
