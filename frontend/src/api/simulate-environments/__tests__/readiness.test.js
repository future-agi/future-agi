import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { getReadiness, getReadinessCases } = await import("../readiness");

const PATH = "/simulate/api/harness-environments/env-1/capabilities/";

describe("readiness requests", () => {
  beforeEach(() => {
    axios.get.mockReset().mockResolvedValue({ data: { items: [] } });
    axios.post.mockReset().mockResolvedValue({ data: {} });
  });

  it("reads the whole suite with a GET and a named selection with a POST that carries the trials", async () => {
    await getReadiness("env-1", { agent: "a2" });
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: { agent_version_id: "a2" },
    });
    await getReadiness("env-1", { agent: "a2" }, ["k1"], 3);
    expect(axios.post).toHaveBeenCalledWith(PATH, {
      agent_version_id: "a2",
      scenario_ids: ["k1"],
      trials: 3,
    });
  });

  it("asks for the cases one row holds back, keys repeated", async () => {
    await getReadinessCases(
      "env-1",
      {},
      { keys: ["k1", "k2"], row: "tool_routing" },
    );
    expect(axios.get).toHaveBeenCalledWith(`${PATH}cases/`, {
      params: { keys: ["k1", "k2"], row: "tool_routing" },
      paramsSerializer: { indexes: null },
    });
  });
});
