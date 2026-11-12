import { describe, expect, it } from "vitest";
import {
  parseAnalyticsQuery,
  serializeAnalyticsQuery,
  refreshAnalyticsQuery,
} from "./queryState";

const now = new Date("2026-11-09T12:00:00.123Z");
const later = new Date(now.getTime() + 600000);

describe("Shareable analytics query", () => {
  it.each(["api_key_id", "user_id"])(
    "round-trips %s for Cost only",
    (group) => {
      const state = parseAnalyticsQuery(
        `cost_group=${group}&usage_group=${group}&errors_group=${group}`,
        now,
      );
      expect(state.groups).toEqual({
        usage: "",
        cost: group,
        errors: "status_code",
      });
      expect(
        parseAnalyticsQuery(serializeAnalyticsQuery(state), later),
      ).toEqual(state);
    },
  );

  it("resolves a rolling preset once and round-trips each supported grouping", () => {
    const state = parseAnalyticsQuery(
      "preset=7d&usage_group=provider&cost_group=service&errors_group=billing_mode",
      now,
    );
    expect(state).toMatchObject({
      preset: "7d",
      start: "2026-11-02T12:00:00.123Z",
      end: now.toISOString(),
      granularity: "hour",
      groups: { usage: "provider", cost: "service", errors: "billing_mode" },
    });
    expect(parseAnalyticsQuery(serializeAnalyticsQuery(state), later)).toEqual(
      state,
    );
    const refreshed = refreshAnalyticsQuery(state, later);
    expect(refreshed.end).toBe(later.toISOString());
    expect(refreshed.groups).toEqual(state.groups);
    expect(Date.parse(refreshed.end) - Date.parse(refreshed.start)).toBe(
      7 * 86400000,
    );
  });

  it("keeps an explicit snapshot fixed on Refresh", () => {
    const state = parseAnalyticsQuery(
      "start=2026-11-01T00:00:00Z&end=2026-11-02T00:00:00Z",
      now,
    );
    expect(state.preset).toBe("");
    expect(refreshAnalyticsQuery(state, later)).toEqual(state);
    expect(
      new URLSearchParams(serializeAnalyticsQuery(state)).has("preset"),
    ).toBe(false);
  });

  it.each([
    "start=not-a-date&end=Infinity",
    "start=2026-11-03T00:00:00Z&end=2026-11-02T00:00:00Z",
    "start=2026-02-30T00:00:00Z&end=2026-03-02T00:00:00Z",
    "start=2026-11-01T00:00:00&end=2026-11-02T00:00:00Z",
    "preset=unknown&usage_group=api_key&cost_group=status_code&errors_group=password&token=do-not-copy",
  ])(
    "canonicalizes malformed input without invalid parameters or a rewrite loop: %s",
    (query) => {
      const state = parseAnalyticsQuery(query, now);
      expect(state).toMatchObject({
        preset: "24h",
        start: "2026-11-08T12:00:00.123Z",
        end: now.toISOString(),
      });
      const canonical = serializeAnalyticsQuery(state);
      expect(canonical).not.toMatch(
        /unknown|api_key|password|token|Infinity|not-a-date/,
      );
      expect(
        serializeAnalyticsQuery(parseAnalyticsQuery(canonical, later)),
      ).toBe(canonical);
    },
  );
});
