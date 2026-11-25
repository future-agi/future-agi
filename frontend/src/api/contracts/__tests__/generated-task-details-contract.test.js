import { describe, expect, it } from "vitest";
import {
  TracerEvalTaskGetEvalDetailsQueryParams,
  TracerEvalTaskGetEvalDetailsResponse,
} from "src/generated/api-contracts/api.zod";
import swagger from "../../../../../api_contracts/openapi/swagger.json";

const id = "11111111-1111-4111-8111-111111111111";
const policy = {
  version: 1,
  sampling_unit: "session",
  mode: "closed",
  window: { start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" },
  default: { target_n: 10 },
};
const payload = (samplingPolicy) => ({
  status: true,
  result: {
    id,
    name: null,
    project_id: id,
    project_name: "Synthetic project",
    status: "completed",
    filters_applied: {},
    created_at: "2026-10-03T00:00:00Z",
    evals_applied: [
      {
        id,
        name: "Judge",
        template_id: null,
        templateId: null,
        mapping: { output: "response", optional: null },
        model: null,
        config: { enabled: true, nested: [1, "value", null] },
        error_localizer: false,
        evalType: null,
        templateType: "single",
        outputType: null,
      },
    ],
    spans_limit: null,
    sampling_rate: null,
    sampling_policy: samplingPolicy,
    last_run: null,
    run_type: "historical",
    row_type: "spans",
  },
});

describe("generated task-details contract", () => {
  it("requires the actual eval_id query rather than pagination", () => {
    expect(TracerEvalTaskGetEvalDetailsQueryParams.safeParse({}).success).toBe(
      false,
    );
    expect(
      TracerEvalTaskGetEvalDetailsQueryParams.parse({ eval_id: id }),
    ).toEqual({ eval_id: id });
    const operation = swagger.paths["/tracer/eval-task/get_eval_details/"].get;
    expect(operation.parameters.map(({ name }) => name)).toEqual(["eval_id"]);
    expect(operation.responses["200"].schema.$ref).toBe(
      "#/definitions/EvalTaskDetailsResponse",
    );
    expect(
      swagger.definitions.EvalTaskDetailsSamplingPolicy["x-nullable"],
    ).toBe(true);
  });

  it.each([
    null,
    policy,
    {
      ...policy,
      mode: "live",
      window: { ...policy.window, end: null },
      default: { rate: 25 },
    },
  ])("accepts the unchanged status/result envelope and policy %j", (value) => {
    const parsed = TracerEvalTaskGetEvalDetailsResponse.parse(payload(value));
    if (value === null) expect(parsed.result.sampling_policy).toBeNull();
    else expect(parsed.result.sampling_policy).toMatchObject(value);
    expect(parsed.result.evals_applied[0].config).toEqual({
      enabled: true,
      nested: [1, "value", null],
    });
    expect(parsed.result.last_run).toBeNull();
    expect(parsed).not.toHaveProperty("count");
  });

  it.each([null, ""])("accepts legacy empty task state %j", (value) => {
    const response = payload(null);
    Object.assign(response.result, {
      status: value,
      run_type: value,
      filters_applied: null,
    });
    expect(
      TracerEvalTaskGetEvalDetailsResponse.parse(response).result,
    ).toMatchObject({
      status: value,
      run_type: value,
      filters_applied: null,
    });
  });

  it("rejects paginated envelopes and malformed sampling policies", () => {
    expect(
      TracerEvalTaskGetEvalDetailsResponse.safeParse({ count: 0, results: [] })
        .success,
    ).toBe(false);
    expect(
      TracerEvalTaskGetEvalDetailsResponse.safeParse(
        payload({ ...policy, version: 2 }),
      ).success,
    ).toBe(false);
  });
});
