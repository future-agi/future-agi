import { describe, expect, it } from "vitest";
import { VOCAB, plural } from "../vocab";
import { datasetKindOf } from "../Datasets/datasetKind";

describe("vocab", () => {
  it("one vocabulary", () => {
    expect(VOCAB).toMatchObject({
      row: "row",
      rows: "rows",
      run: "run",
      eval: "eval",
      taskBadge: "Task",
      conversationBadge: "Conversation",
      checkChip: "Check",
      judgeChip: "Judge",
      // No status words (options-truth L2, S1): the key stays, empty.
      preview: "",
    });
  });
  it("plural", () => {
    expect(plural(1, "row")).toBe("1 row");
    expect(plural(248, "row")).toBe("248 rows");
    expect(plural(1234, "run")).toBe("1,234 runs");
    expect(plural(0, "eval")).toBe("0 evals");
    expect(plural(2, "entry", "entries")).toBe("2 entries");
    expect(plural(undefined, "row")).toBe("0 rows");
  });
});

describe("datasetKindOf", () => {
  it("defaults to rows", () => {
    expect(datasetKindOf({ kind: "task_suite" })).toBe("task_suite");
    expect(datasetKindOf({ kind: "conversations" })).toBe("conversations");
    expect(datasetKindOf({})).toBe("rows");
    expect(datasetKindOf({ kind: "weird" })).toBe("rows");
    expect(datasetKindOf(null)).toBe("rows");
  });
});
