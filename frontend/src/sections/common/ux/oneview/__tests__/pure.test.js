import { describe, expect, it } from "vitest";
import { OUTLINE_MAX, outlineIsOpen, setOutline } from "../useOutline";
import { fitActions, primaryCount } from "../fitActions";
import { viewportDensityOf, SHORT_SCREEN } from "../useViewportDensity";
import { scrubLegacyParams } from "../LegacyParamScrub";
import { arrangeMenuItems } from "../MenuItems";
import { headerMenuItems, toAgMenuItems } from "../headerMenuItems";
import {
  canMoveColumnBy,
  isDefaultLayout,
  resolveColumns,
} from "../columnLayout";
import { sectionDefaultOpen } from "../Section";
import { visibleHintId } from "../Hint";

describe("outline", () => {
  it("stores touched ids last and caps at 500 (oldest out)", () => {
    let e = [];
    for (let i = 0; i < OUTLINE_MAX + 20; i += 1)
      e = setOutline(e, [`r${i}`], true);
    expect(e).toHaveLength(OUTLINE_MAX);
    expect(e[0][0]).toBe("r20");
    // Touching an old id moves it to the end (LRU), no duplicate.
    e = setOutline(e, ["r20"], false);
    expect(e).toHaveLength(OUTLINE_MAX);
    expect(e[e.length - 1]).toEqual(["r20", false]);
    expect(e.filter(([id]) => id === "r20")).toHaveLength(1);
  });

  it("an explicit choice beats the default; the default may be a function", () => {
    const map = new Map([["a", false]]);
    expect(outlineIsOpen(map, "a", true)).toBe(false);
    expect(outlineIsOpen(map, "b", (id) => id === "b")).toBe(true);
    expect(outlineIsOpen(map, "c", () => false)).toBe(false);
    expect(setOutline([], ["x", "y"], true, 1)).toEqual([["y", true]]);
  });
});

describe("fitActions", () => {
  const A = [
    { id: "run", priority: 1, variant: "primary", icon: "i" },
    { id: "review", priority: 2, icon: "i", hideLabelBelow: 150 },
    { id: "edit", priority: 3 },
    { id: "csv", priority: 3 },
    { id: "evals", priority: 4, icon: "i", hideLabelBelow: 1 },
  ];
  const W = {
    run: { full: 100, icon: 32 },
    review: { full: 90, icon: 32 },
    edit: { full: 60 },
    csv: { full: 110 },
    evals: { full: 130, icon: 32 },
  };
  const opts = { gap: 4, moreWidth: 70 };

  it("shows everything in declared order when it fits", () => {
    expect(fitActions(A, W, 2000, opts)).toEqual({
      shown: ["run", "review", "edit", "csv", "evals"],
      compact: [],
      overflow: [],
    });
  });

  it("lowest priority goes first: label drop when allowed, then overflow", () => {
    // full total = 490 + 16 gaps = 506; evals → icon saves 98 → 408
    const r = fitActions(A, W, 420, opts);
    expect(r.compact).toEqual(["evals"]);
    expect(r.overflow).toEqual([]);
    // tighter: evals overflows after compacting, then the right-most 3 (csv)
    const t = fitActions(A, W, 340, opts);
    expect(t.overflow).toEqual(["csv", "evals"]);
    expect(t.shown).toEqual(["run", "review", "edit"]);
  });

  it("drops priority 2 labels only after 3s and 4s are gone", () => {
    const r = fitActions(A, W, 215, opts);
    expect(r.overflow).toEqual(["edit", "csv", "evals"]);
    expect(r.compact).toEqual(["review"]);
    expect(r.shown).toEqual(["run", "review"]);
  });

  it("priority 1 / primary never overflows, even with no room", () => {
    const r = fitActions(A, W, 10, opts);
    expect(r.shown).toContain("run");
    expect(r.overflow).not.toContain("run");
    expect(
      fitActions(
        [{ id: "p", priority: 3, variant: "primary" }],
        { p: { full: 99 } },
        5,
      ).overflow,
    ).toEqual([]);
  });

  it("hideLabelBelow forces icon-only whenever the bar is narrower", () => {
    const one = [{ id: "x", icon: "i", hideLabelBelow: 1300 }];
    const w = { x: { full: 90, icon: 32 } };
    expect(fitActions(one, w, 1200).compact).toEqual(["x"]);
    expect(fitActions(one, w, 1400).compact).toEqual([]);
  });

  it("counts primaries (P3)", () => {
    expect(primaryCount(A)).toBe(1);
    expect(primaryCount([...A, { id: "x", variant: "primary" }])).toBe(2);
  });
});

describe("viewport density", () => {
  it("short < 820 ≤ regular < 1080 ≤ tall", () => {
    expect(viewportDensityOf(720)).toBe("short");
    expect(viewportDensityOf(768)).toBe("short");
    expect(viewportDensityOf(819)).toBe("short");
    expect(viewportDensityOf(820)).toBe("regular");
    expect(viewportDensityOf(900)).toBe("regular");
    expect(viewportDensityOf(1079)).toBe("regular");
    expect(viewportDensityOf(1080)).toBe("tall");
    expect(viewportDensityOf(undefined)).toBe("regular");
    expect(SHORT_SCREEN).toBe("(max-height:819px)");
  });

  it("roomy sections are open unless short; explicit defaults win", () => {
    expect(sectionDefaultOpen("roomy", "short")).toBe(false);
    expect(sectionDefaultOpen("roomy", "regular")).toBe(true);
    expect(sectionDefaultOpen(false, "tall")).toBe(false);
    expect(sectionDefaultOpen(true, "short")).toBe(true);
  });
});

describe("scrubLegacyParams", () => {
  it("removes only `ui` and keeps every other param in order", () => {
    expect(scrubLegacyParams("?ui=simple&tab=data&row=4")).toBe(
      "tab=data&row=4",
    );
    expect(scrubLegacyParams("?tab=data&ui=advanced&cols=a%2Cb")).toBe(
      "tab=data&cols=a%2Cb",
    );
    expect(scrubLegacyParams("?ui=advanced")).toBe("");
    expect(scrubLegacyParams("?tab=data")).toBe(null);
    expect(scrubLegacyParams("")).toBe(null);
  });
});

describe("menu items", () => {
  it("puts danger items last after one divider and trims dividers", () => {
    const out = arrangeMenuItems([
      { divider: true },
      { id: "del", label: "Delete", danger: true },
      { id: "a", label: "A" },
      { divider: true },
      { divider: true },
      { id: "b", label: "B" },
      { divider: true },
    ]);
    expect(out.map((i) => i.id ?? "—")).toEqual(["a", "—", "b", "—", "del"]);
  });

  const COLS = [
    { id: "name", label: "Name", locked: true, defaultPinned: "left" },
    { id: "rows", label: "Rows", groupable: true },
    { id: "kind", label: "Kind", sortable: false },
  ];
  const fakeLayout = (prefs = {}) => ({
    columns: resolveColumns(COLS, prefs),
    isDefault: isDefaultLayout(COLS, prefs),
    canMoveBy: (id, d) => canMoveColumnBy(COLS, prefs, id, d),
    pin() {},
    moveBy() {},
    hide() {},
    reset() {},
    openMenu() {},
  });

  it("header menu: Excel order, copy and reasons", () => {
    const items = headerMenuItems(fakeLayout(), "rows", {
      sort: { id: "rows", desc: true },
      onSort: () => {},
      group: null,
      onGroup: () => {},
    });
    expect(items.map((i) => (i.divider ? "—" : i.label))).toEqual([
      "Sort ascending",
      "Sort descending",
      "Clear sort",
      "—",
      "Group by Rows",
      "—",
      "Freeze column",
      "Move left",
      "Move right",
      "—",
      "Hide column",
      "—",
      "Columns…",
      "Reset columns",
    ]);
    expect(items.find((i) => i.id === "sortDesc").checked).toBe(true);
    expect(items.find((i) => i.id === "moveLeft")).toMatchObject({
      disabled: true,
      reason: "Already first",
    });
    expect(items.find((i) => i.id === "resetColumns").disabled).toBe(true);
    const name = headerMenuItems(fakeLayout(), "name", {});
    expect(name.find((i) => i.id === "hide")).toMatchObject({
      disabled: true,
      reason: "Name is always shown",
    });
    expect(name.find((i) => i.id === "freeze").label).toBe("Unfreeze column");
    // No onSort → no sort rows; not groupable → no group rows.
    expect(name.some((i) => i.id === "sortAsc" || i.id === "groupBy")).toBe(
      false,
    );
    const kind = headerMenuItems(fakeLayout(), "kind", { onSort: () => {} });
    expect(kind.find((i) => i.id === "sortAsc").disabled).toBe(true);
    expect(
      headerMenuItems(fakeLayout(), "rows", {
        group: "rows",
        onGroup: () => {},
      }).find((i) => i.id === "stopGroup").label,
    ).toBe("Stop grouping");
  });

  it("AG menu items: separators, tooltips, no leading/trailing separator", () => {
    const ag = toAgMenuItems(headerMenuItems(fakeLayout(), "name", {}));
    expect(ag[0]).toMatchObject({ name: "Unfreeze column" });
    expect(ag[ag.length - 1]).toMatchObject({ name: "Reset columns" });
    expect(ag.find((i) => i.name === "Hide column")).toMatchObject({
      disabled: true,
      tooltip: "Name is always shown",
    });
    expect(ag.filter((i) => i === "separator").length).toBe(2);
  });
});

describe("hints", () => {
  it("first mounted, not dismissed, wins", () => {
    const mounted = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(visibleHintId(mounted, [])).toBe("a");
    expect(visibleHintId(mounted, ["a"])).toBe("b");
    expect(visibleHintId(mounted, ["a", "b", "c"])).toBe(null);
    expect(visibleHintId([], [])).toBe(null);
  });
});
