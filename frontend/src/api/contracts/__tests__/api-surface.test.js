import { describe, expect, it } from "vitest";

import {
  API_SURFACE_CONTRACT,
  API_SURFACE_PATHS,
} from "../api-surface.generated";
import {
  apiPath,
  getContractedApiMethods,
  isContractedApiPath,
} from "../api-surface";

describe("api surface contract", () => {
  it("generates annotation endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.swaggerVersion).toBe("2.0");
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/model-hub/annotation-tasks/",
    );
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
    );
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
    );
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/model-hub/scores/bulk/",
    );
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/model-hub/dataset/{dataset_id}/annotation-summary/",
    );
    expect(API_SURFACE_CONTRACT.groups.annotation).toHaveProperty(
      "/tracer/project-version/add_annotations/",
    );
  });

  it("generates filter and observe endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/trace/list_traces/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/observation-span/list_spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/trace-session/list_sessions/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/trace/list_voice_calls/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty("/tracer/users/");
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/observation-span/root-spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/observation-span/get_evaluation_details/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
      "/tracer/trace/{id}/tags/",
    );
    expect(API_SURFACE_CONTRACT.groups.filter).toHaveProperty(
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
    expect(isContractedApiPath("/not-in-openapi/")).toBe(false);
    expect(() => apiPath("/not-in-openapi/")).toThrow(
      "API path is not in generated contract",
    );
    expect(() => apiPath("/tracer/trace/{id}/tags/")).toThrow(
      'Missing API path param "id"',
    );
  });

  it("does not let annotation/filter groups accidentally go empty", () => {
    expect(Object.keys(API_SURFACE_PATHS).length).toBeGreaterThan(40);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.annotation).length,
    ).toBeGreaterThan(30);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.filter).length,
    ).toBeGreaterThan(20);
  });
});
