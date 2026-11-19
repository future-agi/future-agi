import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {} },
}));

import {
  FIXED_PRESET,
  LEGACY_DAY,
  askSeed,
  buildRerunPayload,
  buildRunPayload,
  countedScope,
  describeFilter,
  editableScope,
  fixedScope,
  isFixedScope,
  lastScope,
  reanchorScope,
  sampleSentence,
  savedDateOption,
  scopeDateLabel,
  shownScopeLabel,
  spreadPick,
  storedScope,
  toPopulation,
} from "../insightsScope";
import {
  defaultScope,
  presetWindow,
  windowItem,
  withFilters,
} from "src/sections/projects/observeScope";

const STATUS = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};
const NOW = new Date("2026-10-15T12:00:00Z");
const RANGE = ["2026-10-01T00:00:00.000Z", "2026-10-02T00:00:00.000Z"];

describe("scopes", () => {
  it("stores a preset scope with its window as a created_at filter", () => {
    const scope = withFilters(defaultScope("sessions", NOW), [STATUS]);
    const window = presetWindow("7D", NOW);
    expect(storedScope(scope)).toEqual({
      filters: [STATUS, windowItem(window)],
      label: "Past 7D · 1 filter",
      preset: "7D",
      window,
    });
    expect(countedScope({ ...scope, search: "ada" })).toEqual({
      filters: [STATUS, windowItem(window)],
      search: "ada",
    });
  });

  it("reads a saved scope back for the bar, its preset moved to now", () => {
    const later = new Date("2026-10-20T08:00:00Z");
    const saved = storedScope(
      withFilters(defaultScope("sessions", NOW), [STATUS]),
    );
    expect(editableScope(saved, "sessions", later)).toEqual({
      unit: "sessions",
      filters: [STATUS],
      dateOption: "7D",
      window: presetWindow("7D", later),
      label: "Past 7D · 1 filter",
    });
    const custom = editableScope(
      { filters: [windowItem(RANGE)], preset: "Custom" },
      "traces",
      later,
    );
    expect(custom).toMatchObject({ dateOption: "Custom", window: RANGE });
    expect(reanchorScope(custom, later)).toBe(custom);
  });

  it("maps the presets of reports saved before Observe's", () => {
    expect(savedDateOption({ preset: "7d" })).toBe("7D");
    expect(savedDateOption({ preset: "30d" })).toBe("30D");
    expect(savedDateOption({ preset: "24h" })).toBe(LEGACY_DAY);
    // A window without a preset is a custom range; no window reads all time.
    expect(savedDateOption({ filters: [windowItem(RANGE)] })).toBe("Custom");
    expect(savedDateOption({ label: "All" })).toBeNull();

    const week = editableScope(
      { preset: "7d", filters: [STATUS, windowItem(RANGE)] },
      "traces",
      NOW,
    );
    expect(week).toMatchObject({
      dateOption: "7D",
      window: presetWindow("7D", NOW),
      label: "Past 7D · 1 filter",
    });
    // "24h" has no Observe match: still the 24 hours ending now.
    const day = editableScope({ preset: "24h" }, "traces", NOW);
    expect(day.window).toEqual([
      "2026-10-14T12:00:00.000Z",
      "2026-10-15T12:00:00.000Z",
    ]);
    expect(scopeDateLabel(day)).toBe("Past 24h");
    const all = editableScope({ label: "All" }, "traces", NOW);
    expect(all.label).toBe("All time");
    expect(scopeDateLabel(all)).toBe("All time");
  });

  it("hands the Ask page a report's scope, never a legacy preset", () => {
    const later = new Date("2026-10-20T08:00:00Z");
    const saved = {
      ...storedScope(withFilters(defaultScope("users", NOW), [STATUS])),
      search: "acme",
    };
    expect(askSeed(saved, "users", later)).toEqual({
      unit: "users",
      filters: [STATUS, windowItem(presetWindow("7D", later))],
      dateOption: "7D",
      window: presetWindow("7D", later),
      search: "acme",
      label: "Past 7D · 1 filter · “acme”",
    });
    // Instant eval keeps Observe presets only: a rolling day is a range.
    expect(askSeed({ preset: "24h" }, "traces", NOW)).toMatchObject({
      dateOption: "Custom",
      window: ["2026-10-14T12:00:00.000Z", "2026-10-15T12:00:00.000Z"],
      label: "Custom range",
    });
    expect(askSeed(fixedScope("12 sessions"), "sessions", NOW)).toBeNull();
    expect(askSeed({ label: "All" }, "sessions", NOW)).toBeNull();
  });

  it("keeps a handed-over set fixed", () => {
    const fixed = fixedScope("46 sessions from Instant eval");
    expect(isFixedScope(fixed)).toBe(true);
    expect(fixed.preset).toBe(FIXED_PRESET);
    expect(lastScope({ scope: {}, latest_run: { scope: fixed } })).toEqual(
      fixed,
    );
  });

  it("describes filters in words", () => {
    expect(describeFilter(STATUS)).toBe("Status is ERROR");
    expect(
      describeFilter({
        display_name: "Did the agent resolve it?",
        filter_config: { col_type: "EVAL_METRIC", filter_value: ["No"] },
      }),
    ).toBe("No · Did the agent resolve it?");
  });
});

describe("payloads", () => {
  const draft = {
    name: "  Failures  ",
    unit: "sessions",
    lens: "failures",
    question: "Where is the agent failing?",
    agentDescription: "Harbor",
    instructions: "",
    sampleSize: 5000,
    attributes: [
      {
        key: "asked_for_human",
        type: "boolean",
        description: "d",
        filter_by: true,
      },
      { key: "score", type: "number", description: "", filter_by: true },
      { key: "", type: "boolean" },
    ],
  };

  it("cleans a population: unique, non-empty ids, optional fields", () => {
    expect(
      toPopulation([
        { id: "a", time: "2026-10-15T00:00:00Z", user_id: "u1" },
        { id: "a", time: "dup" },
        { id: "", time: "x" },
        { id: 42, user_id: null },
      ]),
    ).toEqual([
      { id: "a", time: "2026-10-15T00:00:00Z", user_id: "u1" },
      { id: "42" },
    ]);
  });

  it("builds a new-report request the server reads the rows of", () => {
    const scope = storedScope(defaultScope("sessions", NOW));
    const body = buildRunPayload({ projectId: "p1", draft, scope });
    expect(body).not.toHaveProperty("population");
    expect(
      buildRunPayload({
        projectId: "p1",
        draft,
        scope,
        population: [{ id: "s1" }, { id: "s2" }],
      }),
    ).toEqual({
      project_id: "p1",
      config: {
        name: "Failures",
        unit: "sessions",
        lens: "failures",
        question: "Where is the agent failing?",
        agent_description: "Harbor",
        instructions: "",
        attributes: [
          {
            key: "asked_for_human",
            type: "boolean",
            description: "d",
            filter_by: true,
          },
          { key: "score", type: "number", description: "", filter_by: false },
        ],
        sample_size: 1000,
      },
      scope,
      population: [{ id: "s1" }, { id: "s2" }],
      sample_size: 1000,
    });
  });

  it("builds a rerun request for a saved report", () => {
    expect(
      buildRerunPayload({
        projectId: "p1",
        configId: "c1",
        scope: { label: "All" },
        population: [{ id: "t1", user_id: "u" }],
        sampleSize: 4,
      }),
    ).toEqual({
      project_id: "p1",
      config_id: "c1",
      scope: { label: "All" },
      population: [{ id: "t1", user_id: "u" }],
      sample_size: 10,
    });
  });

  it("asks to rediscover categories with the run, not before it", () => {
    // A refused run (credits, rate limit) then keeps the saved categories.
    expect(
      buildRerunPayload({
        projectId: "p1",
        configId: "c1",
        scope: { label: "All" },
        resetTaxonomy: true,
      }),
    ).toEqual({
      project_id: "p1",
      config_id: "c1",
      scope: { label: "All" },
      reset_taxonomy: true,
    });
    expect(
      buildRerunPayload({ projectId: "p1", configId: "c1", scope: {} }),
    ).not.toHaveProperty("reset_taxonomy");
  });
});

describe("sampleSentence", () => {
  it("says when everything is read", () => {
    expect(
      sampleSentence({ unit: "sessions", total: 108, sampleSize: 300 }),
    ).toBe("Analysing all 108 sessions");
  });

  it("says how large the sample is and how it is drawn", () => {
    // A scope's sample is the server's uniform random draw.
    expect(
      sampleSentence({ unit: "traces", total: 12345, sampleSize: 300 }),
    ).toBe("Analysing a random sample of 300 of 12,345 traces");
    expect(
      sampleSentence({
        unit: "users",
        total: 1000,
        isLowerBound: true,
        sampleSize: 300,
      }),
    ).toBe("Analysing a random sample of 300 of 1,000+ users");
    // Rows handed over are sampled evenly across them.
    expect(
      sampleSentence({
        unit: "traces",
        total: 1200,
        sampleSize: 300,
        handedOver: true,
      }),
    ).toBe("Analysing an even sample of 300 of 1,200 traces");
  });
});

describe("spreadPick", () => {
  const items = Array.from({ length: 100 }, (_, i) => i);

  it("spreads picks over the list", () => {
    expect(spreadPick(items, 3)).toEqual([0, 33, 66]);
  });

  it("moves to other distinct picks on each offset", () => {
    const next = spreadPick(items, 3, 1);
    expect(next).toEqual([16, 49, 82]);
    expect(new Set(spreadPick(items, 3, 7)).size).toBe(3);
  });

  it("returns everything when there are few items", () => {
    expect(spreadPick([1, 2], 3)).toEqual([1, 2]);
  });
});

describe("shownScopeLabel", () => {
  // Local days of this year, so the label carries no year.
  const year = new Date().getFullYear();
  const day = (d, ...time) => new Date(year, 9, d, ...time).toISOString();

  it("names the days a custom range read, keeping the filters", () => {
    expect(
      shownScopeLabel({
        label: "Custom range · 1 filter",
        preset: "Custom",
        window: [day(22), day(23, 23, 59, 59, 999)],
      }),
    ).toBe("Oct 22–23 · 1 filter");
    expect(
      shownScopeLabel({
        label: "Custom range",
        preset: "Custom",
        window: [day(22), day(22, 23, 59, 59, 999)],
      }),
    ).toBe("Oct 22");
  });

  it("names a window ending at midnight by the last day it read", () => {
    expect(
      shownScopeLabel({
        label: "Custom range",
        preset: "Custom",
        window: [day(22), day(23)],
      }),
    ).toBe("Oct 22");
  });

  it("keeps any other scope as stored", () => {
    const window = [day(15), day(22)];
    expect(shownScopeLabel({ label: "Past 7D", preset: "7D", window })).toBe(
      "Past 7D",
    );
    expect(shownScopeLabel({ label: "Custom range", preset: "Custom" })).toBe(
      "Custom range",
    );
    expect(
      shownScopeLabel({
        label: "12 sessions from Instant eval",
        preset: "fixed",
      }),
    ).toBe("12 sessions from Instant eval");
    expect(shownScopeLabel({ label: "All" })).toBe("All");
    expect(shownScopeLabel(undefined)).toBe("");
  });
});
