import { afterEach, describe, expect, it, vi } from "vitest";

import {
  findOpenApiEndpoint,
  shouldEnforceApiResponseContracts,
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";
import { OPENAPI_CONTRACT } from "../openapi-contract.generated";

describe("OpenAPI runtime contract", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps response contracts warn-only unless explicitly promoted to strict", () => {
    expect(shouldEnforceApiResponseContracts()).toBe(false);

    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
    expect(shouldEnforceApiResponseContracts()).toBe(true);

    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "off");
    expect(shouldEnforceApiResponseContracts()).toBe(false);
  });

  it("finds endpoints across the full Management API surface", () => {
    expect(findOpenApiEndpoint("/usage/ee/licenses/", "get")).toMatchObject({
      template: "/usage/ee/licenses/",
      method: "get",
    });
    expect(
      findOpenApiEndpoint("/falcon-ai/conversations/", "post"),
    ).toMatchObject({
      template: "/falcon-ai/conversations/",
      method: "post",
    });
    expect(
      findOpenApiEndpoint("/model-hub/annotation-queues/queue-1/items/", "get"),
    ).toMatchObject({
      template: "/model-hub/annotation-queues/{queue_id}/items/",
      method: "get",
    });
  });

  it("prefers concrete routes over dynamic siblings when matching contracts", () => {
    expect(
      findOpenApiEndpoint("/model-hub/experiments/v2/list/", "get"),
    ).toMatchObject({
      template: "/model-hub/experiments/v2/list/",
      method: "get",
    });
    expect(
      findOpenApiEndpoint("/simulate/run-tests/active/", "get"),
    ).toMatchObject({
      template: "/simulate/run-tests/active/",
      method: "get",
    });
    expect(
      findOpenApiEndpoint("/tracer/feed/issues/stats/", "get"),
    ).toMatchObject({
      template: "/tracer/feed/issues/stats/",
      method: "get",
    });
  });

  it("validates request bodies from backend serializer schemas", () => {
    expect(
      validateContractedRequestConfig({
        url: "/accounts/2fa/recovery-codes/regenerate/",
        method: "post",
        data: { code: "123456", password: "password" },
      }),
    ).toMatchObject({ ok: true });

    const result = validateContractedRequestConfig({
      url: "/accounts/2fa/recovery-codes/regenerate/",
      method: "post",
      data: { code: "1", password: "" },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "request body contract validation failed",
    );
  });

  it("keeps generated alert-log mutation request bodies documented", () => {
    expect(
      findOpenApiEndpoint("/tracer/user-alert-logs/", "post").contract
        .requestBody,
    ).toEqual({ $ref: "#/definitions/UserAlertMonitorLogWriteRequest" });
    expect(
      findOpenApiEndpoint(
        "/tracer/user-alert-logs/7b4d69c3-6a5b-48cf-8d7e-2bcdf488e1e5/",
        "put",
      ).contract.requestBody,
    ).toEqual({ $ref: "#/definitions/UserAlertMonitorLogWriteRequest" });
    expect(
      findOpenApiEndpoint(
        "/tracer/user-alert-logs/7b4d69c3-6a5b-48cf-8d7e-2bcdf488e1e5/",
        "patch",
      ).contract.requestBody,
    ).toEqual({ $ref: "#/definitions/UserAlertMonitorLogWriteRequest" });
    expect(
      findOpenApiEndpoint("/tracer/user-alert-logs/resolve/", "post").contract
        .requestBody,
    ).toEqual({ $ref: "#/definitions/UserAlertMonitorLogResolveRequest" });
  });

  it("accepts dashboard custom time ranges without a preset sentinel", () => {
    expect(
      validateContractedRequestConfig({
        url: "/tracer/dashboard/query/",
        method: "post",
        data: {
          workflow: "observability",
          project_ids: ["5073ebad-e148-4b2b-a839-bcc51f612294"],
          time_range: {
            custom_start: "2026-05-24T14:00:00.000Z",
            custom_end: "2026-05-31T14:00:00.000Z",
          },
          granularity: "day",
          metrics: [
            {
              id: "latency",
              name: "latency",
              display_name: "Latency",
              type: "system_metric",
              source: "traces",
              aggregation: "avg",
              unit: "ms",
            },
          ],
          filters: [],
          breakdowns: [],
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts JSON-valued Gateway provider config maps", () => {
    expect(
      validateContractedRequestConfig({
        url: "/agentcc/gateways/default/update-provider/",
        method: "post",
        data: {
          name: "bedrock",
          config: {
            api_format: "bedrock",
            models: ["anthropic.claude-3-haiku-20240307-v1:0"],
            default_timeout: 45,
            max_concurrent: 3,
          },
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts JSON-valued Gateway guardrail config maps", () => {
    expect(
      validateContractedRequestConfig({
        url: "/agentcc/gateways/default/update-guardrail/",
        method: "post",
        data: {
          name: "keyword-blocklist",
          config: {
            enabled: true,
            action: "log",
            confidence_threshold: 0.65,
            config: {
              words: ["browser_guardrail_smoke"],
            },
          },
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("validates form bodies against the same request schema", () => {
    const body = new FormData();
    body.set("code", "123456");
    body.set("password", "password");

    expect(
      validateContractedRequestConfig({
        url: "/accounts/2fa/recovery-codes/regenerate/",
        method: "post",
        data: body,
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts multipart files for read-only file fields in request schemas", () => {
    const body = new FormData();
    body.set("dataset_id", "eeacd5c1-6491-42a6-b72d-5d36ebeee72d");
    body.set("file", new Blob(["input,output\nhello,world\n"]), "rows.csv");

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/develops/add_rows_from_file/",
        method: "post",
        data: body,
      }),
    ).toMatchObject({ ok: true });

    const invalidBody = new FormData();
    invalidBody.set("dataset_id", "not-a-uuid");
    invalidBody.set("file", new Blob(["input\nhello\n"]), "rows.csv");

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/develops/add_rows_from_file/",
        method: "post",
        data: invalidBody,
      }).ok,
    ).toBe(false);
  });

  it("keeps form-body coercion isolated from JSON request validation", () => {
    const body = new FormData();
    body.set("require_2fa", "true");
    body.set("require_2fa_grace_period_days", "7");

    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/2fa-policy/",
        method: "put",
        data: body,
      }),
    ).toMatchObject({ ok: true });

    const result = validateContractedRequestConfig({
      url: "/accounts/organization/2fa-policy/",
      method: "put",
      data: {
        require_2fa: true,
        require_2fa_grace_period_days: "7",
      },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "request body contract validation failed",
    );
  });

  it("rejects unknown query params from the generated endpoint schema", () => {
    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/?status=active&legacyStatus=active",
        method: "get",
      }).ok,
    ).toBe(false);

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/",
        method: "get",
        params: { status: "active", legacyStatus: "active" },
      }).ok,
    ).toBe(false);
  });

  it("does not infer an empty query contract when backend has not declared one", () => {
    const result = validateContractedRequestConfig({
      url: "/usage/ee/licenses/?legacy=true",
      method: "post",
      data: { band: "team", billing_interval: "monthly" },
    });

    expect(result).toMatchObject({ ok: true });
  });

  it("does not enforce inferred legacy contracts until the endpoint is runtime-backed", () => {
    const endpoint = findOpenApiEndpoint(
      "/tracer/project/list_projects/",
      "get",
    );
    expect(endpoint.contract.runtimeRequestValidation).toBe(false);

    expect(
      validateContractedRequestConfig({
        url: "/tracer/project/list_projects/",
        method: "get",
        params: {
          project_type: "observe",
          page_number: 0,
          page_size: 25,
        },
      }),
    ).toMatchObject({ ok: true, skipped: true });
  });

  it("validates list query params the way DRF query serializers receive them", () => {
    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/members/?filter_status=Active",
        method: "get",
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedRequestConfig({
        url: "/accounts/organization/members/?filter_status=Active&filter_status=Pending",
        method: "get",
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts annotation queue multi-select query params from the backend contract", () => {
    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/q-1/items/",
        method: "get",
        params: {
          status: ["pending", "completed"],
          source_type: ["trace", "dataset_row"],
          page: 1,
          limit: 25,
        },
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/annotation-queues/q-1/items/",
        method: "get",
        params: {
          sourceType: ["trace"],
        },
      }).ok,
    ).toBe(false);
  });

  it("accepts Error Feed severity filter and severity sorting", () => {
    expect(
      validateContractedRequestConfig({
        url: "/tracer/feed/issues/",
        method: "get",
        params: {
          severity: "medium",
          sort_by: "severity",
          sort_dir: "desc",
          limit: 25,
          offset: 0,
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("validates query params after dropping nullish values that Axios omits", () => {
    expect(
      validateContractedRequestConfig({
        url: "/model-hub/develops/get-datasets/",
        method: "get",
        params: {
          search_text: null,
          page: 0,
          page_size: 25,
          sort: undefined,
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("validates dataset detail grid query params against canonical backend keys", () => {
    expect(
      validateContractedRequestConfig({
        url: "/model-hub/develops/eeacd5c1-6491-42a6-b72d-5d36ebeee72d/get-dataset-table/",
        method: "get",
        params: {
          current_page_index: 0,
          page_size: 100,
          filters: "[]",
          sort: '[{"column_id":"score","type":"descending"}]',
          column_config_only: true,
        },
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedRequestConfig({
        url: "/model-hub/develops/eeacd5c1-6491-42a6-b72d-5d36ebeee72d/get-dataset-table/",
        method: "get",
        params: {
          current_page_index: 0,
          page_size: 100,
          filters: "[]",
          sort: '[{"column_id":"score","type":"descending"}]',
          columnConfigOnly: true,
        },
      }).ok,
    ).toBe(false);
  });

  it("does not unwrap response envelopes to hide schema drift", () => {
    const response = {
      status: 200,
      config: { url: "/usage/ee/licenses/", method: "get" },
      data: {
        result: {
          licenses: [],
        },
      },
    };

    const result = validateContractedResponse(response);

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "response contract validation failed",
    );
    expect(result.error.message).toContain("status");
  });

  it("validates default error responses instead of falling back to success schemas", () => {
    const response = {
      status: 404,
      config: {
        url: "/usage/ee/licenses/2db3e0e8-5cec-4bb3-a358-ff1ea0671599/revoke/",
        method: "post",
      },
      data: {
        status: false,
        result: "License not found",
      },
    };

    expect(validateContractedResponse(response)).toMatchObject({
      ok: true,
      endpoint: {
        template: "/usage/ee/licenses/{grant_id}/revoke/",
        method: "post",
      },
    });
  });

  it("accepts string and structured accounts error result contracts", () => {
    const resultSchema =
      OPENAPI_CONTRACT.definitions.AccountsErrorResponse.properties.result;

    expect(resultSchema["x-string-or-object"]).toBe(true);
    expect(resultSchema.properties.error_code.type).toBe("string");

    expect(
      validateContractedResponse({
        status: 400,
        config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
        data: {
          status: false,
          result: "Invalid code. Please try again.",
        },
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedResponse({
        status: 400,
        config: { url: "/accounts/token/", method: "post" },
        data: {
          status: false,
          code: "LOGIN_INVALID_CREDENTIALS",
          detail: "Invalid credentials",
          result: {
            error: "Invalid credentials",
            error_code: "LOGIN_INVALID_CREDENTIALS",
            remaining_attempts: 4,
          },
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts arbitrary JSON response fields declared by backend serializers", () => {
    expect(
      validateContractedResponse({
        status: 200,
        config: {
          url: "/tracer/project/0cc2c8c8-58ee-4369-8f79-547c71de25cb/",
          method: "get",
        },
        data: {
          status: true,
          result: {
            id: "0cc2c8c8-58ee-4369-8f79-547c71de25cb",
            model_type: "GenerativeLLM",
            name: "Observe project",
            trace_type: "observe",
            metadata: {},
            organization: "f7f5533e-44a1-438b-9e6d-6f4747f1eb16",
            workspace: "f7f5533e-44a1-438b-9e6d-6f4747f1eb16",
            created_at: "2026-05-19T00:00:00Z",
            updated_at: "2026-05-19T00:00:00Z",
            config: [],
            source: "prototype",
            session_config: [],
            tags: [],
            sampling_rate: 0.1,
          },
        },
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateContractedResponse({
        status: 200,
        config: {
          url: "/tracer/dashboard/metrics/?sources=traces",
          method: "get",
        },
        data: {
          status: true,
          result: {
            metrics: [
              {
                name: "eval-template-id",
                display_name: "Choices eval",
                category: "eval_metric",
                source: "all",
                sources: ["all"],
                output_type: "CHOICES",
                choices: ["Passed", "Failed"],
              },
            ],
          },
        },
      }),
    ).toMatchObject({ ok: true });
  });

  it("accepts traces-of-session rows with scalar, null, array, and object cells", () => {
    // Regression: DictField(child=JSONField) typed cells as objects and
    // rejected every real (scalar) cell; a strict scalar union would reject
    // the array/object cells the row builder emits for aggregated span
    // attributes and verbatim metadata values. x-json-value now maps to a
    // real recursive JsonValue, so all valid JSON cells pass.
    const tracesOfSessionResponse = (table) => ({
      status: 200,
      config: {
        url: "/tracer/trace/list_traces_of_session/?project_id=p1",
        method: "get",
      },
      data: {
        status: true,
        result: {
          metadata: { total_rows: table.length },
          table,
          config: [
            {
              id: "trace_name",
              name: "Trace Name",
              is_visible: true,
              group_by: null,
              choices: [null],
            },
          ],
        },
      },
    });

    expect(
      validateContractedResponse(
        tracesOfSessionResponse([
          {
            trace_id: "a2f1c9d0-0000-4000-8000-000000000001",
            trace_name: "checkout-flow",
            latency: 1.42,
            is_error: false,
            cost: null,
            "llm.model": ["gpt-4o", "gpt-4o-mini"],
            user_context: { plan: "pro", region: "us" },
          },
        ]),
      ),
    ).toMatchObject({ ok: true });

    // A malformed cell (undefined is not valid JSON) must fail — the old
    // z.any() mapping silently accepted it.
    expect(
      validateContractedResponse(
        tracesOfSessionResponse([{ trace_id: undefined }]),
      ),
    ).toMatchObject({ ok: false });
  });
});
