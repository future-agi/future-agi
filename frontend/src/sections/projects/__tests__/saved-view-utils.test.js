import { describe, expect, it } from "vitest";

import {
  dateBoundsEqual,
  dateFilterEqual,
  filtersContentEqual,
} from "../saved-view-utils";

const filter = (overrides = {}) => ({
  column_id: "latency_ms",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "number",
    filter_op: "greater_than",
    filter_value: 100,
  },
  ...overrides,
});

describe("saved-view-utils", () => {
  it("compares canonical saved-view filters deeply", () => {
    expect(filtersContentEqual([filter()], [filter()])).toBe(true);
    expect(
      filtersContentEqual(
        [filter()],
        [
          filter({
            filter_config: { ...filter().filter_config, filter_value: 200 },
          }),
        ],
      ),
    ).toBe(false);
  });

  it("does not treat legacy camelCase filter payloads as equivalent", () => {
    const legacy = {
      columnId: "latency_ms",
      filterConfig: {
        colType: "SYSTEM_METRIC",
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 100,
      },
    };

    expect(filtersContentEqual([filter()], [legacy])).toBe(false);
  });

  it("detects identity-only changes for the same native column", () => {
    const systemModel = filter({
      column_id: "model",
      property_id: "system_attribute:sessions:model",
    });
    const customModel = filter({
      column_id: "model",
      property_id: "custom_attribute:model",
    });

    expect(filtersContentEqual([systemModel], [customModel])).toBe(false);
    expect(filtersContentEqual([systemModel], [{ ...systemModel }])).toBe(true);
  });

  it("keeps the explicit no-property-id legacy fallback", () => {
    expect(filtersContentEqual([filter()], [filter()])).toBe(true);
    expect(
      filtersContentEqual(
        [filter()],
        [filter({ property_id: "system_attribute:traces:latency_ms" })],
      ),
    ).toBe(false);
  });

  describe("dateBoundsEqual", () => {
    const range = ["2026-09-01 00:00:00", "2026-09-10 00:00:00"];

    it("treats identical persisted bounds as equal", () => {
      expect(dateBoundsEqual(range, [...range])).toBe(true);
    });

    it("detects an edited start or end bound", () => {
      expect(dateBoundsEqual(range, ["2026-09-02 00:00:00", range[1]])).toBe(
        false,
      );
      expect(dateBoundsEqual(range, [range[0], "2026-09-12 00:00:00"])).toBe(
        false,
      );
    });

    it("treats missing bounds as an empty pair", () => {
      expect(dateBoundsEqual(undefined, [])).toBe(true);
      expect(dateBoundsEqual(null, undefined)).toBe(true);
      expect(dateBoundsEqual(undefined, range)).toBe(false);
    });
  });

  describe("dateFilterEqual", () => {
    const custom = (dateFilter) => ({ dateOption: "Custom", dateFilter });
    const range = ["2026-09-01 00:00:00", "2026-09-10 00:00:00"];

    it("is false when the option name differs", () => {
      expect(dateFilterEqual({ dateOption: "7D" }, custom(range))).toBe(false);
    });

    it("compares bounds only for a Custom baseline", () => {
      expect(
        dateFilterEqual(
          { dateOption: "7D", dateFilter: range },
          { dateOption: "7D", dateFilter: ["x", "y"] },
        ),
      ).toBe(true);
      expect(dateFilterEqual(custom(range), custom([...range]))).toBe(true);
      expect(
        dateFilterEqual(
          custom(["2026-09-02 00:00:00", "2026-09-12 00:00:00"]),
          custom(range),
        ),
      ).toBe(false);
    });

    it("treats a missing filter on both sides as equal", () => {
      expect(dateFilterEqual(undefined, undefined)).toBe(true);
      expect(dateFilterEqual(undefined, custom(range))).toBe(false);
    });
  });
});
