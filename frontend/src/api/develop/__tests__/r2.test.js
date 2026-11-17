import { describe, expect, it } from "vitest";
import { configKeyOf, lineageOf, toIterateBody } from "../r2";

describe("r2 helpers", () => {
  it("toIterateBody maps camelCase vars to the I1 body", () => {
    expect(
      toIterateBody({
        parentRunId: "R2B",
        scope: "rows",
        rowIds: ["r1", "r2"],
        change: "tighten_prompt",
        cause: "c1",
        trials: 3,
      }),
    ).toEqual({
      parent_run_id: "R2B",
      scope: "rows",
      row_ids: ["r1", "r2"],
      change: "tighten_prompt",
      cause: "c1",
      trials: 3,
    });
    expect(toIterateBody({ parentRunId: "R", rowIds: [] })).toEqual({
      parent_run_id: "R",
    });
  });

  it("configKeyOf: API key, else kind | target | model", () => {
    expect(configKeyOf({ config_key: "k1" })).toBe("k1");
    expect(
      configKeyOf({
        config: {
          kind: "harness",
          harness: { name: "Claude Code", model: "claude-sonnet-4-5" },
        },
      }),
    ).toBe("harness|Claude Code|claude-sonnet-4-5");
    expect(
      configKeyOf({ target_type: "agent", target: { graph_id: "G" } }),
    ).toBe("agent|G|");
    expect(configKeyOf(null)).toBeNull();
  });

  it("lineageOf: root first, iterations in order", () => {
    const runs = [
      { run_id: "2B" },
      {
        run_id: "2G",
        parent_run_id: "2B",
        iteration: { root_run_id: "2B", n: 2 },
      },
      {
        run_id: "2F",
        parent_run_id: "2B",
        iteration: { root_run_id: "2B", n: 1 },
      },
      { run_id: "2A" },
    ];
    const l = lineageOf(runs, "2G");
    expect(l.root.run_id).toBe("2B");
    expect(l.chain.map((r) => r.run_id)).toEqual(["2B", "2F", "2G"]);
    expect(lineageOf(runs, "nope")).toEqual({ root: null, chain: [] });
  });
});
