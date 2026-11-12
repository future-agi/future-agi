import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  clearScenarioSelection,
  getScenarioSelection,
  publishScenarioSelection,
  subscribeScenarioSelection,
} from "../scenarioSelectionBus";

const allMatching = () => ({
  ids: [],
  rows: [],
  count: 40,
  all: true,
  jobId: "job-1",
  predicate: {
    search: "refund",
    filters: { "persona.languages": ["English"], status_not: ["draft"] },
    marked: ["excluded"],
  },
});

beforeEach(() => clearScenarioSelection());
afterEach(() => clearScenarioSelection());

describe("W10-SIMULATE-OTHER-L03 — scenario selection bus", () => {
  it("keeps an all-matching selection's scope: its environment, search, filters and exclusions", () => {
    const seen = [];
    const unsubscribe = subscribeScenarioSelection((s) => seen.push(s));
    publishScenarioSelection(allMatching());
    unsubscribe();

    const current = getScenarioSelection();
    expect(current).toMatchObject({ ids: [], all: true, count: 40, jobId: "job-1" });
    expect(current.predicate).toEqual(allMatching().predicate);
    // Subscribers (the chat chip) get the same scoped state.
    expect(seen.at(-1)).toBe(current);
  });

  it("snapshots the predicate instead of sharing the table's mutable arrays", () => {
    const selection = allMatching();
    publishScenarioSelection(selection);
    selection.predicate.filters["persona.languages"].push("French");
    selection.predicate.marked.push("new-exclusion");
    selection.predicate.search = "changed";

    expect(getScenarioSelection().predicate).toEqual(allMatching().predicate);
  });

  it("carries no predicate for picked rows or a cleared selection", () => {
    publishScenarioSelection({ ids: ["s-1", "s-2"], rows: [] });
    expect(getScenarioSelection()).toMatchObject({ ids: ["s-1", "s-2"], count: 2, all: false });
    expect(getScenarioSelection().predicate).toBeFalsy();

    publishScenarioSelection(allMatching());
    clearScenarioSelection();
    expect(getScenarioSelection()).toMatchObject({ ids: [], count: 0, all: false });
    expect(getScenarioSelection().predicate).toBeFalsy();
  });
});
