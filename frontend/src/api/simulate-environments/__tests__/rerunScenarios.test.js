import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { listMatchingCalls, rerunCallEvals, uniqueScenarioKeys } = await import(
  "../rerunScenarios"
);

describe("uniqueScenarioKeys", () => {
  it("reduces calls to their scenarios, once each, skipping calls without one", () => {
    expect(
      uniqueScenarioKeys([
        { sourceScenarioKey: "refund" },
        { sourceScenarioKey: "refund" },
        { sourceScenarioKey: "escalate" },
        { sourceScenarioKey: null },
      ]),
    ).toEqual(["refund", "escalate"]);
  });
});

describe("listMatchingCalls", () => {
  beforeEach(() => axios.get.mockReset());

  it("walks every page of the filtered calls, flat, and skips the unticked ones", async () => {
    axios.get
      .mockResolvedValueOnce({
        data: {
          total_pages: 2,
          results: [
            { id: "c1", source_scenario_key: "refund" },
            { id: "c2", source_scenario_key: "refund" },
            { id: "c3", source_scenario_key: "escalate" },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          total_pages: 2,
          results: [
            { id: "c4", source_scenario_key: "timeout" },
            { id: "c5", source_scenario_key: null },
          ],
        },
      });

    const result = await listMatchingCalls("ex1", { status: ["failed"] }, [
      "c3",
    ]);

    expect(result).toEqual({
      callIds: ["c1", "c2", "c4"],
      scenarioKeys: ["refund", "timeout"],
    });
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.get).toHaveBeenNthCalledWith(
      1,
      endpoints.runResultsV3.calls("ex1"),
      {
        params: {
          page: 1,
          page_size: 500,
          search: "",
          filters: JSON.stringify({ status: ["failed"] }),
        },
      },
    );
    expect(axios.get.mock.calls[1][1].params.page).toBe(2);
  });
});

describe("rerunCallEvals", () => {
  beforeEach(() => axios.post.mockReset());

  it("asks for an eval-only re-run of exactly these calls", async () => {
    axios.post.mockResolvedValue({ data: { success_count: 2 } });

    const data = await rerunCallEvals("ex1", ["c1", "c2"]);

    expect(data).toEqual({ success_count: 2 });
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.testExecutions.rerunExecution("ex1"),
      {
        select_all: false,
        rerun_type: "eval_only",
        call_execution_ids: ["c1", "c2"],
      },
    );
  });
});
