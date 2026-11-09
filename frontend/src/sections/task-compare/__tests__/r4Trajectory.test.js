// R4 lane T in Task compare: Failed by / worse rail sets, the failed card's
// First wrong step line, R4 depths surviving the one-view param mapping,
// and the shared Timeline axis.
import { describe, expect, it } from "vitest";
import { railFilterOf, railSets } from "../workspaceModel";
import { firstWrongLineOf } from "../whyModel";
import { depthOf, legacyParamPatch } from "../oneViewParams";
import { sharedAxis } from "../LaneTimelines";

const matrix = {
  rows: [
    {
      row_id: "r1",
      n: 1,
      cells: { A: { trials: [1, 1, 1] }, B: { trials: [0, 0, 0] } },
    },
    {
      row_id: "r2",
      n: 2,
      cells: { A: { trials: [1, 1, 1] }, B: { trials: [1, 1, 1] } },
    },
    {
      row_id: "r3",
      n: 3,
      cells: { A: { trials: [1, 0, 1] }, B: { trials: [1, "error", 1] } },
    },
    {
      row_id: "r4",
      n: 4,
      cells: { A: { trials: [0, 0, 0] }, B: { trials: [1, 1, 1] } },
    },
  ],
};

describe("rail: Failed by and worse (hc-7)", () => {
  it("counts tasks each lane failed, any run, and where the focus is worse", () => {
    const s = railSets(matrix, {
      runIds: ["A", "B"],
      baselineId: "A",
      focusId: "B",
    });
    expect(s["failed:B"]).toEqual(["r1", "r3"]);
    expect(s["failed:A"]).toEqual(["r3", "r4"]);
    expect(s["failed:any"]).toEqual(["r1", "r3", "r4"]);
    // B worse than A (fewer passes) or B failed a try; never r2 / r4
    expect(s.worse).toEqual(["r1", "r3"]);
  });
  it("names the filter after the run", () => {
    const labelOf = (id) => (id === "B" ? "#2B" : "#3A");
    expect(railFilterOf("failed:B", { labelOf }).long).toBe("Tasks #2B failed");
    expect(
      railFilterOf("worse", { labelOf, focusId: "B", baselineId: "A" }).long,
    ).toBe("Tasks where #2B is worse than #3A");
    expect(railFilterOf("nonsense").value).toBe("differ");
  });
});

describe("failed card line (fr-5)", () => {
  it("says the first wrong step from G0's milestones and the order id read", () => {
    const lane = {
      id: "B",
      label: "#2B",
      steps: [
        {
          n: 1,
          kind: "think",
          name: "classify_intent",
          isMilestone: true,
          output: '{"order_ref":"7781"}',
        },
        {
          n: 2,
          kind: "tool",
          name: "lookup_order",
          isMilestone: true,
          input: '{"order_id":"7781"}',
          error: "not found",
        },
      ],
      checks: [{ key: "order_found", passed: false, cause_step_n: 1 }],
    };
    const line = firstWrongLineOf(lane);
    expect(line === null || /^First wrong step · step \d+/.test(line)).toBe(
      true,
    );
    expect(firstWrongLineOf({ id: "x", steps: [], checks: [] })).toBeNull();
  });
});

describe("R4 depths survive the one-view mapping", () => {
  it("keeps depth=graph|call, still maps the R3 depths", () => {
    expect(legacyParamPatch("?depth=graph&gsel=lookup_order")).toBeNull();
    expect(legacyParamPatch("?depth=call")).toBeNull();
    expect(legacyParamPatch("?depth=raw&rtab=waterfall").patch).toMatchObject({
      depth: null,
      inspect: "timeline",
    });
    expect(depthOf("graph")).toBe("graph");
    expect(depthOf("story")).toBeNull();
  });
});

describe("Timeline shared axis", () => {
  it("is the longest lane's duration", () => {
    const tree = (ms) => [
      { observation_span: { id: "s", latency_ms: ms }, children: [] },
    ];
    expect(
      sharedAxis([{ tree: tree(9800) }, { tree: tree(81000) }, { tree: null }]),
    ).toEqual({
      duration: 81000,
    });
  });
});
