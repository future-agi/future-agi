import { describe, it, expect } from "vitest";

import {
  WORKSPACE_TABS,
  CHIPS_BY_TAB,
  GAP_AREA_TO_TAB,
  WORKSPACE_COPY,
} from "../workspace.constants";

describe("workspace tab constants", () => {
  it("declares the five tabs in order with runs last", () => {
    expect(WORKSPACE_TABS.map((t) => t.id)).toEqual([
      "contract",
      "scenarios",
      "evals",
      "summary",
      "runs",
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

describe("workspace chip prompts", () => {
  it("offers a non-empty suggestion list for every tab", () => {
    Object.values(CHIPS_BY_TAB).forEach((chips) => {
      expect(Array.isArray(chips)).toBe(true);
      expect(chips.length).toBeGreaterThan(0);
    });
  });

  it("is keyed by the tab ids the workspace actually switches on", () => {
    // The chips are looked up as CHIPS_BY_TAB[activeTab]; the summary tab's id
    // is "summary", so an "overview" key served nothing.
    WORKSPACE_TABS.forEach((tab) =>
      expect(Object.keys(CHIPS_BY_TAB)).toContain(tab.id),
    );
  });
});

describe("setup-gap to tab mapping", () => {
  it("routes contract gaps to Contract and grading gaps to Evaluations", () => {
    expect(GAP_AREA_TO_TAB).toEqual({ Contract: "contract", Grading: "evals" });
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

describe("workspace copy honesty", () => {
  it("does not promise the fork resets an agent it copies", () => {
    expect(WORKSPACE_COPY.forkHint).not.toMatch(/Agent \+ runs reset/);
  });

  it("points the run-blocked agent reason at a tab that exists", () => {
    const tabLabels = WORKSPACE_TABS.map((t) => t.label);
    const named = WORKSPACE_COPY.runBlocked.agent.match(/on the (\w+) tab/);
    expect(named && tabLabels).toContain(named?.[1]);
  });
});
