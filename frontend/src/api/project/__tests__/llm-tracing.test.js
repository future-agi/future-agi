import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { fetchSpanAttributeKeys } from "../llm-tracing";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
      getEvalAttributeList: () =>
        "/tracer/observation-span/get_eval_attributes_list/",
    },
  },
}));

describe("fetchSpanAttributeKeys", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reads the project's span attributes", async () => {
    const response = { data: { result: [{ key: "llm.model_name" }] } };
    axios.get.mockResolvedValueOnce(response);

    await expect(fetchSpanAttributeKeys("project-1")).resolves.toBe(response);
    expect(axios.get).toHaveBeenCalledWith("/api/traces/span-attribute-keys/", {
      params: { project_id: "project-1" },
    });
  });

  it("falls back to eval attributes when the span lookup fails", async () => {
    const response = { data: { result: ["legacy.attribute"] } };
    axios.get
      .mockRejectedValueOnce(new Error("ClickHouse unavailable"))
      .mockResolvedValueOnce(response);

    await expect(fetchSpanAttributeKeys("project-1")).resolves.toBe(response);
    expect(axios.get).toHaveBeenNthCalledWith(
      2,
      "/tracer/observation-span/get_eval_attributes_list/",
      {
        params: {
          filters: JSON.stringify({ project_id: "project-1" }),
        },
      },
    );
  });
});
