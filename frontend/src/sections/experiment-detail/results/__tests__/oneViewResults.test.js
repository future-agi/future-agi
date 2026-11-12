// One view (implementation): the pure pieces of the results grid, the verdict fold,
// the group menu and the matrix frontier line.
import { describe, expect, it } from "vitest";
import {
  buildResultColumns,
  keyOfColId,
  legacyHiddenToPrefs,
  resultsColumnCatalog,
  shapeColumns,
} from "../resultsColumns";
import { resolveColumns } from "src/sections/common/ux/oneview/columnLayout";
import { movedIndex } from "../ResultsGrid";
import { verdictFoldLabel } from "../VerdictFold";
import { fromHeaderItems, buildGroupMenu } from "../groupMenu";
import { runLineText } from "../RunLine";
import { defaultResultsDensity, ROW_HEIGHT } from "../RunResultsView";
import { diffTarget } from "../../ExperimentBarRightSection/ExperimentBarDataRightSection";
import { frontierLine } from "../../matrix/ParetoStrip";

const A = "run-a";
const B = "run-b";
const runs = [
  { run_id: A, label: "#2B", trials: 3 },
  { run_id: B, label: "#2A", trials: 3 },
];
const summary = {
  runs,
  metrics: [
    {
      key: "eval:task",
      name: "Task success",
      kind: "eval",
      output_type: "pass_fail",
      runs: [A, B],
    },
  ],
  human_metrics: [],
  system_metrics: [
    { key: "ops:latency", name: "Latency", kind: "system" },
    { key: "ops:cost", name: "Cost", kind: "system" },
    { key: "ops:tokens", name: "Tokens", kind: "system" },
    { key: "agent:steps", name: "Steps", kind: "system" },
  ],
  aggregates: {},
  preference: {},
  metadata_keys: ["intent"],
};
const runsById = Object.fromEntries(runs.map((r) => [r.run_id, r]));

describe("resultsColumnCatalog", () => {
  it("locks and freezes Row and Input; Splits, Metadata, Tokens, roll-ups start hidden", () => {
    const cat = resultsColumnCatalog(summary, { taskSuite: true });
    const by = Object.fromEntries(cat.map((c) => [c.id, c]));
    expect(by.row).toMatchObject({
      label: "Task",
      locked: true,
      defaultPinned: "left",
    });
    expect(by.input).toMatchObject({ locked: true, defaultPinned: "left" });
    expect(by.splits.defaultVisible).toBe(false);
    expect(by.meta.defaultVisible).toBe(false);
    expect(by["ops:tokens"].defaultVisible).toBe(false);
    expect(by["ops:latency"].label).toBe("Wall time");
    expect(by["rollup:tool_calls"].defaultVisible).toBe(false);
    // agent:steps is the Steps roll-up: no duplicate entry
    expect(cat.some((c) => c.id === "rollup:steps")).toBe(false);
    expect(by.why).toBeUndefined();
  });
  it("adds Why only on a multi-run page with Show ≠ All", () => {
    expect(
      resultsColumnCatalog(summary, { why: true }).some((c) => c.id === "why"),
    ).toBe(true);
    const one = { ...summary, runs: [runs[0]] };
    expect(
      resultsColumnCatalog(one, { why: true }).some((c) => c.id === "why"),
    ).toBe(false);
  });
});

describe("buildResultColumns with a layout", () => {
  const catalog = resultsColumnCatalog(summary, {});
  const build = (prefs) =>
    buildResultColumns({
      summary,
      baselineId: A,
      runsById,
      layout: resolveColumns(catalog, prefs),
    });
  const ids = (cols) => cols.map((c) => c.colId ?? c.groupId);

  it("default: hidden-by-default columns are left out, frozen ones pinned", () => {
    const cols = build({});
    expect(ids(cols)).not.toContain("splits");
    expect(ids(cols)).not.toContain("ops:tokens");
    expect(ids(cols)).not.toContain("meta:intent");
    expect(ids(cols).slice(0, 2)).toEqual(["row", "input"]);
    expect(cols.find((c) => c.colId === "input").pinned).toBe("left");
  });
  it("hide, order, freeze and width apply to a whole per-run group", () => {
    const cols = build({
      visible: { "ops:cost": false, meta: true },
      pinned: { expected: "left" },
      widths: { output: 420 },
    });
    expect(ids(cols)).not.toContain("ops:cost");
    expect(ids(cols)).toContain("meta:intent");
    expect(cols.find((c) => c.colId === "expected").pinned).toBe("left");
    const out = cols.find((c) => c.groupId === "output");
    expect(out.children.every((c) => c.width === 420)).toBe(true);
    expect(out.children.every((c) => c.pinned === null)).toBe(true);
  });
  it("without a layout the legacy hidden set still works", () => {
    const cols = buildResultColumns({
      summary,
      baselineId: A,
      runsById,
      hidden: new Set(["expected"]),
    });
    expect(ids(cols)).not.toContain("expected");
    expect(ids(cols)).toContain("splits");
  });
});

describe("shapeColumns / keyOfColId", () => {
  it("maps grid ids to catalog ids", () => {
    expect(keyOfColId("ops:cost|run-a")).toBe("ops:cost");
    expect(keyOfColId("meta:intent")).toBe("meta");
    expect(keyOfColId("row")).toBe("row");
  });
  it("keeps entries the catalog does not know, at the end", () => {
    const out = shapeColumns(
      [
        { key: "x", def: { colId: "x" } },
        { key: "a", def: { colId: "a" } },
      ],
      [{ id: "a", index: 0, hidden: false, pinned: null }],
    );
    expect(out.map((d) => d.colId)).toEqual(["a", "x"]);
  });
});

describe("legacyHiddenToPrefs", () => {
  it("turns the old hidden set into visible overrides", () => {
    const cat = resultsColumnCatalog(summary, {});
    expect(legacyHiddenToPrefs(["ops:tokens", "meta", "splits"], cat)).toEqual(
      {},
    );
    expect(legacyHiddenToPrefs(["expected", "+rollup:turns"], cat)).toEqual({
      visible: {
        splits: true,
        expected: false,
        meta: true,
        "ops:tokens": true,
        "rollup:turns": true,
      },
    });
  });
});

describe("movedIndex", () => {
  it("places a dragged key before its new right neighbour", () => {
    expect(
      movedIndex(["a", "b", "c", "d"], ["a", "c", "b", "b", "d"], "b"),
    ).toBe(2);
    expect(movedIndex(["a", "b", "c"], ["b", "c", "a"], "a")).toBe(2);
  });
});

describe("one-view copy helpers", () => {
  it("verdict fold", () => {
    expect(verdictFoldLabel(false, 4)).toBe("+3 more");
    expect(verdictFoldLabel(true, 4)).toBe("Show fewer");
  });
  it("run line", () => {
    expect(runLineText(runs, A)).toBe("2 runs · baseline #2B");
    expect(runLineText([...runs, { run_id: "z", not_found: true }], A)).toBe(
      "2 runs (1 missing) · baseline #2B",
    );
  });
  it("density defaults by window", () => {
    expect(defaultResultsDensity("short")).toBe("compact");
    // R4 8f: 8+ rows above the fold at 1440×900 (regular) → compact.
    expect(defaultResultsDensity("regular")).toBe("compact");
    expect(defaultResultsDensity("tall")).toBe("comfortable");
    expect(ROW_HEIGHT.compact).toBeLessThan(ROW_HEIGHT.comfortable);
  });
  it("Diff target and reason", () => {
    expect(diffTarget({ runCount: 3, hasExpected: false })).toEqual({
      target: "baseline",
      reason: null,
    });
    expect(diffTarget({ runCount: 1, hasExpected: false }).reason).toMatch(
      /no expected column/,
    );
  });
  it("frontier line is the collapsed Section's answer", () => {
    const pModel = {
      x: "cost",
      frontier: [
        { id: "d", label: "#2D", quality: 0.91, cost: 0.21 },
        { id: "c", label: "#2C", quality: 0.86, cost: 0.019 },
      ],
      sentence: "Claude Code passes 5.4 pts more trials than #2C.",
    };
    expect(frontierLine(pModel)).toBe(
      "#2D most solved · #2C best value · Claude Code passes 5.4 pts more trials than #2C.",
    );
  });
});

describe("group menu carries the Excel header items", () => {
  it("fromHeaderItems keeps order, reasons and dividers", () => {
    const items = fromHeaderItems([
      { id: "pin", label: "Freeze column", run: () => {} },
      { divider: true },
      {
        id: "left",
        label: "Move left",
        disabled: true,
        reason: "Already first",
      },
    ]);
    expect(items.map((i) => i.label)).toEqual(["Freeze column", "Move left"]);
    expect(items[0].divider).toBe(true);
    expect(items[1]).toMatchObject({
      divider: true,
      disabled: true,
      secondary: "Already first",
    });
  });
  it("replaces the old Hide column item", () => {
    const layoutItems = [{ label: "Hide column", onClick: () => {} }];
    const menu = buildGroupMenu(
      { key: "ops:cost", kind: "system" },
      {
        layoutItems,
        hide: () => {},
        multiRun: true,
        firstCandidate: B,
        sortBy: () => {},
      },
    );
    expect(menu.filter((i) => i.label === "Hide column")).toHaveLength(1);
  });
});
