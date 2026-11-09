import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {} },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import { observePresetDateFilter } from "../timeWindowPresets";
import {
  CUSTOM,
  datePresetOf,
  defaultScope,
  makeScope,
  presetWindow,
  reanchorScope,
  scopeApiFilters,
  scopeForUnit,
  scopeFromSeed,
  scopeLabel,
  splitWindow,
  viewSeed,
  windowItem,
  withDate,
  withFilters,
} from "../observeScope";

const NOW = new Date(2026, 9, 18, 14, 37);
const LATER = new Date(2026, 9, 25, 9, 5);
const STATUS = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};
const COST = {
  column_id: "total_cost",
  filter_config: {
    filter_type: "number",
    filter_op: "greater_than",
    filter_value: 1,
  },
};
const RANGE = ["2026-10-01T00:00:00.000Z", "2026-10-08T00:00:00.000Z"];

// The created_at item the views send for a date filter.
const viewWindowItem = (key, now) =>
  windowItem(
    observePresetDateFilter(key, now).map((value) =>
      new Date(value).toISOString(),
    ),
  );

describe("a scope's label", () => {
  it("reads like the Observe toolbar", () => {
    expect(scopeLabel({ dateOption: "7D", filters: [STATUS, COST] })).toBe(
      "Past 7D · 2 filters",
    );
    expect(
      scopeLabel({ dateOption: CUSTOM, window: RANGE, filters: [STATUS] }),
    ).toBe("Custom range · 1 filter");
    expect(scopeLabel({ dateOption: "Today", filters: [] })).toBe("Today");
    expect(scopeLabel({ filters: [] })).toBe("All time");
  });

  it("doesn't count the window, and quotes a search", () => {
    expect(
      scopeLabel({ dateOption: "7D", filters: [windowItem(RANGE), STATUS] }),
    ).toBe("Past 7D · 1 filter");
    expect(
      scopeLabel({ dateOption: "7D", filters: [STATUS, COST], search: "acme" }),
    ).toBe("Past 7D · 2 filters · “acme”");
    expect(
      scopeLabel({
        dateOption: "7D",
        search: "a very long customer name, inc",
      }),
    ).toBe("Past 7D · “a very long customer na…”");
  });

  it("names the date as Instant eval keeps it", () => {
    expect(datePresetOf("30D")).toBe("30D");
    expect(datePresetOf(CUSTOM)).toBe(CUSTOM);
    // A legacy report's rolling day reads as the range it resolved to.
    expect(datePresetOf("24h")).toBe(CUSTOM);
    expect(datePresetOf(undefined)).toBeUndefined();
  });
});

describe("windows", () => {
  it("are the views' own preset windows", () => {
    const [item] = scopeApiFilters(
      makeScope({
        unit: "traces",
        dateOption: "7D",
        window: presetWindow("7D", NOW),
      }),
    );
    expect(item).toEqual(viewWindowItem("7D", NOW));
    expect(presetWindow(CUSTOM, NOW)).toBeNull();
  });

  it("split from the other filters and merge back", () => {
    const filters = [STATUS, windowItem(RANGE), COST];
    expect(splitWindow(filters)).toEqual({
      window: RANGE,
      filters: [STATUS, COST],
    });
    const scope = makeScope({ unit: "traces", filters, dateOption: CUSTOM });
    expect(scope.window).toEqual(RANGE);
    expect(scopeApiFilters(scope)).toEqual([STATUS, COST, windowItem(RANGE)]);
  });
});

describe("a hand-over's scope", () => {
  it("opens with the view's preset, resolved to now", () => {
    const seed = viewSeed({
      unit: "users",
      filters: [viewWindowItem("7D", NOW), COST],
      dateFilter: { dateOption: "7D" },
    });
    // The Users view's date option is "7D"; the label says it in full.
    expect(seed.label).toBe("Past 7D · 1 filter");
    expect(seed.dateOption).toBe("7D");
    const scope = scopeFromSeed(seed, "users", NOW);
    expect(scope).toMatchObject({
      unit: "users",
      dateOption: "7D",
      filters: [COST],
      label: "Past 7D · 1 filter",
    });
    expect(scopeApiFilters(scope)).toEqual([COST, viewWindowItem("7D", NOW)]);
    // Opened again a week later, "Past 7D" means the week before then.
    expect(scopeFromSeed(seed, "users", LATER).window).toEqual(
      presetWindow("7D", LATER),
    );
  });

  it("keeps a custom range, and starts from the past week without one", () => {
    const custom = scopeFromSeed(
      { filters: [windowItem(RANGE)], dateOption: CUSTOM },
      "sessions",
      NOW,
    );
    expect(custom).toMatchObject({ dateOption: CUSTOM, window: RANGE });
    const bare = scopeFromSeed({ label: "Refunds" }, "sessions", NOW);
    expect(bare).toMatchObject({
      dateOption: "7D",
      window: presetWindow("7D", NOW),
      label: "Past 7D",
    });
    expect(defaultScope("traces", NOW)).toEqual({ ...bare, unit: "traces" });
  });

  it("keeps a Users search only for users", () => {
    const seed = viewSeed({
      unit: "users",
      filters: [viewWindowItem("30D", NOW)],
      dateFilter: { dateOption: "30D" },
      search: "  ada ",
    });
    expect(seed.search).toBe("ada");
    expect(scopeFromSeed(seed, "users", NOW).search).toBe("ada");
    expect(scopeFromSeed(seed, "sessions", NOW).search).toBeUndefined();
  });
});

describe("edits", () => {
  it("relabel the scope", () => {
    const scope = defaultScope("traces", NOW);
    const filtered = withFilters(scope, [STATUS]);
    expect(filtered.label).toBe("Past 7D · 1 filter");
    const custom = withDate(filtered, CUSTOM, [
      "2026-10-01 00:00:00",
      "2026-10-08 00:00:00",
    ]);
    expect(custom.label).toBe("Custom range · 1 filter");
    expect(custom.window).toEqual(
      ["2026-10-01 00:00:00", "2026-10-08 00:00:00"].map((value) =>
        new Date(value).toISOString(),
      ),
    );
  });

  it("keep the date when the unit changes and say which filters went", () => {
    const traces = withFilters(defaultScope("traces", NOW), [
      STATUS,
      {
        column_id: "span_name",
        filter_config: { filter_op: "equals", filter_value: "x" },
      },
    ]);
    const spans = scopeForUnit(
      { ...traces, filters: [STATUS], unit: "spans" },
      "traces",
    );
    expect(spans.scope.filters).toEqual([STATUS]);
    expect(spans.notice).toBeNull();

    const sessions = scopeForUnit(traces, "sessions");
    expect(sessions.scope).toMatchObject({
      unit: "sessions",
      dateOption: "7D",
      window: traces.window,
      filters: [],
      label: "Past 7D",
    });
    expect(sessions.notice).toBe(
      "2 filters don't apply to sessions, so they were removed. The date range stays.",
    );

    const users = { ...defaultScope("users", NOW), search: "ada" };
    expect(scopeForUnit(users, "traces")).toMatchObject({
      scope: { unit: "traces", label: "Past 7D" },
      notice:
        "The search doesn't apply to traces, so it was removed. The date range stays.",
    });
    expect(scopeForUnit(users, "traces").scope.search).toBeUndefined();
  });
});

describe("a scope's next run", () => {
  it("moves a preset to now and leaves a custom range", () => {
    const week = defaultScope("traces", NOW);
    const moved = reanchorScope(week, LATER);
    expect(moved.window).toEqual(presetWindow("7D", LATER));
    expect(reanchorScope(week, NOW)).toBe(week);
    const custom = makeScope({
      unit: "traces",
      dateOption: CUSTOM,
      window: RANGE,
    });
    expect(reanchorScope(custom, LATER)).toBe(custom);
  });
});
