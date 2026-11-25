import { describe, expect, it } from "vitest";
import {
  checkProblemsOf,
  flakyRerunLabel,
  initialPickOf,
  launchUrlOf,
  needsCheckOf,
  pairIdOf,
  scopeRowsOf,
  trialsHelpOf,
} from "../runPlanScope";

describe("runPlanScope", () => {
  const plan = {
    run: [{ id: "a", title: "A" }, { id: "b", title: "B" }, { id: "c" }],
    skipped: [{ id: "d", title: "D", skipReason: "Quarantined" }],
  };
  it("lists runnable rows then blocked ones", () => {
    const rows = scopeRowsOf(plan, { a: "flaky" });
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
    expect(rows[0].status).toBe("flaky");
    expect(rows[3]).toMatchObject({ ok: false, why: "Quarantined" });
  });
  it("a re-run keeps every skipped scenario runnable, tagged Not proved", () => {
    const rows = scopeRowsOf(plan, {}, { rerun: true });
    expect(rows.every((r) => r.ok)).toBe(true);
    expect(rows[3]).toMatchObject({ id: "d", notProved: true });
    expect(rows[0].notProved).toBeUndefined();
  });
  it("titles match the run page's", () => {
    const rows = scopeRowsOf({
      run: [{ id: "a", title: "Routine task using check_return_eligibility" }],
      skipped: [],
    });
    expect(rows[0].title).toBe("Routine check return eligibility request");
  });
  it("pre-fills the asked-for ids that can run, else all", () => {
    const rows = scopeRowsOf(plan);
    expect(initialPickOf(rows, ["b", "d", "zz"])).toEqual(["b"]);
    expect(initialPickOf(rows, ["d"])).toEqual(["a", "b", "c"]);
    expect(initialPickOf(rows, null)).toEqual(["a", "b", "c"]);
  });
  it("needs a check when the pair differs and has not been checked", () => {
    const base = pairIdOf("k", "v1", "v2");
    const other = pairIdOf("k", "v2", "v2");
    expect(needsCheckOf({ hasCheck: true, pairId: base, baseId: base })).toBe(
      false,
    );
    expect(needsCheckOf({ hasCheck: true, pairId: other, baseId: base })).toBe(
      true,
    );
    expect(
      needsCheckOf({
        hasCheck: true,
        pairId: other,
        baseId: base,
        checked: { [other]: true },
      }),
    ).toBe(false);
    expect(needsCheckOf({ hasCheck: false, pairId: other, baseId: base })).toBe(
      false,
    );
  });
  it("names only blocked areas as problems", () => {
    expect(
      checkProblemsOf([
        { id: "x", label: "X", status: "ready" },
        { id: "y", label: "Y", status: "blocked", detail: "fix it" },
      ]),
    ).toEqual([{ id: "y", label: "Y", detail: "fix it" }]);
  });
  it("trials copy: nothing at 3 or more, one line below", () => {
    expect(trialsHelpOf(3)).toBe("");
    expect(trialsHelpOf(5)).toBe("");
    expect(trialsHelpOf(2)).toBe("Pick 3 or more to spot flaky scenarios");
    expect(trialsHelpOf(1)).toBe("Pick 3 or more to spot flaky scenarios");
  });
  it("flaky button carries the verdict", () => {
    expect(flakyRerunLabel(14, 5)).toBe("Re-run 14 flaky · 5 trials");
  });
  it("builds the live-run address", () => {
    const url = launchUrlOf("e1", "r1", ["a", "b"], 5, {
      agent: "v2",
      envVersion: "v3",
    });
    expect(url).toContain("only=a%2Cb");
    expect(url).toContain("trials=5");
    expect(url).toContain("agent=v2");
    expect(url).toContain("env=v3");
  });
});
