// K-3 (BL-06): one publish gate for the result banner and the dock.
import { describe, expect, it } from "vitest";
import {
  EDITORS_ONLY,
  carriedFromBaseOf,
  carriedText,
  coverageOf,
  gateRunIdOf,
  leftOutText,
  publishGateOf,
  quickTestText,
  waitingText,
} from "../publishGate";

const draft = { draft: true, version: "v3.1" };
const cand = { run_id: "h", label: "#2H", listed: true };
const base = {
  rerunCmp: { partial: null },
  carriedFromBase: [],
  draftVersion: draft,
  cand,
  baseLabel: "#2B",
  canEdit: true,
};

describe("publishGateOf", () => {
  it("waits for the tries still scoring: N − k, plural and singular", () => {
    const g = publishGateOf({
      ...base,
      rerunCmp: { partial: { scored: 44, total: 51 } },
    });
    expect(g).toMatchObject({ show: true, ready: false, state: "waiting" });
    expect(g.reason).toBe(
      "Waiting for 7 tries · Publish when the verdict is in",
    );
    expect(g.waiting).toBe(7);
    const one = publishGateOf({
      ...base,
      rerunCmp: { partial: { scored: 50, total: 51 } },
    });
    expect(one.reason).toBe(
      "Waiting for 1 try · Publish when the verdict is in",
    );
    expect(waitingText(0)).toMatch(/^Waiting for 0 tries/);
  });

  it("waits for the rows still carried from the base, naming the version", () => {
    const rows = Array.from({ length: 17 }, (_, i) => ({ row_id: `r${i}` }));
    const g = publishGateOf({ ...base, carriedFromBase: rows });
    expect(g).toMatchObject({ show: true, ready: false, state: "carried" });
    expect(g.reason).toBe(
      "17 rows still come from #2B · Run them on v3.1 to publish",
    );
    expect(carriedText(1, "#2B", "v3.1")).toBe(
      "1 row still comes from #2B · Run it on v3.1 to publish",
    );
    // a count works too
    expect(publishGateOf({ ...base, carriedFromBase: 3 }).carried).toBe(3);
  });

  it("tries still scoring win over carried rows (the verdict is not in)", () => {
    const g = publishGateOf({
      ...base,
      rerunCmp: { partial: { scored: 0, total: 39 } },
      carriedFromBase: [{ row_id: "a" }],
    });
    expect(g.state).toBe("waiting");
    expect(g.reason).toMatch(/^Waiting for 39 tries/);
  });

  it("a viewer is told only editors can publish", () => {
    const g = publishGateOf({ ...base, canEdit: false });
    expect(g).toMatchObject({
      show: true,
      ready: false,
      state: "editor",
      reason: EDITORS_ONLY,
    });
  });

  it("is not shown for a published version, the baseline or a quick test", () => {
    const published = publishGateOf({
      ...base,
      draftVersion: { draft: false, version: "v3.1" },
    });
    expect(published).toMatchObject({
      show: false,
      ready: false,
      reason: null,
      state: "published",
    });
    expect(publishGateOf({ ...base, draftVersion: null }).show).toBe(false);
    expect(
      publishGateOf({ ...base, cand: { ...cand, is_dataset_baseline: true } })
        .show,
    ).toBe(false);
    // review MF-2: the mock labels every quick test `Quick test`; the
    // reason never repeats the label, and says how much of the dataset ran.
    const quickRun = {
      run_id: "q",
      label: "Quick test",
      listed: false,
      source: { kind: "quick_test" },
    };
    const quick = publishGateOf({
      ...base,
      cand: quickRun,
      candLabel: "Quick test",
      notRun: 25,
      baseRows: 30,
    });
    expect(quick.show).toBe(false);
    expect(quick.ready).toBe(false);
    expect(quick.state).toBe("quick");
    expect(quick.reason).toBe(
      "A quick test ran 5 of 30 rows · Publish needs every row run on v3.1",
    );
    expect(quick.reason).not.toMatch(/Quick test was/);
    // a quick test that covered every row is still not a verdict
    expect(
      publishGateOf({ ...base, cand: quickRun, notRun: 0, baseRows: 8 }).reason,
    ).toBe(
      "A quick test is not a verdict · Publish needs a run of v3.1 on the leaderboard",
    );
    expect(quickTestText({ ran: null, total: null, version: null })).toBe(
      "A quick test is not a verdict · Publish needs a run of this version on the leaderboard",
    );
  });

  it("waits for the base rows the new run left out (review MF-1: Leave them out)", () => {
    const g = publishGateOf({
      ...base,
      candLabel: "#2H",
      notRun: Array.from({ length: 17 }, (_, i) => ({ row_id: `r${i}` })),
      baseRows: 30,
    });
    expect(g).toMatchObject({
      show: true,
      ready: false,
      state: "left_out",
      notRun: 17,
    });
    expect(g.reason).toBe(
      "17 rows were left out of #2H · Publish needs every row run on v3.1",
    );
    expect(leftOutText(1, "#2H", "v3.1")).toBe(
      "1 row was left out of #2H · Publish needs every row run on v3.1",
    );
    // tries still scoring come first; rows carried from the base next
    expect(
      publishGateOf({
        ...base,
        notRun: 17,
        rerunCmp: { partial: { scored: 3, total: 13 } },
      }).state,
    ).toBe("waiting");
    expect(
      publishGateOf({ ...base, notRun: 2, carriedFromBase: 3 }).state,
    ).toBe("carried");
  });

  it("is ready once every try is scored and no row is carried", () => {
    const g = publishGateOf(base);
    expect(g).toMatchObject({
      show: true,
      ready: true,
      reason: null,
      state: "ready",
    });
    // readiness does not depend on `show` (the dock reads ready + reason)
    const pinned = publishGateOf({
      ...base,
      cand: { ...cand, is_dataset_baseline: true },
    });
    expect(pinned.ready).toBe(true);
  });

  it("is not ready without the candidate run", () => {
    expect(publishGateOf({ ...base, cand: null })).toMatchObject({
      show: false,
      ready: false,
    });
  });
});

describe("coverageOf", () => {
  it("counts the base rows the new run has no cell on (R3 gives none)", () => {
    const matrix = {
      rows: [
        { row_id: "1", cells: { b: {}, h: {} } },
        { row_id: "2", cells: { b: {} } },
        { row_id: "3", cells: { b: {}, h: { carried_from: "#2B" } } },
        { row_id: "4", cells: { b: {} } },
        // a row only the new run covers is not the base's
        { row_id: "5", cells: { h: {} } },
      ],
    };
    const c = coverageOf(matrix, "b", "h");
    expect(c.baseRows).toBe(4);
    expect(c.notRun.map((r) => r.row_id)).toEqual(["2", "4"]);
    expect(coverageOf(null, "b", "h")).toEqual({ notRun: [], baseRows: 0 });
    expect(coverageOf(matrix, "b", null)).toEqual({ notRun: [], baseRows: 0 });
  });
});

describe("carriedFromBaseOf", () => {
  it("counts rows carried from the base only (a chain's rows count as run)", () => {
    const matrix = {
      rows: [
        { row_id: "1", cells: { i: { carried_from: "#2B" } } },
        { row_id: "2", cells: { i: { carried_from: "#2H" } } },
        { row_id: "3", cells: { i: {} } },
      ],
    };
    expect(carriedFromBaseOf(matrix, "i", "#2B").map((r) => r.row_id)).toEqual([
      "1",
    ]);
    expect(carriedFromBaseOf(null, "i", "#2B")).toEqual([]);
  });
});

describe("gateRunIdOf", () => {
  const runs = [
    { run_id: "b", parent_run_id: null, changes: null },
    { run_id: "h", parent_run_id: "b", changes: [{ kind: "agent" }] },
    {
      run_id: "i",
      parent_run_id: "h",
      changes: [],
      created_at: "2026-10-23T10:00:00Z",
    },
    {
      run_id: "j",
      parent_run_id: "i",
      changes: [],
      created_at: "2026-10-23T10:05:00Z",
    },
    {
      run_id: "q",
      parent_run_id: "h",
      changes: [],
      listed: false,
      created_at: "2026-10-23T11:00:00Z",
    },
    {
      run_id: "k",
      parent_run_id: "h",
      changes: [{ kind: "prompt" }],
      created_at: "2026-10-23T12:00:00Z",
    },
  ];
  it("follows the as-is reruns of the run that saved the version", () => {
    expect(gateRunIdOf(runs, "h")).toBe("j");
    expect(gateRunIdOf(runs, "i")).toBe("j");
  });
  it("a run with no as-is rerun is its own gate; none without a run", () => {
    expect(gateRunIdOf(runs, "k")).toBe("k");
    expect(gateRunIdOf(runs, null)).toBeNull();
  });
  it("never follows a changed run or a quick test", () => {
    expect(
      gateRunIdOf(
        runs.filter((r) => r.run_id !== "i"),
        "h",
      ),
    ).toBe("h");
  });
});
