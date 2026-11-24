import { describe, expect, it } from "vitest";
import { isAgentFix, readinessView } from "../readinessView";

const report = (rows, validation = { state: "ready" }) => ({
  validation,
  answer: { kind: "blocked" },
  pairing: { evidence: "pair_certificate" },
  areas: [{ id: "tool_binding", rows: rows.map((row) => row.id) }],
  rows,
});

describe("readinessView", () => {
  it("reads an area as its worst counted row, leaving out rows nothing measures or touches", () => {
    const view = readinessView(
      report([
        { id: "browser", status: "untested", measured: false },
        {
          id: "tool_routing",
          status: "blocked",
          measured: true,
          affected: { count: 3 },
        },
      ]),
    );
    expect(view.open).toEqual([
      expect.objectContaining({
        area: "tool_binding",
        status: "blocked",
        rowId: "tool_routing",
        affected: 3,
      }),
    ]);
    expect(view.holdsRun).toBe(false);
  });

  it("holds the run only while the pair is checked or after its check failed", () => {
    const held = (state) => readinessView(report([], { state })).holdsRun;
    expect(held("validating")).toBe(true);
    expect(held("failed")).toBe(true);
    expect(held("check_unavailable")).toBe(false);
    expect(held("ready")).toBe(false);
  });

  it("sends fixes made on the agent to its versions", () => {
    expect(isAgentFix({ category: "agent_version" })).toBe(true);
    expect(isAgentFix({ type: "preflight" })).toBe(true);
    expect(isAgentFix({ type: "recheck", category: "recheck" })).toBe(false);
  });

  it("counts every scenario nothing can grade, past the ones the report lists", () => {
    const { grading } = readinessView({
      selection: {
        requested: 1103,
        grading: { gradable: 3, not_gradable: [{ key: "a" }], truncated: true },
      },
    });
    expect(grading).toEqual({ gradable: 3, notGradable: 1100 });
  });
});
