import { describe, it, expect } from "vitest";

import {
  WORKSPACE_TABS,
  GAP_AREA_TO_TAB,
  WORKSPACE_COPY,
} from "../workspace.constants";

describe("workspace tab constants", () => {
  it("declares the tabs in rail order with Overview first and Settings last", () => {
    expect(WORKSPACE_TABS.map((t) => t.id)).toEqual([
      "overview",
      "world",
      "contract",
      "scenarios",
      "evals",
      "runs",
      "versions",
      "settings",
    ]);
  });

  it("gives every tab a label and an icon", () => {
    WORKSPACE_TABS.forEach((tab) => {
      expect(typeof tab.label).toBe("string");
      expect(tab.label.length).toBeGreaterThan(0);
      expect(typeof tab.icon).toBe("string");
      expect(tab.icon.length).toBeGreaterThan(0);
    });
  });

  it("badges the counted tabs with their state key", () => {
    const badged = WORKSPACE_TABS.filter((t) => t.badge);
    expect(badged.map((t) => t.badge)).toEqual(["scenarios", "evals", "runs"]);
  });
});

describe("setup-gap to tab mapping", () => {
  it("routes agent gaps to Overview, sandbox and tool gaps to World, contract and grading gaps to their tabs", () => {
    expect(GAP_AREA_TO_TAB).toEqual({
      Agent: "overview",
      Sandbox: "world",
      Tools: "world",
      World: "world",
      Contract: "contract",
      Grading: "evals",
    });
  });

  it("only maps gaps onto tabs the workspace actually renders", () => {
    const tabIds = WORKSPACE_TABS.map((t) => t.id);
    Object.values(GAP_AREA_TO_TAB).forEach((tabId) => {
      expect(tabIds).toContain(tabId);
    });
  });
});

describe("workspace copy", () => {
  it("exposes the header, menu and empty-state strings", () => {
    ["back", "live", "run", "moreActions", "fork", "forkHint"].forEach((key) => {
      expect(typeof WORKSPACE_COPY[key]).toBe("string");
      expect(WORKSPACE_COPY[key].length).toBeGreaterThan(0);
    });
    ["title", "body", "action"].forEach((key) => {
      expect(typeof WORKSPACE_COPY.notFound[key]).toBe("string");
    });
  });
});
