import { describe, expect, it } from "vitest";
import { listDirection, pillRunOf, triageRunOf } from "../triageRun";
import { comparedChipOf } from "../pageCandidate";
import {
  fixStepHref,
  pinnedBaselineOf,
} from "src/sections/task-compare/fixStepHref";
import { iterateHref as realIterateHref } from "src/sections/common/Analysis/analysisLinks";

const row = {
  results: {
    B: { movement: {} },
    A: { movement: { "eval:task_success": "regressed" } },
    D: { movement: { "ops:latency": "regressed" } },
  },
};
const runIds = ["B", "A", "D"];

describe("triageRunOf (iterate r3 G3 P1)", () => {
  it("focuses the run that got worse on a Worse than list", () => {
    expect(
      triageRunOf({
        row,
        show: "regressed",
        baselineId: "B",
        runIds,
        pageCand: "D",
      }),
    ).toBe("A");
  });
  it("prefers the page candidate when it got worse here", () => {
    const r = {
      results: { ...row.results, D: { movement: { "eval:x": "regressed" } } },
    };
    expect(
      triageRunOf({
        row: r,
        show: "regressed",
        baselineId: "B",
        runIds,
        pageCand: "D",
      }),
    ).toBe("D");
  });
  it("a pill about one run wins; else the page candidate", () => {
    const pills = [
      { type: "movement", metric: "eval:x", dir: "regressed", run: "D" },
    ];
    expect(pillRunOf(pills, { baselineId: "B", runIds })).toBe("D");
    expect(listDirection({ pills })).toBe("regressed");
    expect(triageRunOf({ row, pills, baselineId: "B", runIds })).toBe("D");
    expect(
      triageRunOf({ row, show: "all", baselineId: "B", runIds, pageCand: "D" }),
    ).toBe("D");
  });
});

describe("fixStepHref (S2): the subject never flips, the page never re-bases", () => {
  const iterateHref = (path, { runId }) => `${path}?iterate=${runId}`;
  const back = "/dashboard/develop/ds/compare/tasks?runs=B,A&baseline=B&row=r1";
  it("fixing the baseline lane keeps it as the subject and the pinned baseline", () => {
    const href = fixStepHref({
      lane: { id: "B", experiment_id: "e2" },
      rowId: "r1",
      baselineId: "A",
      back,
      iterateHref,
      inPlace: false,
    });
    const sp = new URLSearchParams(href.split("?")[1]);
    expect(sp.get("cand")).toBe("B");
    expect(sp.get("baseline")).toBe("B");
    expect(sp.get("compared")).toBe("A");
  });
  it("fixing the other lane: baseline stays pinned, no compared chip", () => {
    const sp = new URLSearchParams(
      fixStepHref({
        lane: { id: "A", experiment_id: "e2" },
        rowId: "r1",
        baselineId: "B",
        back,
        iterateHref,
        inPlace: false,
      }).split("?")[1],
    );
    expect(sp.get("cand")).toBe("A");
    expect(sp.get("baseline")).toBe("B");
    expect(sp.get("compared")).toBeNull();
  });
  it("IT-R5 G3 P4: from side by side the panel opens on the same page", () => {
    const href = fixStepHref({
      lane: { id: "B", experiment_id: "e2" },
      rowId: "r1",
      node: "classify_intent",
      baselineId: "A",
      back,
      iterateHref: realIterateHref,
    });
    expect(href.startsWith("/dashboard/develop/ds/compare/tasks?")).toBe(true);
    const sp = new URLSearchParams(href.split("?")[1]);
    expect(sp.get("row")).toBe("r1");
    expect(sp.get("baseline")).toBe("B");
    expect(sp.get("iterate")).toBe("B");
    expect(sp.get("scope")).toBe("failed");
    expect(sp.get("node")).toBe("classify_intent");
  });
  it("reads the pinned baseline from back and shows compared as a chip only", () => {
    expect(pinnedBaselineOf(back)).toBe("B");
    expect(
      comparedChipOf({
        search: "?compared=A",
        runIds: ["A", "B"],
        baselineId: "B",
        subjectId: "B",
      }),
    ).toBe("A");
    expect(
      comparedChipOf({
        search: "?compared=B",
        runIds: ["A", "B"],
        baselineId: "B",
        subjectId: "A",
      }),
    ).toBeNull();
  });
});
