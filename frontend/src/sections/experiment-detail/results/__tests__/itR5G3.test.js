import { describe, expect, it } from "vitest";
import { whyMenuItems } from "../SeeWhyButton";
import { namedWorse } from "../ResultsVerdict";
import { anyWorseTip } from "../ResultsToolbar";
import {
  lastRowText,
  markText,
  reviewedText,
  worseEvalsOf,
} from "../reviewMarks";
import { patchSearch, readResultsState } from "../useResultsUrlState";
import { withLiveProgress, liveProgress } from "../liveModel";

describe("IT-R5 G3", () => {
  it("S6: Why ▾ has the same three items", () => {
    const items = whyMenuItems({
      noun: "task",
      worse: 3,
      canWhy: true,
      why: null,
    });
    expect(items.map((i) => i.label)).toEqual([
      "Open the worst task",
      "Show the 3 that got worse",
      "Explain",
    ]);
    expect(items[2].disabled).toBe(true);
    expect(items[2].reason).toMatch(/No finding/);
  });

  it("P2: the verdict prints worse on <eval> n · on any eval m", () => {
    const v = namedWorse(
      {
        parts: [
          { t: "text", text: "a · " },
          { t: "term", k: "worse", text: "worse 3" },
        ],
      },
      "Correctness",
      9,
    );
    expect(v.parts.map((p) => p.text).join("")).toBe(
      "a · worse on Correctness 3 · on any eval 9",
    );
  });

  it("P2: the Worse pill's tooltip carries both numbers", () => {
    expect(
      anyWorseTip({
        metricName: "Correctness",
        worse: 3,
        total: 9,
        oneCandidate: true,
      }),
    ).toBe("9 rows worse on any eval · 3 on Correctness");
  });

  it("P2: show=worse is the URL word, regressed still reads", () => {
    expect(readResultsState(new URLSearchParams("show=worse")).show).toBe(
      "regressed",
    );
    expect(readResultsState(new URLSearchParams("show=regressed")).show).toBe(
      "regressed",
    );
    expect(patchSearch("", { show: "regressed" }).get("show")).toBe("worse");
  });

  it("P2: worse evals of a row, primary first", () => {
    const row = {
      results: {
        base: { movement: { "eval:tone": "regressed" } },
        cand: {
          movement: {
            "eval:tone": "regressed",
            "eval:corr": "regressed",
            "eval:g": "improved",
          },
        },
      },
    };
    const defsByKey = {
      "eval:tone": { name: "Tone", kind: "eval" },
      "eval:corr": { name: "Correctness", kind: "eval" },
    };
    expect(
      worseEvalsOf(row, {
        baselineId: "base",
        defsByKey,
        primaryKey: "eval:corr",
      }).map((w) => w.name),
    ).toEqual(["Correctness", "Tone"]);
  });

  it("P3: marks, the reviewed count and the last-row toast", () => {
    expect(markText({ choice: "B" })).toBe("✓ B");
    expect(markText({ choice: "tie" })).toBe("= tie");
    expect(markText({ choice: "A", blind: true })).toBe("✓");
    const marks = { a: { choice: "A" }, b: { choice: "tie" } };
    expect(reviewedText(marks, ["a", "b"])).toBe("2/2 reviewed");
    expect(reviewedText({}, ["a"])).toBeNull();
    expect(lastRowText({ label: "Worse (9)" }, marks, ["a", "b"])).toBe(
      "Last row in Worse (9) · all reviewed",
    );
    expect(lastRowText({ label: "Worse (9)" }, marks, ["a", "b", "c"])).toBe(
      "Last row in Worse (9) · 2/3 reviewed",
    );
  });

  it("S3: chips and the live counter read one progress, never past the rows", () => {
    const shown = [
      { run_id: "f", progress: { status: "Running", done: 0, total: 24 } },
    ];
    const live = [
      {
        run_id: "f",
        progress: { status: "Running", done: 30, scored: 2, total: 30 },
      },
    ];
    const [r] = withLiveProgress(shown, live, 24);
    expect(r.progress.done).toBe(24);
    expect(r.progress.total).toBe(24);
    expect(liveProgress(withLiveProgress(live, live, 24)).scored).toBe(24);
  });
});

describe("IT-R5 G3 P4: fix in place", () => {
  it("the scope says what happens to every row", async () => {
    const { scopeSentence } = await import("../../iterate/ScopePane");
    const base = { n: 13, rest: 17, total: 30, label: "#2B" };
    expect(scopeSentence({ ...base, then: false, carry: true })).toBe(
      "13 rows rerun · 17 other rows keep their #2B results (not re-run)",
    );
    expect(scopeSentence({ ...base, then: true, carry: true })).toBe(
      "13 rows rerun with the fix, then the other 17 · one verdict on all 30",
    );
    expect(scopeSentence({ ...base, then: false, carry: false })).toBe(
      "13 rows rerun · the other 17 are left out of the new run",
    );
    expect(scopeSentence({ ...base, n: 30, rest: 0 })).toBe(
      "All 30 rows rerun",
    );
  });
  it("Fix the next failing step opens the first row the new run still fails", async () => {
    const { stillFailingRows } = await import("../../iterate/iterateSummary");
    const { nextFixHrefOf } = await import("../../iterate/IterateResultBanner");
    const matrix = {
      rows: [
        {
          row_id: "r1",
          cells: { B: { trials: [1, 1] }, C: { trials: [1, 0] } },
        },
        {
          row_id: "r2",
          cells: { B: { trials: [0, 0] }, C: { trials: [0, 1] } },
        },
        { row_id: "r3", cells: { B: { trials: [0] }, C: { trials: [1] } } },
      ],
    };
    // still failing (failed on both) first, then new failures
    expect(stillFailingRows(matrix, "B", "C")).toEqual(["r2", "r1"]);
    const href = nextFixHrefOf({
      datasetId: "ds",
      baseId: "B",
      candId: "C",
      stillFailing: ["r2", "r1"],
    });
    const sp = new URLSearchParams(href.split("?")[1]);
    expect(href.startsWith("/dashboard/develop/ds/compare/tasks?")).toBe(true);
    expect(sp.get("row")).toBe("r2");
    expect(sp.get("cand")).toBe("C");
    expect(sp.get("baseline")).toBe("B");
    expect(nextFixHrefOf({ datasetId: "ds", baseId: "B", candId: "C" })).toBe(
      null,
    );
  });
});

describe("IT-R5 G3 P1/P5: budgets", () => {
  it("the strip's lead line drops its Confidence clause (it moves to +N)", async () => {
    const { splitConfidence } = await import("../ResultsVerdict");
    const verdict = {
      parts: [
        { t: "run", text: "#2D" },
        { t: "text", text: " beats baseline " },
        { t: "run", text: "#2B" },
        { t: "text", text: ": +25 pts across 30 tasks. " },
        { t: "term", k: "confidence", text: "Confidence" },
        { t: "text", text: ": medium (30 tasks × 3 tries)." },
      ],
    };
    const out = splitConfidence(verdict);
    expect(out.confidence).toBe("medium (30 tasks × 3 tries)");
    expect(out.verdict.sentence).toBe(
      "#2D beats baseline #2B: +25 pts across 30 tasks.",
    );
    expect(splitConfidence({ parts: [] }).confidence).toBe(null);
  });
  it("the stats strip names its trial (S7)", async () => {
    const { trialNote } = await import("../drawer/OutputCard");
    expect(trialNote(3, 3, [true, true, false])).toBe(
      "trial 3 of 3 · the failing one",
    );
    expect(trialNote(1, 3, [true, false, false])).toBe(
      "trial 1 of 3 · the passing one",
    );
    expect(trialNote(2, 3, [true, true, true])).toBe("trial 2 of 3");
    expect(trialNote(1, 3)).toBe("trial 1 of 3");
  });
});

describe("IT-R5 G3 S5: counts add up", () => {
  it("names each version's rows", async () => {
    const { populationLine } = await import("../populationLine");
    expect(
      populationLine({
        lead: { bothRan: 19 },
        cand: { dataset_version: { number: 6 }, progress: { total: 20 } },
        base: { dataset_version: { number: 5 }, progress: { total: 238 } },
      }),
    ).toBe("19 rows both ran (v5: 238 rows, v6: 20 rows)");
    expect(
      populationLine({
        lead: { bothRan: 19 },
        cand: { dataset_version: { number: 6 } },
        base: { dataset_version: { number: 5 } },
      }),
    ).toBe("19 rows both ran · candidate on dataset v6, baseline on v5");
  });
});

describe("IT-R5 G3 S2: why the tool failed", () => {
  it("not attached vs attached but not called", async () => {
    const { neverCalledText, missingToolsOf } = await import(
      "../drawer/whyLine"
    );
    const result = { steps: { tool_calls: 0 }, output: "Done." };
    const expected = {
      value: {
        expected_tool_calls: [
          { name: "cancel_order", args: { order_id: "A-1" } },
          { name: "lookup_order", args: { order_id: "A-1" } },
        ],
      },
    };
    const tools = { names: ["lookup_order"], noun: "prompt" };
    expect(neverCalledText(result, expected, 1, tools)).toBe(
      "cancel_order is not attached to this prompt; lookup_order was attached but not called; replied as if it had",
    );
    expect(missingToolsOf(result, expected, 1, tools)).toEqual([
      "cancel_order",
    ]);
    // tools unknown: the old sentence
    expect(neverCalledText(result, expected, 1)).toMatch(
      /^never called cancel_order and lookup_order/,
    );
  });
});

describe("IT-R5 G3 S3: Excel-style columns", () => {
  it("Freeze up to here freezes every shown column through this one", async () => {
    const { pinThroughColumn, resolveColumns } = await import(
      "src/sections/common/ux/oneview/columnLayout"
    );
    const cols = [
      { id: "row", label: "Row", locked: true, defaultPinned: "left" },
      { id: "input", label: "Input" },
      { id: "out", label: "Output" },
      { id: "score", label: "Score" },
    ];
    const prefs = pinThroughColumn({}, cols, "out");
    const pinned = resolveColumns(cols, prefs)
      .filter((c) => c.pinned === "left")
      .map((c) => c.id);
    expect(pinned).toEqual(expect.arrayContaining(["input", "out"]));
    expect(pinned).not.toContain("score");
  });
  it("warns above half the width", async () => {
    const { frozenWarning } = await import(
      "src/sections/common/ux/oneview/ColumnsMenu"
    );
    expect(frozenWarning(0.58)).toBe("Frozen columns take 58% of the width");
    expect(frozenWarning(0.42)).toBe(null);
  });
});
