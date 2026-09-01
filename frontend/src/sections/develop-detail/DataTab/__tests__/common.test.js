import { describe, it, expect, vi } from "vitest";
import {
  enhanceCol,
  getColumnConfig,
  normalizeCompositeResult,
  normalizeEvalResult,
} from "../common";

describe("enhanceCol", () => {
  it("preserves null data_type from snake_case input", () => {
    const avgMeta = [{ id: "col1", metadata: {} }];
    const result = enhanceCol({ id: "col1", data_type: null }, avgMeta);
    expect(result.data_type).toBe(null);
    expect(result.dataType).toBe(null);
  });

  it("returns col unchanged when no metadata match found", () => {
    const col = { id: "col1", data_type: "text" };
    expect(enhanceCol(col, [])).toBe(col);
  });

  it("sets metadata from matching averageMetaData entry", () => {
    const avgMeta = [{ id: "col1", metadata: { min: 0, max: 100 } }];
    const result = enhanceCol({ id: "col1", data_type: "integer" }, avgMeta);
    expect(result.metadata).toEqual({ min: 0, max: 100 });
  });

  it("copies snake_case values to both shapes", () => {
    const avgMeta = [{ id: "col1", metadata: {} }];
    const col = {
      id: "col1",
      data_type: "text",
      origin_type: "dataset",
      is_frozen: true,
      is_visible: false,
      source_id: "src-1",
    };
    const result = enhanceCol(col, avgMeta);
    expect(result.data_type).toBe("text");
    expect(result.dataType).toBe("text");
    expect(result.origin_type).toBe("dataset");
    expect(result.originType).toBe("dataset");
    expect(result.is_frozen).toBe(true);
    expect(result.isFrozen).toBe(true);
    expect(result.is_visible).toBe(false);
    expect(result.isVisible).toBe(false);
    expect(result.source_id).toBe("src-1");
    expect(result.sourceId).toBe("src-1");
  });
});

describe("getColumnConfig valueGetter", () => {
  const mkConfig = (overrides = {}) =>
    getColumnConfig({
      eachCol: { id: "col1", name: "Col 1", data_type: "text" },
      children: undefined,
      queryClient: { invalidateQueries: vi.fn() },
      dataset: "test-ds",
      getWaveSurferInstance: vi.fn(),
      storeWaveSurferInstance: vi.fn(),
      removeWaveSurferInstance: vi.fn(),
      updateWaveSurferInstance: vi.fn(),
      ...overrides,
    });

  it("returns null when cell_value is null", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: { cell_value: null } },
    });
    expect(result).toBe(null);
  });

  it("returns parsed value when cell_value is a string", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: { cell_value: "hello" } },
    });
    expect(result).toBe("hello");
  });

  it("returns undefined when cell_value is missing", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: {} },
    });
    expect(result).toBe(undefined);
  });

  it("returns undefined when cell is absent", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: {},
    });
    expect(result).toBe(undefined);
  });
});

describe("normalizeEvalResult", () => {
  it("unwraps scored choices wrapped in an array", () => {
    const result = normalizeEvalResult(
      [{ score: 0.25, choices: ["Useless", "Non-Toxic"] }],
      "choices",
    );

    expect(result).toMatchObject({ kind: "choices" });
    expect(result.items).toEqual(["Useless", "Non-Toxic"]);
  });
});

describe("normalizeCompositeResult", () => {
  it("degrades to an empty result rather than throwing on a null payload", () => {
    expect(normalizeCompositeResult(null)).toMatchObject({
      children: [],
      totalCount: 0,
      completedCount: 0,
      failedCount: 0,
      hasAggregateScore: false,
      summary: "",
    });
  });

  it("drops a non-array children field instead of rendering it", () => {
    expect(normalizeCompositeResult({ children: "boom" }).children).toEqual([]);
  });

  it("derives counts from the child list when the stored counts are absent", () => {
    const result = normalizeCompositeResult({
      children: [
        { child_id: "a", status: "completed" },
        { child_id: "b", status: "completed" },
        { child_id: "c", status: "failed" },
      ],
    });
    expect(result.totalCount).toBe(3);
    expect(result.completedCount).toBe(2);
    expect(result.failedCount).toBe(1);
  });

  it("prefers the stored counts when they are present", () => {
    const result = normalizeCompositeResult({
      children: [{ child_id: "a", status: "completed" }],
      total_children: 4,
      completed_children: 3,
      failed_children: 1,
    });
    expect(result).toMatchObject({
      totalCount: 4,
      completedCount: 3,
      failedCount: 1,
    });
  });

  it("reports a non-numeric aggregate as absent so it is never formatted", () => {
    expect(normalizeCompositeResult({ aggregate_score: null })).toMatchObject({
      hasAggregateScore: false,
    });
    expect(normalizeCompositeResult({ aggregate_score: "0.5" })).toMatchObject({
      hasAggregateScore: false,
    });
    expect(normalizeCompositeResult({ aggregate_score: 0 })).toMatchObject({
      hasAggregateScore: true,
    });
  });

  it("coerces child reason and error to strings", () => {
    const [child] = normalizeCompositeResult({
      children: [{ child_id: "a", reason: { text: "hi" }, error: null }],
    }).children;
    expect(typeof child.reason).toBe("string");
    expect(child.error).toBe("");
  });

  it("skips non-object entries in the child list", () => {
    expect(
      normalizeCompositeResult({ children: [null, "x", { child_id: "a" }] })
        .children,
    ).toHaveLength(1);
  });
});
