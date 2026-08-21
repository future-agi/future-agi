import { describe, expect, it } from "vitest";

import {
  AGGREGATION_POLL_INITIAL_DELAY_MS,
  AGGREGATION_POLL_MAX_DELAY_MS,
  ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  AUTOMATION_RULE_LIST_PAGE_SIZE,
  FILTER_VALUE_MIN_VISIBLE_RESULTS,
  FILTER_VALUE_PAGE_SIZE,
  FILTER_AUTO_APPLY_DEBOUNCE_MS,
  formatRuntimeSeconds,
  INTERACTIVE_MAX_PAGE_SIZE,
  INTERACTIVE_TABLE_PAGE_SIZE,
  OBSERVE_PROJECT_PAGE_SIZE,
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
  PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
  PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS,
  PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
  PROPERTY_CATALOG_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
  PROPERTY_CATALOG_STALE_TIME_MS,
  readBoundedRuntimeInteger,
} from "./runtime_limits";

const options = {
  minimum: 1,
  maximum: 100,
};

describe("readBoundedRuntimeInteger", () => {
  it("prefers a valid runtime override over the build value", () => {
    expect(
      readBoundedRuntimeInteger("LIMIT", 10, {
        ...options,
        runtimeConfig: { LIMIT: "25" },
        envConfig: { LIMIT: "20" },
      }),
    ).toBe(25);
  });

  it("uses a valid build override when runtime config is absent", () => {
    expect(
      readBoundedRuntimeInteger("LIMIT", 10, {
        ...options,
        runtimeConfig: {},
        envConfig: { LIMIT: "20" },
      }),
    ).toBe(20);
  });

  it.each(["", "not-a-number", "101"])(
    "uses a valid build override when runtime value %s is unusable",
    (runtimeValue) => {
      expect(
        readBoundedRuntimeInteger("LIMIT", 10, {
          ...options,
          runtimeConfig: { LIMIT: runtimeValue },
          envConfig: { LIMIT: "20" },
        }),
      ).toBe(20);
    },
  );

  it.each(["not-a-number", "1.5", "0", "101"])(
    "falls back for unsafe value %s",
    (value) => {
      expect(
        readBoundedRuntimeInteger("LIMIT", 10, {
          ...options,
          runtimeConfig: { LIMIT: value },
          envConfig: {},
        }),
      ).toBe(10);
    },
  );

  it("rejects an invalid reviewed default", () => {
    expect(() =>
      readBoundedRuntimeInteger("LIMIT", 101, {
        ...options,
        runtimeConfig: {},
        envConfig: {},
      }),
    ).toThrow("LIMIT has an invalid default");
  });
});

describe("formatRuntimeSeconds", () => {
  it.each([
    [9_000, "9"],
    [9_500, "9.5"],
  ])("formats %i milliseconds from runtime configuration", (value, result) => {
    expect(formatRuntimeSeconds(value)).toBe(result);
  });

  it.each([0, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid duration %s",
    (value) => {
      expect(() => formatRuntimeSeconds(value)).toThrow(RangeError);
    },
  );
});

describe("runtime limit relationships", () => {
  it("keeps frontend request page defaults inside the shared maximum", () => {
    expect([
      PROPERTY_CATALOG_PAGE_SIZE,
      PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
      PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
      INTERACTIVE_TABLE_PAGE_SIZE,
      FILTER_VALUE_PAGE_SIZE,
      AUTOMATION_RULE_LIST_PAGE_SIZE,
      OBSERVE_PROJECT_PAGE_SIZE,
    ]).toEqual(
      expect.arrayContaining([
        expect.any(Number),
        expect.any(Number),
        expect.any(Number),
        expect.any(Number),
      ]),
    );
    expect(
      Math.max(
        PROPERTY_CATALOG_PAGE_SIZE,
        PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
        PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
        INTERACTIVE_TABLE_PAGE_SIZE,
        FILTER_VALUE_PAGE_SIZE,
        AUTOMATION_RULE_LIST_PAGE_SIZE,
        OBSERVE_PROJECT_PAGE_SIZE,
      ),
    ).toBeLessThanOrEqual(INTERACTIVE_MAX_PAGE_SIZE);
    expect(PROPERTY_CATALOG_LEGACY_PAGE_SIZE).toBeLessThanOrEqual(200);
  });

  it("keeps the polling cap at or above its initial delay", () => {
    expect(AGGREGATION_POLL_MAX_DELAY_MS).toBeGreaterThanOrEqual(
      AGGREGATION_POLL_INITIAL_DELAY_MS,
    );
  });

  it("keeps catalog cache and picker targets inside their parent bounds", () => {
    expect(PROPERTY_CATALOG_CACHE_TIME_MS).toBeGreaterThanOrEqual(
      PROPERTY_CATALOG_STALE_TIME_MS,
    );
    expect(FILTER_VALUE_MIN_VISIBLE_RESULTS).toBeLessThanOrEqual(
      FILTER_VALUE_PAGE_SIZE,
    );
    expect(PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(0);
    expect(ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(0);
    expect(FILTER_AUTO_APPLY_DEBOUNCE_MS).toBeGreaterThanOrEqual(0);
    expect(PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS).toBeGreaterThanOrEqual(
      PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
    );
  });
});
