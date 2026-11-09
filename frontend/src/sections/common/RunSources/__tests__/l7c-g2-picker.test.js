// L7c g2 · step-1 picker: the search index covers every way step 1 offers
// (picker:search, picker:search-no-match), the recommended card is first
// with one sentence (picker:recommended), keys follow what is shown.
import { describe, expect, it } from "vitest";
import {
  orderedCards,
  recommendedFromRuns,
  searchStepCards,
  stepCardsFor,
  stepWayCount,
  stepWayNames,
  STEP1_COPY,
} from "../catalog";

const names = {
  prompts: [
    { name: "Support Reply", model: "gpt-4o-mini" },
    { name: "Policy Answer", model: "claude-sonnet-4-5" },
  ],
  graphs: [{ name: "Support Agent" }],
};

describe("picker:search · the step-1 index", () => {
  it("finds Prompt and Agent graph by title, alias, name and model", () => {
    expect(searchStepCards("prompt", names).map((h) => h.card)).toEqual([
      "prompt",
    ]);
    expect(searchStepCards("graph", names).map((h) => h.card)).toEqual([
      "agent",
    ]);
    expect(searchStepCards("Support Reply", names)).toEqual([
      { card: "prompt", why: "prompt Support Reply" },
    ]);
    expect(searchStepCards("gpt-4o-mini", names)).toEqual([
      { card: "prompt", why: "model gpt-4o-mini (Support Reply)" },
    ]);
    expect(searchStepCards("Support Agent", names)).toEqual([
      { card: "agent", why: "graph Support Agent" },
    ]);
    expect(searchStepCards("running", names).map((h) => h.card)).toEqual([
      "running",
    ]);
  });
  it("junk names nothing, so the no-match line lists all five ways", () => {
    expect(searchStepCards("zzqx", names)).toEqual([]);
    expect(stepWayNames()).toEqual([
      "Prompt",
      "Agent graph",
      "HTTP endpoint",
      "A2A agent card",
      "MCP server tool",
    ]);
    expect(stepWayCount()).toBe(5);
    expect(STEP1_COPY.searchPlaceholder(stepWayCount())).toBe(
      "Search 5 ways to run, prompts, graphs, models",
    );
  });
});

describe("picker:recommended · one badge, on card 1", () => {
  it("puts the door's card first; keys follow the shown order", () => {
    expect(orderedCards("task_suite", "prompt").map((c) => c.id)).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    expect(orderedCards("rows", null).map((c) => c.id)).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    const { cards, rec } = stepCardsFor("task_suite", {
      card: "running",
      reason: "x",
    });
    expect(cards[0].id).toBe("running");
    expect(rec.card).toBe("running");
    expect(stepCardsFor("task_suite").rec.card).toBe("agent");
  });
  it("re-test and baseline reasons are one sentence each", () => {
    const runs = {
      baseline: { run_id: "a", label: "#1A" },
      results: [{ run_id: "a", label: "#1A", kind: "prompt" }],
    };
    expect(recommendedFromRuns(runs).reason).toBe(
      "Your baseline #1A is a prompt",
    );
    expect(
      recommendedFromRuns(runs, { label: "#1A", kind: "prompt" }).reason,
    ).toBe("You are re-testing #1A, a prompt");
  });
});
