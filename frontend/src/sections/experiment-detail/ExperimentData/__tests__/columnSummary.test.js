import { describe, expect, it } from "vitest";

import {
  buildExperimentPinnedSummaryRow,
  formatColumnSummary,
  getAvailableColumnSummaryTypes,
  getColumnSummaryStats,
  resolveColumnSummaryType,
} from "../columnSummary";

describe("getColumnSummaryStats", () => {
  it("returns null when the column cannot be summarised", () => {
    expect(getColumnSummaryStats(undefined)).toBeNull();
    expect(getColumnSummaryStats({})).toBeNull();
    expect(getColumnSummaryStats({ average_score: null })).toBeNull();
  });

  it("keeps a genuine 0% average", () => {
    expect(getColumnSummaryStats({ average_score: 0 })).toEqual({
      isColumnSummary: true,
      average: 0,
      max: null,
      min: null,
      median: null,
    });
  });

  it("reads snake_case and camelCase score fields", () => {
    expect(
      getColumnSummaryStats({
        averageScore: 50,
        maxScore: 90,
        minScore: 10,
        medianScore: 40,
      }),
    ).toEqual({
      isColumnSummary: true,
      average: 50,
      max: 90,
      min: 10,
      median: 40,
    });
  });
});

describe("formatColumnSummary", () => {
  const stats = {
    isColumnSummary: true,
    average: 50,
    max: 90.5,
    min: 10,
    median: 40,
  };

  it("defaults to Average with two decimal places, matching today", () => {
    expect(formatColumnSummary(stats)).toBe("Average: 50.00%");
  });

  it("names whichever summary is shown", () => {
    expect(formatColumnSummary(stats, "max")).toBe("Maximum: 90.50%");
    expect(formatColumnSummary(stats, "min")).toBe("Minimum: 10.00%");
    expect(formatColumnSummary(stats, "median")).toBe("Median: 40.00%");
  });

  it("falls back to Average when the chosen summary is missing", () => {
    expect(
      formatColumnSummary({ isColumnSummary: true, average: 12.3 }, "max"),
    ).toBe("Average: 12.30%");
  });
});

describe("resolveColumnSummaryType / available types", () => {
  it("lets two columns keep independent choices", () => {
    const colA = {
      average_score: 20,
      max_score: 80,
      min_score: 5,
      median_score: 18,
    };
    const colB = {
      average_score: 40,
      max_score: 99,
      min_score: 1,
      median_score: 33,
    };
    const typeByColumn = { a: "max", b: "min" };

    expect(
      formatColumnSummary(getColumnSummaryStats(colA), typeByColumn.a),
    ).toBe("Maximum: 80.00%");
    expect(
      formatColumnSummary(getColumnSummaryStats(colB), typeByColumn.b),
    ).toBe("Minimum: 1.00%");
  });

  it("only offers summaries that have a value", () => {
    const types = getAvailableColumnSummaryTypes({
      isColumnSummary: true,
      average: 20,
      max: null,
      min: 0,
      median: undefined,
    });
    expect(types.map((item) => item.id)).toEqual(["average", "min"]);
    expect(resolveColumnSummaryType({ average: 20 }, "median")).toBe("average");
  });
});

describe("buildExperimentPinnedSummaryRow", () => {
  it("returns no pinned row when nothing can be summarised", () => {
    expect(buildExperimentPinnedSummaryRow([])).toEqual([]);
    expect(
      buildExperimentPinnedSummaryRow([{ field: "text", col: {} }]),
    ).toEqual([]);
  });

  it("pins stats per summarisable column, including grouped children", () => {
    const result = buildExperimentPinnedSummaryRow([
      { field: "prompt", col: {} },
      {
        children: [
          {
            field: "eval-a",
            col: {
              average_score: 50,
              max_score: 90,
              min_score: 10,
              median_score: 40,
            },
          },
          { field: "eval-a-reason", col: { average_score: null } },
        ],
      },
      {
        field: "eval-b",
        col: {
          averageScore: 12.3,
          maxScore: 20,
          minScore: 1,
          medianScore: 11,
        },
      },
    ]);

    expect(result).toHaveLength(1);
    expect(result[0]["eval-a"]).toEqual({
      isColumnSummary: true,
      average: 50,
      max: 90,
      min: 10,
      median: 40,
    });
    expect(result[0]["eval-b"].average).toBe(12.3);
    expect(result[0].prompt).toBeUndefined();
    expect(result[0]["eval-a-reason"]).toBeUndefined();
  });
});
