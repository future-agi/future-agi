import { describe, expect, it } from "vitest";

import {
  API_SURFACE_CONTRACT,
  API_SURFACE_PATHS,
} from "../api-surface.generated";
import {
  apiPath,
  getContractedApiMethods,
  isContractedApiPath,
  isApiContractExceptionPath,
  uncontractedApiPath,
} from "../api-surface";
import { API_CONTRACT_EXCEPTIONS } from "../api-contract-exceptions";

describe("api surface contract", () => {
  it("generates annotation and model-hub endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.swaggerVersion).toBe("2.0");
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-tasks/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/scores/bulk/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/dataset/{dataset_id}/annotation-summary/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/project-version/add_annotations/",
    );
  });

  it("generates tracer, filter, and observe endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/list_traces/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/list_spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace-session/list_sessions/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/list_voice_calls/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty("/tracer/users/");
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/root-spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/get_evaluation_details/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/{id}/tags/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/",
    );
  });

  it("builds only registered paths and preserves method metadata", () => {
    expect(
      apiPath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        { queue_id: "queue one", id: "item/1" },
      ),
    ).toBe(
      "/model-hub/annotation-queues/queue%20one/items/item%2F1/annotate-detail/",
    );
    expect(
      getContractedApiMethods("/model-hub/annotation-queues/{id}/"),
    ).toEqual(["delete", "get", "patch", "put"]);
    expect(getContractedApiMethods("/model-hub/annotation-tasks/")).toEqual([
      "get",
    ]);
    expect(getContractedApiMethods("/tracer/trace/{id}/tags/")).toEqual([
      "patch",
    ]);
    expect(getContractedApiMethods("/usage/v2/add-addon/")).toEqual([
      "delete",
      "post",
      "put",
    ]);
    expect(getContractedApiMethods("/usage/v2/usage-overview/")).toEqual([
      "get",
    ]);
    expect(
      apiPath("/usage/v2/invoices/{invoice_id}/", { invoice_id: "invoice/1" }),
    ).toBe("/usage/v2/invoices/invoice%2F1/");
    expect(getContractedApiMethods("/usage/ee/licenses/")).toEqual([
      "get",
      "post",
    ]);
    expect(getContractedApiMethods("/falcon-ai/conversations/")).toEqual([
      "get",
      "post",
    ]);
    expect(isContractedApiPath("/not-in-openapi/")).toBe(false);
    expect(() => apiPath("/not-in-openapi/")).toThrow(
      "API path is not in generated contract",
    );
    expect(() => apiPath("/tracer/trace/{id}/tags/")).toThrow(
      'Missing API path param "id"',
    );
  });

  it("keeps the endpoint registry fully backed by generated contracts", () => {
    expect(API_CONTRACT_EXCEPTIONS).toEqual({});
    expect(isApiContractExceptionPath("/usage/v2/usage-overview/")).toBe(false);
    expect(() => uncontractedApiPath("/model-hub/legacy/")).toThrow(
      "API contract exception path is not registered",
    );
  });

  it("does not let generated Management API coverage accidentally shrink", () => {
    expect(API_SURFACE_CONTRACT.endpointCount).toBeGreaterThan(960);
    expect(Object.keys(API_SURFACE_PATHS).length).toBe(
      API_SURFACE_CONTRACT.endpointCount,
    );
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups["model-hub"]).length,
    ).toBeGreaterThan(360);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.tracer).length,
    ).toBeGreaterThan(153);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.accounts).length,
    ).toBeGreaterThan(75);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.simulate).length,
    ).toBeGreaterThan(100);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.agentcc).length,
    ).toBeGreaterThan(100);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.usage).length,
    ).toBeGreaterThan(55);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups["falcon-ai"]).length,
    ).toBeGreaterThan(15);
  });
});
