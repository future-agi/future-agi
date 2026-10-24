import { describe, expect, it } from "vitest";
import {
  REQUEST_FILTER_KEYS,
  fromPanelValue,
  requestFilterFields,
  toPanelValue,
} from "./filterPanelAdapter";

describe("request log filter adapter", () => {
  const filters = {
    startedAfter: "2026-01-02T00:00",
    startedBefore: "2026-01-09T10:30",
    model: "gpt-4o, claude-3",
    statusCodeMin: "400",
    statusCodeMax: "499",
    minLatency: "1000",
    maxCost: "0.5",
    userId: "user_1",
    isError: "true",
  };

  it("offers one field per filter, with ranges as a single row", () => {
    const fields = requestFilterFields(["gpt-4o"]);
    expect(fields.map((f) => f.id)).toEqual([
      "started",
      "model",
      "provider",
      "status_code",
      "latency",
      "cost",
      "user_id",
      "session_id",
      "is_error",
      "cache_hit",
      "guardrail_triggered",
    ]);
    expect(fields.find((f) => f.id === "model").options).toEqual(["gpt-4o"]);
  });

  it("round-trips the flat URL filters through the panel", () => {
    const rows = toPanelValue(filters);
    expect(
      rows.map(({ field, operator, value }) => ({ field, operator, value })),
    ).toEqual([
      {
        field: "started",
        operator: "between",
        value: ["2026-01-02T00:00", "2026-01-09T10:30"],
      },
      { field: "model", operator: "in", value: ["gpt-4o", "claude-3"] },
      { field: "status_code", operator: "between", value: ["400", "499"] },
      { field: "latency", operator: "greater_than_or_equal", value: "1000" },
      { field: "cost", operator: "less_than_or_equal", value: "0.5" },
      { field: "user_id", operator: "in", value: ["user_1"] },
      { field: "is_error", operator: "equals", value: ["true"] },
    ]);
    expect(fromPanelValue(rows)).toEqual({
      ...filters,
      model: "gpt-4o,claude-3",
    });
  });

  it("returns nothing for an empty panel and only owns its own keys", () => {
    expect(fromPanelValue([])).toEqual({});
    expect(REQUEST_FILTER_KEYS).not.toContain("view");
    expect(REQUEST_FILTER_KEYS).not.toContain("search");
    expect(REQUEST_FILTER_KEYS).toContain("minLatency");
  });

  it("reads a quick filter (slow requests) as a latency row", () => {
    expect(toPanelValue({ minLatency: "1000" })).toEqual([
      {
        field: "latency",
        fieldType: "number",
        operator: "greater_than_or_equal",
        value: "1000",
      },
    ]);
  });
});
