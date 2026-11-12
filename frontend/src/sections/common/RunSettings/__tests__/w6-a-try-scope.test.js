// W6 A1: the Try carries the run's row scope.
import { describe, expect, it } from "vitest";
import { withRunScope } from "../tryScope";

describe("W6 A1 · withRunScope", () => {
  const config = { kind: "prompt", tools: [{ name: "a", mode: "recorded" }] };

  it("adds the Data step's splits and rows", () => {
    expect(
      withRunScope(config, { splitIds: ["s1"], rowIds: ["r1", "r2"] }),
    ).toEqual({
      ...config,
      run_scope: { split_ids: ["s1"], row_ids: ["r1", "r2"] },
    });
  });

  it("keeps the config as it is when the run covers every row", () => {
    expect(withRunScope(config, { splitIds: [], rowIds: [] })).toBe(config);
    expect(withRunScope(config, {})).toBe(config);
    expect(withRunScope(config)).toBe(config);
  });

  it("drops blanks and never invents a scope", () => {
    expect(
      withRunScope(config, { splitIds: [null, "", "s2"], rowIds: undefined }),
    ).toEqual({ ...config, run_scope: { split_ids: ["s2"], row_ids: [] } });
  });

  it("a card that cannot be tried stays null", () => {
    expect(withRunScope(null, { splitIds: ["s1"] })).toBeNull();
  });
});
