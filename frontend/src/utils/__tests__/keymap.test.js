import { describe, expect, it } from "vitest";
import {
  KEYMAP,
  SURFACES,
  actionForEvent,
  keyLabel,
  keymapEntry,
  matchesAction,
  shortcutRows,
  withKeyHint,
} from "../keymap";
import {
  hasCommandModifier,
  isInGrid,
  isSafeForSingleKey,
  isTextInput,
} from "../keyboard-guards";

const ev = (key, mods = {}) => ({ key, ...mods });

describe("keymap", () => {
  it("has one entry per action and every action has keys and caps", () => {
    const actions = KEYMAP.map((e) => e.action);
    expect(new Set(actions).size).toBe(actions.length);
    KEYMAP.forEach((e) => {
      expect(e.keys.length).toBeGreaterThan(0);
      expect(e.display.length).toBeGreaterThan(0);
      expect(Object.keys(e.surfaces).length).toBeGreaterThan(0);
    });
  });

  it("binds no key twice on the same surface (one documented override)", () => {
    // Row view: Cmd+S is saveVersion; `submit` keeps mod+s for review/drawer
    // and for an open field editor (useRowKeyboardNav).
    // R4 (lane F, D18 / D25): keys shared by an R3 action and an R4 action
    // on one surface; useShortcuts fires the one the screen has a live
    // handler for (keymap.actionsForEvent).
    const allowed = new Set([
      "row mod+s",
      "results c",
      "results C",
      "taskCompare i",
      "taskCompare I",
      "newExperiment 1",
      "newExperiment 2",
      "newExperiment 3",
      "newExperiment 4",
      "newExperiment alt+ArrowLeft",
    ]);
    SURFACES.forEach((surface) => {
      const seen = new Map();
      KEYMAP.filter((e) => e.surfaces[surface]).forEach((e) =>
        e.keys.forEach((k) => {
          if (!allowed.has(`${surface} ${k}`))
            expect(seen.has(k), `${k} on ${surface}`).toBe(false);
          seen.set(k, e.action);
        }),
      );
    });
  });

  it("covers every R3 surface with a complete table", () => {
    const has = (surface, action) =>
      shortcutRows(surface).some((r) => r.action === action);
    expect(has("global", "palette")).toBe(true);
    expect(has("global", "askFalcon")).toBe(true);
    expect(has("global", "help")).toBe(true);
    ["next", "open", "select", "newDataset", "duplicate", "search"].forEach(
      (a) => expect(has("list", a), a).toBe(true),
    );
    [
      "next",
      "open",
      "edit",
      "select",
      "filter",
      "columns",
      "density",
      "history",
      "goToRow",
      "search",
      "escape",
    ].forEach((a) => expect(has("grid", a), a).toBe(true));
    ["tab", "splits", "collapseAll", "copyRowId", "saveVersion"].forEach((a) =>
      expect(has("row", a), a).toBe(true),
    );
    [
      "next",
      "nextUnreviewed",
      "prevCandidate",
      "nextTrial",
      "verdictBaseline",
      "verdictTie",
      "verdictOther",
      "verdictBothBad",
      "makeBaseline",
      "toggleMap",
      "inspect",
      "diffOnly",
      "nextDiff",
      "prevDiff",
      "jumpFailedCheck",
      "expandAllSteps",
      "collapseAllSteps",
      "markViewed",
      "addNote",
      "copyLink",
      "search",
      "escape",
    ].forEach((a) => expect(has("taskCompare", a), a).toBe(true));
    ["cellLeft", "cellDown", "open", "makeBaseline", "moreMetrics"].forEach(
      (a) => expect(has("matrix", a), a).toBe(true),
    );
    ["download", "falcon", "toggleChart", "copyLink"].forEach((a) =>
      expect(has("summary", a), a).toBe(true),
    );
    ["notes", "undo", "comments"].forEach((a) =>
      expect(has("review", a), a).toBe(true),
    );
    shortcutRows("grid").forEach((r) =>
      expect(["navigate", "view", "act"]).toContain(r.group),
    );
  });

  it("one view (G-09): no depth modes; map, inspect, columns, filter, density, diff", () => {
    const has = (surface, action) =>
      shortcutRows(surface).some((r) => r.action === action);
    expect(keymapEntry("cycleDepth")).toBeNull();
    expect(actionForEvent(ev("m"), "taskCompare")).toBe("toggleMap");
    expect(
      shortcutRows("taskCompare").find((r) => r.action === "toggleMap")
        .description,
    ).toBe("Show or hide the map");
    expect(actionForEvent(ev("i"), "taskCompare")).toBe("inspect");
    // Columns (C) on grid, list and results — not where C means something else.
    ["grid", "list", "results"].forEach((s) =>
      expect(actionForEvent(ev("c"), s), s).toBe("columns"),
    );
    expect(actionForEvent(ev("c"), "leaderboard")).toBe("compareSelected");
    expect(actionForEvent(ev("c"), "summary")).toBe("copyLink");
    // Expand / collapse all on Summary too; the leaderboard keeps E = group.
    expect(actionForEvent(ev("e"), "summary")).toBe("expandAllSteps");
    expect(actionForEvent(ev("E", { shiftKey: true }), "summary")).toBe(
      "collapseAllSteps",
    );
    expect(actionForEvent(ev("e"), "leaderboard")).toBe("toggleGroup");
    // Filter on leaderboard + results; density and the diff toggle on results.
    expect(has("leaderboard", "filter")).toBe(true);
    expect(has("results", "filter")).toBe(true);
    expect(actionForEvent(ev("d"), "results")).toBe("density");
    expect(actionForEvent(ev("D", { shiftKey: true }), "results")).toBe(
      "diffToggle",
    );
    expect(actionForEvent(ev("D", { shiftKey: true }), "list")).toBe(
      "duplicate",
    );
    // Summary keeps D = download.
    expect(actionForEvent(ev("d"), "summary")).toBe("download");
  });

  it("prefers a shift binding on surfaces that have one", () => {
    expect(actionForEvent(ev("J", { shiftKey: true }), "drawer")).toBe(
      "nextRegressed",
    );
    expect(actionForEvent(ev("J", { shiftKey: true }), "taskCompare")).toBe(
      "nextUnreviewed",
    );
    // review has no shift+j: Shift+J is still `next`; caps lock J is `next`
    expect(actionForEvent(ev("J", { shiftKey: true }), "review")).toBe("next");
    expect(actionForEvent(ev("J"), "drawer")).toBe("next");
    expect(actionForEvent(ev("C", { shiftKey: true }), "row")).toBe(
      "copyRowId",
    );
    expect(actionForEvent(ev("c"), "row")).toBe("copyLink");
    expect(actionForEvent(ev("s", { metaKey: true }), "row")).toBe(
      "saveVersion",
    );
    expect(actionForEvent(ev("k", { metaKey: true }), "global")).toBe(
      "palette",
    );
    expect(
      actionForEvent(ev("ArrowLeft", { altKey: true }), "newExperiment"),
    ).toBe("wizardPrevStep");
    expect(actionForEvent(ev("2"), "row")).toBe("tab");
    expect(actionForEvent(ev("3"), "taskCompare")).toBe("verdictOther");
  });

  it("follows §4.1: J/↓ next, K/↑ previous, D diff, R raw, ? help", () => {
    expect(actionForEvent(ev("j"), "row")).toBe("next");
    expect(actionForEvent(ev("ArrowDown"), "review")).toBe("next");
    expect(actionForEvent(ev("K"), "drawer")).toBe("prev");
    expect(actionForEvent(ev("d"), "row")).toBe("diff");
    expect(actionForEvent(ev("r"), "drawer")).toBe("raw");
    expect(actionForEvent(ev("?", { shiftKey: true }), "row")).toBe("help");
    expect(actionForEvent(ev("Escape"), "row")).toBe("escape");
  });

  it("keeps surface-only keys off other surfaces", () => {
    expect(actionForEvent(ev("e"), "row")).toBe("edit");
    expect(actionForEvent(ev("e"), "drawer")).toBeNull();
    expect(actionForEvent(ev("s"), "row")).toBe("splits");
    expect(actionForEvent(ev("s"), "review")).toBe("skip");
    expect(actionForEvent(ev(","), "drawer")).toBe("prevCandidate");
    expect(actionForEvent(ev("1"), "row")).toBe("tab");
  });

  it("matches ⌘↵ / Ctrl+↵ and ⌘S as submit, never plain Enter", () => {
    expect(actionForEvent(ev("Enter", { metaKey: true }), "row")).toBe(
      "submit",
    );
    expect(actionForEvent(ev("Enter", { ctrlKey: true }), "review")).toBe(
      "submit",
    );
    expect(actionForEvent(ev("s", { metaKey: true }), "review")).toBe("submit");
    expect(actionForEvent(ev("Enter"), "row")).toBeNull();
    expect(actionForEvent(ev("j", { metaKey: true }), "row")).toBeNull();
    expect(actionForEvent(ev("j", { altKey: true }), "row")).toBeNull();
  });

  it("labels keys for tooltips", () => {
    expect(keyLabel("next")).toBe("J");
    expect(keyLabel("next", { all: true })).toBe("J / ↓");
    expect(keyLabel("nope")).toBe("");
    expect(withKeyHint("Next row", "next")).toBe("Next row (J)");
    expect(matchesAction(ev("f"), "full")).toBe(true);
    expect(keymapEntry("help").display).toEqual(["?"]);
  });

  it("lists sheet rows per surface with surface wording", () => {
    const rows = shortcutRows("row");
    expect(rows.find((r) => r.action === "submit").description).toBe(
      "Apply edit",
    );
    expect(rows.some((r) => r.action === "skip")).toBe(false);
    expect(shortcutRows("review").map((r) => r.action)).toContain("skip");
  });
});

describe("keyboard guards", () => {
  it("treats body and null as safe, text fields as unsafe", () => {
    expect(isSafeForSingleKey(null)).toBe(true);
    expect(isSafeForSingleKey(document.body)).toBe(true);
    const input = document.createElement("input");
    expect(isTextInput(input)).toBe(true);
    expect(isSafeForSingleKey(input)).toBe(false);
    const ta = document.createElement("textarea");
    expect(isSafeForSingleKey(ta)).toBe(false);
  });

  it("R3 RV#1: buttons, tabs, chips and links are safe; sliders, menus and dialogs block", () => {
    expect(isSafeForSingleKey(document.createElement("button"))).toBe(true);
    expect(isSafeForSingleKey(document.createElement("a"))).toBe(true);
    const tab = document.createElement("button");
    tab.setAttribute("role", "tab");
    expect(isSafeForSingleKey(tab)).toBe(true);
    const chip = document.createElement("div");
    chip.setAttribute("role", "button");
    chip.setAttribute("tabindex", "0");
    expect(isSafeForSingleKey(chip)).toBe(true);
    const slider = document.createElement("span");
    slider.setAttribute("role", "slider");
    expect(isSafeForSingleKey(slider)).toBe(false);
    expect(isSafeForSingleKey(document.createElement("select"))).toBe(false);
    const menu = document.createElement("ul");
    menu.setAttribute("role", "menu");
    const item = document.createElement("li");
    menu.append(item);
    document.body.append(menu);
    expect(isSafeForSingleKey(item)).toBe(false);
    menu.remove();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const inDialog = document.createElement("button");
    dialog.append(inDialog);
    document.body.append(dialog);
    expect(isSafeForSingleKey(inDialog)).toBe(false);
    dialog.setAttribute("data-fa-shortcut-sheet", "");
    expect(isSafeForSingleKey(inDialog)).toBe(true);
    dialog.remove();
    const div = document.createElement("div");
    expect(isSafeForSingleKey(div)).toBe(true);
  });

  it("allows a focused grid cell (R3: always), never its editor", () => {
    const grid = document.createElement("div");
    grid.className = "ag-root-wrapper";
    const cell = document.createElement("div");
    cell.setAttribute("role", "gridcell");
    cell.setAttribute("tabindex", "-1");
    const editor = document.createElement("input");
    grid.append(cell, editor);
    document.body.append(grid);
    expect(isInGrid(cell)).toBe(true);
    expect(isSafeForSingleKey(cell)).toBe(true);
    expect(isSafeForSingleKey(cell, { allowGrid: true })).toBe(true);
    expect(isSafeForSingleKey(editor, { allowGrid: true })).toBe(false);
    grid.remove();
  });

  it("detects command modifiers but not shift", () => {
    expect(hasCommandModifier({ metaKey: true })).toBe(true);
    expect(hasCommandModifier({ shiftKey: true })).toBe(false);
  });
});

describe("iterate r3 G3 (P4): arrows and paging", () => {
  const key = (k) => new KeyboardEvent("keydown", { key: k });
  it("↓ / ↑ move wherever J / K do; ← / → no longer do", () => {
    ["list", "leaderboard", "results", "drawer", "grid", "row"].forEach(
      (surface) => {
        expect(actionForEvent(key("ArrowDown"), surface)).toBe("next");
        expect(actionForEvent(key("ArrowUp"), surface)).toBe("prev");
        expect(actionForEvent(key("ArrowRight"), surface)).not.toBe("next");
        expect(actionForEvent(key("ArrowLeft"), surface)).not.toBe("prev");
      },
    );
  });
  it("keeps the Matrix cell moves", () => {
    expect(actionForEvent(key("ArrowRight"), "matrix")).toBe("cellRight");
    expect(actionForEvent(key("ArrowDown"), "matrix")).toBe("cellDown");
  });
  it("pages on results and the drawer", () => {
    expect(actionForEvent(key("Home"), "results")).toBe("first");
    expect(actionForEvent(key("End"), "drawer")).toBe("last");
    expect(actionForEvent(key("PageDown"), "results")).toBe("pageDown");
    expect(actionForEvent(key("PageUp"), "drawer")).toBe("pageUp");
  });
});

// L7c g1 (sheet:shortcuts): the sheet says what each key does there.
describe("the sheet matches the keys (L7c g1)", () => {
  const row = (surface, action) =>
    shortcutRows(surface).find((r) => r.action === action);
  it("lists no Peek on the Data grid (Space selects the row there)", () => {
    expect(row("grid", "peek")).toBeUndefined();
    expect(row("grid", "select")?.caps).toEqual(["X"]);
  });
  it("says a page on the results grid and ten rows in the drawer", () => {
    expect(row("results", "pageDown")?.description).toBe("One page down");
    expect(row("results", "pageUp")?.description).toBe("One page up");
    expect(row("drawer", "pageDown")?.description).toBe("Ten rows down");
  });
  it("says C on results copies the view's link", () => {
    expect(row("results", "copyLink")?.description).toBe(
      "Copy link to this view",
    );
    expect(row("drawer", "copyLink")?.description).toBe("Copy link");
  });
});
