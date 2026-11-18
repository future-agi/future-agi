import { describe, expect, it } from "vitest";
import {
  TracerEvalTaskGetEvalDetailsQueryParams,
  TracerEvalTaskGetEvalDetailsResponse,
  TracerEvalTaskUnpauseEvalTaskBody,
} from "src/generated/api-contracts/api.zod";
import swagger from "../../../../../api_contracts/openapi/swagger.json";
import { validateContractedRequestConfig } from "../openapi-contract";
import { OPENAPI_CONTRACT } from "../openapi-contract.generated";

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

  it.each([true, false])("preserves start eligibility %s", (canStart) => {
    const response = payload(null);
    response.result.can_start = canStart;
    response.result.status = "pending";
    response.result.last_run = "2026-11-17T00:00:00Z";
    expect(
      TracerEvalTaskGetEvalDetailsResponse.parse(response).result.can_start,
    ).toBe(canStart);
    const definition = swagger.definitions.EvalTaskDetailsResult;
    expect(definition.properties.can_start).toEqual({
      title: "Can start",
      type: "boolean",
    });
    expect(
      OPENAPI_CONTRACT.definitions.EvalTaskDetailsResult.properties.can_start,
    ).toEqual(definition.properties.can_start);
  });

  it.each([{}, { start: true }, { start: false }])(
    "preserves start-only intent and accepts legacy resume %j",
    (body) => {
      expect(TracerEvalTaskUnpauseEvalTaskBody.parse(body)).toEqual(body);
      const path = "/tracer/eval-task/unpause_eval_task/";
      const operation = swagger.paths[path].post;
      const runtime = OPENAPI_CONTRACT.endpoints[path].post;
      const requestSchema = operation.parameters.find(
        ({ name }) => name === "data",
      ).schema;
      expect(requestSchema.$ref).toBe("#/definitions/EvalTaskUnpauseRequest");
      expect(runtime.requestBody).toEqual(requestSchema);
      const definition = swagger.definitions.EvalTaskUnpauseRequest;
      expect(definition.properties.start).toEqual({
        title: "Start",
        type: "boolean",
      });
      expect(definition.required || []).not.toContain("start");
      expect(OPENAPI_CONTRACT.definitions.EvalTaskUnpauseRequest).toEqual(
        definition,
      );
      expect(operation.responses["409"].schema.$ref).toBe(
        "#/definitions/ApiErrorResponse",
      );
      expect(runtime.responses["409"]).toEqual(
        operation.responses["409"].schema,
      );
      expect(
        validateContractedRequestConfig({
          url: `${path}?eval_task_id=${id}`,
          method: "post",
          data: body,
        }).ok,
      ).toBe(true);
    },
  );

  it("rejects an invalid start-only discriminator", () => {
    const body = { start: "not-a-boolean" };
    expect(TracerEvalTaskUnpauseEvalTaskBody.safeParse(body).success).toBe(
      false,
    );
    expect(
      validateContractedRequestConfig({
        url: `/tracer/eval-task/unpause_eval_task/?eval_task_id=${id}`,
        method: "post",
        data: body,
      }).ok,
    ).toBe(false);
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
