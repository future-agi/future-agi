import { describe, it, expect } from "vitest";
import { getGlyphMeta, buildColumnBlocks } from "../evalTaskGrouping";

describe("getGlyphMeta — target_type glyph", () => {
  it("returns the S glyph for span target types (singular + plural)", () => {
    expect(getGlyphMeta("span")?.code).toBe("S");
    expect(getGlyphMeta("spans")?.code).toBe("S");
  });
  it("returns the T glyph for trace target types (singular + plural)", () => {
    expect(getGlyphMeta("trace")?.code).toBe("T");
    expect(getGlyphMeta("traces")?.code).toBe("T");
  });
  it("is case-insensitive", () => {
    expect(getGlyphMeta("SPAN")?.code).toBe("S");
  });
  it("returns null for unknown / missing target types", () => {
    expect(getGlyphMeta("session")).toBeNull();
    expect(getGlyphMeta(null)).toBeNull();
    expect(getGlyphMeta(undefined)).toBeNull();
  });
});

describe("buildColumnBlocks", () => {
  const flat = (id) => ({ id, name: id });
  const taskCol = (id, evalTaskName, extra = {}) => ({ id, evalTaskName, ...extra });

  it("returns [] for no columns", () => {
    expect(buildColumnBlocks()).toEqual([]);
    expect(buildColumnBlocks([])).toEqual([]);
  });

  it("emits ungrouped columns as col blocks, preserving order", () => {
    const cols = [flat("a"), flat("b")];
    expect(buildColumnBlocks(cols)).toEqual([
      { type: "col", col: cols[0] },
      { type: "col", col: cols[1] },
    ]);
  });

  it("groups columns sharing an evalTaskName into a single task block", () => {
    const c1 = taskCol("e1", "T1", { rowType: "span" });
    const c2 = taskCol("e2", "T1");
    const blocks = buildColumnBlocks([c1, c2]);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      type: "task",
      group: { taskName: "T1", rowType: "span", evals: [c1, c2] },
    });
  });

  it("emits the task block at its first eval's position and folds later evals in", () => {
    const a = flat("a");
    const t1 = taskCol("e1", "T1");
    const b = flat("b");
    const t2 = taskCol("e2", "T1");
    const blocks = buildColumnBlocks([a, t1, b, t2]);
    expect(blocks.map((x) => x.type)).toEqual(["col", "task", "col"]);
    expect(blocks[0].col).toBe(a);
    expect(blocks[1].group.evals).toEqual([t1, t2]);
    expect(blocks[2].col).toBe(b);
  });

  it("keeps multiple tasks in first-seen order", () => {
    const blocks = buildColumnBlocks([
      taskCol("e1", "T1"),
      taskCol("e2", "T2"),
      taskCol("e3", "T1"),
    ]);
    expect(blocks.map((x) => x.group.taskName)).toEqual(["T1", "T2"]);
    expect(blocks[0].group.evals.map((c) => c.id)).toEqual(["e1", "e3"]);
  });

  it("derives rowType from rowType ?? targetType and createdAt from evalTaskCreatedAt", () => {
    const viaTarget = buildColumnBlocks([taskCol("e1", "T1", { targetType: "trace" })]);
    expect(viaTarget[0].group.rowType).toBe("trace");
    const rowTypeWins = buildColumnBlocks([
      taskCol("e2", "T2", { rowType: "span", targetType: "trace" }),
    ]);
    expect(rowTypeWins[0].group.rowType).toBe("span");
    const bare = buildColumnBlocks([taskCol("e3", "T3")]);
    expect(bare[0].group).toMatchObject({ rowType: null, createdAt: null });
    const withCreated = buildColumnBlocks([
      taskCol("e4", "T4", { evalTaskCreatedAt: "2026-01-01" }),
    ]);
    expect(withCreated[0].group.createdAt).toBe("2026-01-01");
  });

  it("skips null column entries", () => {
    expect(buildColumnBlocks([null, flat("a"), null])).toEqual([
      { type: "col", col: { id: "a", name: "a" } },
    ]);
  });
});
