import { describe, expect, it } from "vitest";
import {
  candidatePending,
  failurePathsKnown,
  fixVerdict,
  fullRunVerdict,
  hasPathData,
  pathFailMove,
  ranAsIs,
  successMove,
  triesText,
} from "../fixVerdict";
import { reopenDraft } from "../useIterateDock";

const ids = (n, p = "r") => Array.from({ length: n }, (_, i) => `${p}${i}`);

describe("fixVerdict", () => {
  it("names the target failure first (r2 P2a), then the other failures", () => {
    const fixed = ids(6, "p");
    const still = ids(7, "o");
    const v = fixVerdict(
      { fixed, stillFailing: still, newFailures: [] },
      { target: fixed, fixedText: 'order id keeps "A-"' },
    );
    expect(v.text).toBe(
      'Target failure fixed on 6 of 6 rows (order id keeps "A-") · 7 other failures unchanged · 0 passing rows broke',
    );
    // Green: the target is fixed and nothing broke (other failures stay).
    expect(v.tone).toBe("good");
    const part = fixVerdict(
      {
        fixed: ids(4, "p"),
        stillFailing: ids(2, "p").map((x) => `${x}x`),
        newFailures: [],
      },
      { target: [...ids(4, "p"), "p0x", "p1x"] },
    );
    expect(part.text).toBe(
      "Target failure fixed on 4 of 6 rows · 2 still fail · 0 passing rows broke",
    );
    expect(part.tone).toBe("mixed");
  });
  it("without a target: fixed / still fail / broke, broken rows named", () => {
    expect(
      fixVerdict({ fixed: ids(2), stillFailing: ids(11), newFailures: [] })
        .text,
    ).toBe("Fixed 2 of 13 failing rows · 11 still fail · 0 passing rows broke");
    const broke = fixVerdict(
      { fixed: ids(7), stillFailing: ids(6), newFailures: ["T13"] },
      { labelOf: (id) => id },
    );
    expect(broke.text).toBe(
      "Fixed 7 of 13 failing rows · 6 still fail · 1 passing row broke (T13)",
    );
    // Red only when something broke.
    expect(broke.tone).toBe("bad");
    expect(broke.brokeRows).toEqual(["T13"]);
    const fixed = fixVerdict({
      fixed: ids(13),
      stillFailing: [],
      newFailures: [],
    });
    expect(fixed.word).toBe("Fixed");
    expect(fixed.tone).toBe("good");
    expect(fixed.text).toBe(
      "Fixed: all 13 rows that failed now pass · 0 passing rows broke",
    );
  });
  it("never `Nothing to fix`", () => {
    const v = fixVerdict({
      fixed: [],
      stillFailing: [],
      newFailures: [],
      rerun: ids(3),
    });
    expect(v.text).toBe("All 3 rows still pass · 0 passing rows broke");
    expect(v.text).not.toMatch(/Nothing to fix/);
  });
});

describe("fullRunVerdict (r2 P2b)", () => {
  it("Full run of v3.1 on 30 rows: better · worse (named, noise) · same", () => {
    const v = fullRunVerdict(
      { better: 6, worse: 1, same: 23, worseIds: ["r13"], notRun: 0 },
      {
        name: "v3.1",
        noise: "within noise",
        labelOf: () => "T13",
        movesOf: () => "3/3 → 2/3",
      },
    );
    expect(v.text).toBe(
      "Full run of v3.1 on 30 rows: 6 better · 1 worse (T13: 3/3 → 2/3, within noise) · 23 same",
    );
    expect(v.tone).toBe("mixed");
    expect(v.worseRows).toEqual(["r13"]);
    expect(
      fullRunVerdict(
        { better: 2, worse: 0, same: 28, worseIds: [] },
        { name: "v3.1", noise: "within noise" },
      ).tone,
    ).toBe("good");
  });
  it("triesText reads a matrix cell", () => {
    expect(triesText({ trials: [1, 1, 0] })).toBe("2/3");
    expect(triesText(null)).toBe("—");
  });
});

describe("successMove", () => {
  it("prints the compareRuns rates and its deltaPts", () => {
    expect(
      successMove({
        baseline: { rate: 59 / 90 },
        candidate: { rate: 77 / 90 },
        deltaPts: 20,
      }),
    ).toBe("65.6% → 85.6% (+20.0 pts)");
    expect(successMove(null)).toBeNull();
  });
});

describe("pathFailMove", () => {
  const p = {
    id: "a → b",
    steps: [{ name: "a", repeat: 1 }],
    failed: 18,
    attempts: 18,
  };
  it("the fixed path's failing tries before → after (0 when gone)", () => {
    expect(pathFailMove({ paths: [p] }, { paths: [] }, "a → b")).toEqual({
      path: p,
      from: 18,
      to: 0,
    });
    expect(
      pathFailMove({ paths: [p] }, { paths: [{ ...p, failed: 11 }] }, null).to,
    ).toBe(11);
  });
});

describe("reopenDraft (P3)", () => {
  const parent = {
    run_id: "B",
    config: {
      kind: "agent",
      agent: {
        version: "v3",
        nodes: [{ id: "classify_intent", type: "llm", prompt: "Digits only." }],
      },
    },
  };
  it("keeps the edits and what last ran; takes the new door's scope", () => {
    const prev = {
      prompts: { classify_intent: "Keep the prefix." },
      saveName: "v3.2",
      ran: { edits: "x", runId: "G", label: "#2G", name: "v3.1" },
      scope: "failed",
      reason: "Fixing: …",
    };
    const d = reopenDraft(parent, { runId: "B", scope: "all" }, prev);
    expect(d.prompts).toEqual({ classify_intent: "Keep the prefix." });
    expect(d.saveName).toBe("v3.2");
    expect(d.ran.label).toBe("#2G");
    expect(d.scope).toBe("all");
    expect(d.reason).toBeNull();
  });
});

describe("options-truth L3 G10 banner:verdict", () => {
  it("hasPathData: a run with no traces has no failing path to name", () => {
    expect(hasPathData({ paths: [] })).toBe(false);
    expect(hasPathData(null)).toBe(false);
    expect(hasPathData({ paths: [{ id: "a", failed: 2 }] })).toBe(true);
  });

  it("ranAsIs walks the chain back to the base", () => {
    const runs = [
      { run_id: "B", changes: undefined },
      { run_id: "C", parent_run_id: "B", changes: [] },
      { run_id: "D", parent_run_id: "C", changes: [] },
      { run_id: "F", parent_run_id: "B", changes: [{ kind: "prompt" }] },
      { run_id: "G", parent_run_id: "F", changes: [] },
      { run_id: "S", parent_run_id: "B" },
    ];
    expect(ranAsIs(runs, "C", "B")).toBe(true);
    expect(ranAsIs(runs, "D", "B")).toBe(true);
    // A guard run (no change itself) carries its parent's fix.
    expect(ranAsIs(runs, "G", "B")).toBe(false);
    expect(ranAsIs(runs, "F", "B")).toBe(false);
    // Seeded history without a `changes` field is not `as is`.
    expect(ranAsIs(runs, "S", "B")).toBe(false);
    expect(ranAsIs(runs, "B", "B")).toBe(false);
    expect(ranAsIs(runs, "X", "B")).toBe(false);
  });

  it("a rerun with nothing changed never says Fixed", () => {
    const v = fixVerdict(
      {
        fixed: ["r1", "r2"],
        stillFailing: ["r3"],
        newFailures: ["r9"],
        rerun: ["r1", "r2", "r3", "r9"],
      },
      { noChange: true, from: "#2B", labelOf: (id) => id.toUpperCase() },
    );
    expect(v.word).toBe("Rerun as is");
    expect(v.fixed).toBe(0);
    expect(v.text).toBe(
      "Nothing changed from #2B · 2 of 3 failing rows passed this time · 1 still fail · 1 passing row failed this time (R9)",
    );
    expect(v.text).not.toMatch(/Fixed/);
  });
});

describe("candidatePending (options-truth L3 G10)", () => {
  it("is true while a candidate try in the rerun is unscored", () => {
    const pending = {
      rows: [
        { id: "r1", n: 1, cells: { b: [0, 0], c: [1, null] } },
        { id: "r2", n: 2, cells: { b: [1, 1], c: [1, 1] } },
      ],
    };
    expect(candidatePending(pending, "b", "c")).toBe(true);
    const done = {
      rows: [{ id: "r1", n: 1, cells: { b: [0, 0], c: [1, 0] } }],
    };
    expect(candidatePending(done, "b", "c")).toBe(false);
    expect(candidatePending(null, "b", "c")).toBe(false);
  });
});

describe("failurePathsKnown (options-truth L3 G10)", () => {
  const withFails = {
    paths: [
      { id: "a", failed: 2 },
      { id: "b", failed: 0 },
    ],
  };
  const noFails = { paths: [{ id: "a", failed: 0 }] };
  it("needs failed tries on the paths when rows still fail", () => {
    expect(failurePathsKnown(withFails, 7)).toBe(true);
    expect(failurePathsKnown(noFails, 7)).toBe(false);
  });
  it("backs a move to 0 when nothing fails any more", () => {
    expect(failurePathsKnown(noFails, 0)).toBe(true);
  });
  it("is false without paths", () => {
    expect(failurePathsKnown({ paths: [] }, 0)).toBe(false);
    expect(failurePathsKnown(null, 3)).toBe(false);
  });
});
