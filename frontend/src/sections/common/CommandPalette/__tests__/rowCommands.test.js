import { describe, expect, it } from "vitest";
import { rowCommandsOf } from "../providers";

const DS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06";
const T26 = "b6000000-0000-4000-8000-000000000026";
const cols = [{ id: "c1" }];
const rows = [{ row_id: T26, c1: { cell_value: "Damaged blender A-8830" } }];

describe("palette row commands", () => {
  it("a task suite's T26 opens side by side first, then the row", () => {
    const out = rowCommandsOf({
      rows,
      cols,
      n: 26,
      q: "T26",
      kind: "task_suite",
      datasetId: DS,
    });
    expect(out.map((c) => c.label)).toEqual([
      "T26 · Compare runs side by side",
      "T26 · Damaged blender A-8830",
    ]);
    expect(out[0].href).toBe(
      `/dashboard/develop/${DS}/compare/tasks?row=${T26}`,
    );
    expect(out[1].href).toBe(`/dashboard/develop/${DS}?tab=data&row=${T26}`);
  });
  it("other datasets and free-text search keep one command per row", () => {
    expect(
      rowCommandsOf({
        rows,
        cols,
        n: 12,
        q: "Row 12",
        kind: "golden",
        datasetId: DS,
      }).map((c) => c.label),
    ).toEqual(["#12 · Damaged blender A-8830"]);
    expect(
      rowCommandsOf({
        rows,
        cols,
        n: null,
        q: "blender",
        kind: "task_suite",
        datasetId: DS,
      }),
    ).toHaveLength(1);
  });
});
