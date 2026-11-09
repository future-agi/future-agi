/* eslint-disable react/prop-types */
// R4 implementation (D18, D25): R4 keys, live-action registration in the `?` sheet,
// `when` entries, and one key resolving to the action the screen binds.
import React from "react";
import fs from "node:fs";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import {
  KEYMAP,
  SURFACES,
  SURFACE_TITLES,
  actionsForEvent,
  shortcutRows,
} from "src/utils/keymap";
import useShortcuts, {
  liveActionsFor,
  useShortcutStore,
} from "../useShortcuts";
import ShortcutSheet from "src/sections/common/RowPanel/ShortcutSheet";

afterEach(() => {
  cleanup();
  useShortcutStore.setState({ stack: [] });
});

const R4_ACTIONS = {
  graphView: ["G", ["taskCompare", "drawer", "paths"]],
  timelineView: ["L", ["taskCompare", "drawer"]],
  inspectNode: ["I", ["taskCompare", "paths"]],
  rerunChanged: ["W", ["results", "leaderboard", "matrix", "drawer"]],
  rerunWithChanges: [
    "⇧R",
    ["leaderboard", "results", "matrix", "taskCompare", "drawer"],
  ],
  lensPlay: ["Space", ["taskCompare"]],
  prevTurn: ["<", ["taskCompare"]],
  nextTurn: [">", ["taskCompare"]],
  blink: ["Q", ["taskCompare"]],
  pickSource: ["1–3", ["newExperiment"]], // L7c g2: the picker offers 3 ways to run
  catalogBack: ["⌫", ["newExperiment"]],
  addToDataset: ["A", ["results"]],
};

describe("R4 keymap (§I5)", () => {
  it("adds every R4 action on its surfaces", () => {
    Object.entries(R4_ACTIONS).forEach(([action, [cap, surfaces]]) => {
      const entry = KEYMAP.find((e) => e.action === action);
      expect(entry, action).toBeTruthy();
      expect(entry.display[0]).toBe(cap);
      surfaces.forEach((s) =>
        expect(entry.surfaces[s], `${action} on ${s}`).toBeTruthy(),
      );
      expect(entry.description).toMatch(/^[A-Z]/);
    });
    // dataset integration FE-0: the Runners page and its shortcuts are gone.
    expect(SURFACES).toEqual(expect.arrayContaining(["paths"]));
    expect(SURFACES).not.toContain("runners");
    expect(SURFACE_TITLES.runners).toBeUndefined();
    expect(SURFACE_TITLES.paths).toBe("Paths");
    const results = shortcutRows("results").map((r) => r.action);
    expect(results).toEqual(
      expect.arrayContaining([
        "select",
        "addToDataset",
        "copyLink",
        "copyOutput",
      ]),
    );
    expect(KEYMAP.find((e) => e.action === "copyCommand")).toBeUndefined();
  });

  it("lists every candidate action for a shared key", () => {
    expect(actionsForEvent({ key: "I" }, "taskCompare")).toEqual([
      "inspect",
      "inspectNode",
    ]);
    expect(actionsForEvent({ key: "c" }, "results")).toEqual([
      "columns",
      "copyLink",
    ]);
    expect(
      actionsForEvent({ key: "R", shiftKey: true }, "leaderboard"),
    ).toEqual(["rerunWithChanges"]);
  });
});

function Screen({ surface, handlers, opts }) {
  useShortcuts(surface, handlers, opts);
  return (
    <button type="button" role="tab">
      tab
    </button>
  );
}

describe("live-action registration (D25)", () => {
  it("Task compare `?` lists D, M, E as live and greys Q with its reason", () => {
    // The handler names Task compare binds today (useWorkspaceActions.js).
    const src = fs.readFileSync(
      `${process.cwd()}/src/sections/task-compare/useWorkspaceActions.js`,
      "utf8",
    );
    ["diffOnly:", "toggleMap:", "expandAllSteps:"].forEach((h) =>
      expect(src).toContain(h),
    );
    const noop = () => {};
    render(
      <Screen
        surface="taskCompare"
        handlers={{
          next: noop,
          prev: noop,
          diffOnly: noop,
          toggleMap: noop,
          expandAllSteps: noop,
          blink: {
            run: noop,
            when: () => false,
            reason: "Needs an image lens",
          },
        }}
      />,
    );
    render(<ShortcutSheet open onClose={() => {}} surface="taskCompare" />);
    const row = (a) => document.querySelector(`[data-shortcut="${a}"]`);
    ["diffOnly", "toggleMap", "expandAllSteps"].forEach((a) => {
      expect(row(a), a).toBeTruthy();
      expect(row(a).getAttribute("data-unavailable")).toBeNull();
    });
    expect(row("blink").getAttribute("data-unavailable")).toBe("true");
    expect(row("blink").textContent).toContain("Needs an image lens");
    // Not bound on this screen → hidden (no dead keys in the sheet).
    expect(row("graphView")).toBeNull();
    expect(row("lensPlay")).toBeNull();
    expect(liveActionsFor("taskCompare").has("diffOnly")).toBe(true);
  });

  it("a `when` that is false blocks the key; one key fires the bound action", () => {
    const blink = vi.fn();
    const inspectNode = vi.fn();
    let image = false;
    render(
      <Screen
        surface="taskCompare"
        handlers={{
          blink: {
            run: blink,
            when: () => image,
            reason: "Needs an image lens",
          },
          inspectNode,
        }}
      />,
    );
    fireEvent.keyDown(document, { key: "q" });
    expect(blink).not.toHaveBeenCalled();
    image = true;
    fireEvent.keyDown(document, { key: "q" });
    expect(blink).toHaveBeenCalledTimes(1);
    // `I` = inspect (unbound here) or inspectNode (bound) → inspectNode.
    fireEvent.keyDown(document, { key: "i" });
    expect(inspectNode).toHaveBeenCalledTimes(1);
  });

  it("keeps natively handled keys (Matrix arrows) and register:false bindings", () => {
    render(<Screen surface="matrix" handlers={{ moreMetrics: () => {} }} />);
    render(
      <Screen
        surface="matrix"
        handlers={{ makeBaseline: () => {} }}
        opts={{ register: false }}
      />,
    );
    const live = liveActionsFor("matrix");
    expect([...live]).toEqual(
      expect.arrayContaining([
        "cellLeft",
        "open",
        "moreMetrics",
        "makeBaseline",
      ]),
    );
    expect(liveActionsFor("global")).toBeNull();
    // The review workspace binds Z / C here and the rest in its own hook.
    render(
      <Screen
        surface="review"
        handlers={{ undo: () => {}, comments: () => {} }}
      />,
    );
    expect([...liveActionsFor("review")]).toEqual(
      expect.arrayContaining([
        "next",
        "prev",
        "submit",
        "skip",
        "notes",
        "undo",
        "comments",
      ]),
    );
  });
});
