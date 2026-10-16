import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {} },
}));

import {
  FIXED_PRESET,
  buildRerunPayload,
  buildRunPayload,
  buildScope,
  describeFilter,
  fixedScope,
  isFixedScope,
  presetWindow,
  reanchorScope,
  sampleSentence,
  scopeLabel,
  splitFilters,
  spreadPick,
  toPopulation,
  withCollectedNote,
} from "../insightsScope";

const STATUS = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};
const NOW = new Date("2026-10-15T12:00:00Z");

describe("scopes", () => {
  it("builds a preset scope with its window as a created_at filter", () => {
    const window = presetWindow("24h", NOW);
    const scope = buildScope({ preset: "24h", window, filters: [STATUS] });
    expect(scope.preset).toBe("24h");
    expect(scope.window).toEqual([
      "2026-10-14T12:00:00.000Z",
      "2026-10-15T12:00:00.000Z",
    ]);
    expect(scope.filters).toEqual([
      STATUS,
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: scope.window,
        },
      },
    ]);
    expect(scope.label).toBe("Past 24 hours · 1 filter");
  });

  it("splits a view's date filter from its other filters", () => {
    const scope = buildScope({
      window: ["2026-10-01T00:00:00Z", "2026-10-08T00:00:00Z"],
      filters: [STATUS],
    });
    expect(scope.preset).toBeUndefined();
    const { window, rest } = splitFilters(scope.filters);
    expect(window).toEqual([
      "2026-10-01T00:00:00.000Z",
      "2026-10-08T00:00:00.000Z",
    ]);
    expect(rest).toEqual([STATUS]);
  });

  it("re-anchors a relative window to now and leaves fixed windows alone", () => {
    const old = buildScope({
      preset: "7d",
      window: presetWindow("7d", new Date("2026-09-01T00:00:00Z")),
      filters: [STATUS],
    });
    const moved = reanchorScope(old, NOW);
    expect(moved.window).toEqual([
      "2026-10-08T12:00:00.000Z",
      "2026-10-15T12:00:00.000Z",
    ]);
    expect(splitFilters(moved.filters).rest).toEqual([STATUS]);

    const custom = buildScope({
      window: ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
    });
    expect(reanchorScope(custom, NOW)).toBe(custom);
    const fixed = fixedScope("46 sessions from Instant eval");
    expect(reanchorScope(fixed, NOW)).toBe(fixed);
    expect(isFixedScope(fixed)).toBe(true);
    expect(fixed.preset).toBe(FIXED_PRESET);
  });

  it("labels an unwindowed scope as all time", () => {
    expect(scopeLabel({ filters: [] })).toBe("All time");
  });

  it("notes when a run collected only the newest rows", () => {
    const scope = buildScope({
      preset: "30d",
      window: presetWindow("30d", NOW),
    });
    const noted = withCollectedNote(scope, {
      items: new Array(2000).fill({ id: "x" }),
      total: 12345,
      totalIsLowerBound: false,
      truncated: true,
    });
    expect(noted.label).toBe("Past 30 days · newest 2,000 of 12,345");
    expect(withCollectedNote(scope, { truncated: false })).toBe(scope);
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

  it("builds a new-report request", () => {
    const scope = buildScope({ preset: "7d", window: presetWindow("7d", NOW) });
    const body = buildRunPayload({
      projectId: "p1",
      draft,
      scope,
      population: [{ id: "s1" }, { id: "s2" }],
    });
    expect(body).toEqual({
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
});

describe("sampleSentence", () => {
  it("says when everything is read", () => {
    expect(
      sampleSentence({ unit: "sessions", total: 108, sampleSize: 300 }),
    ).toBe("Analysing all 108 sessions");
  });

  it("says how large the sample is and where it comes from", () => {
    expect(
      sampleSentence({
        unit: "traces",
        total: 12345,
        sampleSize: 300,
        collectLimit: 2000,
      }),
    ).toBe(
      "Analysing an even sample of 300 of 12,345 traces, drawn from the newest 2,000",
    );
    expect(
      sampleSentence({
        unit: "users",
        total: 1000,
        isLowerBound: true,
        sampleSize: 300,
      }),
    ).toBe("Analysing an even sample of 300 of 1,000+ users");
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
