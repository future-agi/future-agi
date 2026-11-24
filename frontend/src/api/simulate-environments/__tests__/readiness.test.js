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

  it("sends the trials with the whole suite's GET and a named selection's POST", async () => {
    await getReadiness("env-1", { agent: "a2" }, null, 3);
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: { agent_version_id: "a2", trials: 3 },
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
      { keys: ["k1", "k2"], row: "tool_routing", trials: 2 },
    );
    expect(axios.get).toHaveBeenCalledWith(`${PATH}cases/`, {
      params: { trials: 2, keys: ["k1", "k2"], row: "tool_routing" },
      paramsSerializer: { indexes: null },
    });
  });
});
