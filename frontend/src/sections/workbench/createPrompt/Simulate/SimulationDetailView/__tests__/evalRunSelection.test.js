import { describe, expect, it } from "vitest";
import { resolveEvalsToRun } from "../evalRunSelection";

const evals = [{ id: "a" }, { id: "b" }, { id: "c" }];

describe("resolveEvalsToRun", () => {
  it("runs every configured eval when nothing is checked", () => {
    expect(resolveEvalsToRun(evals, new Set())).toEqual(evals);
    expect(resolveEvalsToRun(evals, null)).toEqual(evals);
    expect(resolveEvalsToRun(evals)).toEqual(evals);
  });

  it("runs only the checked subset", () => {
    expect(resolveEvalsToRun(evals, new Set(["a", "c"]))).toEqual([
      { id: "a" },
      { id: "c" },
    ]);
  });

  it("falls back to every configured eval when the selection is stale", () => {
    expect(resolveEvalsToRun(evals, new Set(["gone"]))).toEqual(evals);
  });

  it("returns an empty list when there are no configured evals", () => {
    expect(resolveEvalsToRun([], new Set(["a"]))).toEqual([]);
  });
});
