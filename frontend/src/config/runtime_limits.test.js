import { describe, expect, it } from "vitest";

import {
  AGGREGATION_POLL_INITIAL_DELAY_MS,
  AGGREGATION_POLL_MAX_DELAY_MS,
  AGGREGATION_POLL_TIMEOUT_MS,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS,
  AUTOMATION_RULE_LIST_PAGE_SIZE,
  FILTER_VALUE_MIN_VISIBLE_RESULTS,
  FILTER_VALUE_PAGE_SIZE,
  FILTER_AUTO_APPLY_DEBOUNCE_MS,
  formatRuntimeSeconds,
  INTERACTIVE_MAX_PAGE_SIZE,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
  INTERACTIVE_TABLE_PAGE_SIZE,
  MAX_ADD_QUEUE_CONTINUATION_PAGES,
  MAX_ADD_QUEUE_CONTINUATION_WALL_MS,
  OBSERVE_CURSOR_MAX_CHECKPOINTS,
  OBSERVE_GRID_MAX_BLOCKS_IN_CACHE,
  OBSERVE_GRID_MAX_CONCURRENT_REQUESTS,
  OBSERVE_LIST_DEFAULT_PAGE_SIZE,
  OBSERVE_LIST_PAGE_SIZE_OPTIONS,
  OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS,
  OBSERVE_PROJECT_PAGE_SIZE,
  PROPERTY_CATALOG_CACHE_TIME_MS,
  PROPERTY_CATALOG_COMPACT_PAGE_SIZE,
  PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
  PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS,
  PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
  PROPERTY_CATALOG_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES,
  PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
  PROPERTY_CATALOG_STALE_TIME_MS,
  PROPERTY_PICKER_PREFETCH_MARGIN_PX,
  PROPERTY_PICKER_RENDER_BATCH_SIZE,
  readAggregationPollTimeout,
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
  it("rejects a polling override below the configured aggregation request wall", () => {
    expect(
      readAggregationPollTimeout({
        requestTimeoutMs: 60_000,
        runtimeConfig: { VITE_AGGREGATION_POLL_TIMEOUT_MS: "9000" },
        envConfig: { VITE_AGGREGATION_POLL_TIMEOUT_MS: "180000" },
      }),
    ).toBe(180_000);

    expect(
      readAggregationPollTimeout({
        requestTimeoutMs: 60_000,
        runtimeConfig: { VITE_AGGREGATION_POLL_TIMEOUT_MS: "50000" },
        envConfig: { VITE_AGGREGATION_POLL_TIMEOUT_MS: "59000" },
      }),
    ).toBe(120_000);
  });

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

  it("keeps sequential cursor grids bounded in concurrency and retained blocks", () => {
    expect(OBSERVE_GRID_MAX_CONCURRENT_REQUESTS).toBe(1);
    expect(OBSERVE_GRID_MAX_BLOCKS_IN_CACHE).toBeGreaterThanOrEqual(2);
    expect(OBSERVE_GRID_MAX_BLOCKS_IN_CACHE).toBeLessThanOrEqual(10);
    expect(OBSERVE_CURSOR_MAX_CHECKPOINTS).toBeGreaterThan(
      OBSERVE_GRID_MAX_BLOCKS_IN_CACHE,
    );
    expect(OBSERVE_CURSOR_MAX_CHECKPOINTS).toBeLessThanOrEqual(16_384);
    expect(OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS).toBeGreaterThanOrEqual(30_000);
    expect(OBSERVE_LIST_DEFAULT_PAGE_SIZE).toBe(25);
    expect(OBSERVE_LIST_PAGE_SIZE_OPTIONS).toContain(
      OBSERVE_LIST_DEFAULT_PAGE_SIZE,
    );
    expect(Math.max(...OBSERVE_LIST_PAGE_SIZE_OPTIONS)).toBeLessThanOrEqual(
      INTERACTIVE_MAX_PAGE_SIZE,
    );
  });

  it("keeps annotation-queue continuation finite and above one request wall", () => {
    expect(MAX_ADD_QUEUE_CONTINUATION_PAGES).toBeGreaterThanOrEqual(1);
    expect(MAX_ADD_QUEUE_CONTINUATION_PAGES).toBeLessThanOrEqual(1_000);
    expect(MAX_ADD_QUEUE_CONTINUATION_WALL_MS).toBeGreaterThanOrEqual(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
    expect(MAX_ADD_QUEUE_CONTINUATION_WALL_MS).toBeLessThanOrEqual(
      60 * 60 * 1_000,
    );
  });

  it("keeps the exact-job observation wall above one transport attempt", () => {
    expect(AGGREGATION_POLL_TIMEOUT_MS).toBeGreaterThan(
      AGGREGATION_REQUEST_TIMEOUT_MS,
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
    expect(PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES).toBeGreaterThan(0);
    expect(PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES).toBeLessThanOrEqual(4_096);
    expect(ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(0);
    expect(FILTER_AUTO_APPLY_DEBOUNCE_MS).toBeGreaterThanOrEqual(0);
    expect(PROPERTY_PICKER_RENDER_BATCH_SIZE).toBeGreaterThan(0);
    expect(PROPERTY_PICKER_PREFETCH_MARGIN_PX).toBeGreaterThanOrEqual(0);
    expect(PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS).toBeGreaterThanOrEqual(
      PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
    );
  });
});
