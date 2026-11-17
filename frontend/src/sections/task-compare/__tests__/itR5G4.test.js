import { describe, expect, it } from "vitest";
import { firstWorseRow, openPairIds } from "../workspaceModel";
import { runInfoRows } from "../LaneHeader";

const cells = {
  a: { trials: [1, 1, 1] },
  b: { trials: [1, 1, 1] },
  c: { trials: [0, 0, 1] },
  d: { trials: [1, 1, 1] },
};

describe("IT-R5 G4 P4: task compare opens on the pair", () => {
  it("honours baseline + cand from the link", () => {
    expect(
      openPairIds({
        urlIds: ["a", "b", "c", "d"],
        baselineId: "b",
        candId: "d",
      }),
    ).toEqual(["b", "d"]);
  });

  it("without cand, opens the baseline and the most different run", () => {
    expect(
      openPairIds({ urlIds: ["a", "b", "c", "d"], baselineId: "b", cells }),
    ).toEqual(["b", "c"]);
  });

  it("when every run gives the same result, opens baseline + newest", () => {
    const same = { a: cells.a, b: cells.b, d: cells.d };
    expect(
      openPairIds({
        urlIds: ["a", "b", "d"],
        baselineId: "b",
        cells: same,
        order: ["a", "b", "d"],
      }),
    ).toEqual(["b", "d"]);
  });

  it("waits for the matrix row when there is no cand", () => {
    expect(openPairIds({ urlIds: ["a", "b", "c"], baselineId: "b" })).toBe(
      null,
    );
  });

  it("defaults to the first task where the candidate is worse", () => {
    const matrix = {
      rows: [
        { row_id: "r1", cells: { b: { trials: [1] }, c: { trials: [1] } } },
        { row_id: "r2", cells: { b: { trials: [1] }, c: { trials: [0] } } },
        { row_id: "r3", cells: { b: { trials: [1] }, c: { trials: [0] } } },
      ],
    };
    expect(firstWorseRow(matrix, ["r1", "r2", "r3"], "b", "c")).toBe("r2");
    expect(firstWorseRow(matrix, ["r1"], "b", "c")).toBe(null);
  });

  it("lists the model, framework, version, tools and MCP in run info", () => {
    const rows = runInfoRows(
      {
        models: [{ id: "claude-sonnet-4-5" }],
        harness: { name: "Claude Code", version: "2.1.3" },
        source: { name: "Claude Code" },
        target: null,
        tools: [{ name: "lookup_order" }, { name: "refund" }],
        mcp: [{ name: "acme-support", tools: 12 }],
      },
      null,
    );
    expect(rows).toEqual([
      ["Model", "claude-sonnet-4-5"],
      ["Framework", "Claude Code 2.1.3"],
      ["Tools", "lookup_order, refund"],
      ["MCP", "acme-support (12 tools)"],
    ]);
  });
});
