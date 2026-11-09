import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
const { seeWhyPair } = await import("../RootCauseCard");

describe("IT-R5 G6 P2 (R4): See why opens the pattern row with a run that passes it", () => {
  const data = {
    baseline_run_id: "B",
    candidate_run_id: "D",
    pattern: { run_id: "B", rows: ["t1", "t2", "t11"] },
    runs: [
      { run_id: "A", created_at: "2026-10-01T00:00:00Z" },
      { run_id: "B", created_at: "2026-10-02T00:00:00Z" },
      { run_id: "D", created_at: "2026-10-03T00:00:00Z" },
      { run_id: "E", created_at: "2026-10-04T00:00:00Z" },
    ],
  };
  const cause = { run_id: "B", baseline_run_id: "A", rows: [{ row_id: "t1" }] };
  const runIds = ["A", "B", "D", "E"];
  it("pattern run first, the page candidate second (never #2B vs the first other run)", () => {
    expect(seeWhyPair({ cause, data, runIds })).toEqual(["B", "D"]);
  });
  it("a valid cand= wins", () => {
    expect(seeWhyPair({ cause, data, runIds, cand: "E" })).toEqual(["B", "E"]);
  });
  it("no candidate: the newest passing run", () => {
    expect(
      seeWhyPair({ cause, data: { ...data, candidate_run_id: null }, runIds }),
    ).toEqual(["B", "E"]);
  });
  it("a regression cause pairs the run with what it is measured against", () => {
    expect(
      seeWhyPair({
        cause: { run_id: "D", baseline_run_id: "B", rows: [{ row_id: "t25" }] },
        data,
        runIds,
      }),
    ).toEqual(["D", "B"]);
  });
});
