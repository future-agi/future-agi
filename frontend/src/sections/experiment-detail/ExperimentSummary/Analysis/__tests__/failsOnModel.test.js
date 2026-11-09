import { describe, expect, it } from "vitest";
import { failsOnModel } from "../failsOnModel";

const row = (id, n, category, cells) => ({
  row_id: id,
  n,
  label: `T${n}`,
  group_values: { category },
  cells,
});

describe("failsOnModel", () => {
  const matrix = {
    rows: [
      row("r1", 1, "refund", {
        a: { trials: [0, 0, 0] },
        b: { trials: [1, 1, 1] },
      }),
      row("r2", 2, "refund", {
        a: { trials: [1, 0, 1] },
        b: { trials: [1, 1, 1] },
      }),
      row("r3", 3, "status", {
        a: { trials: [1, 1, 1] },
        b: { trials: [0, 1, 1] },
      }),
    ],
  };
  it("counts failing tasks per category and links the same rows", () => {
    const [a, b] = failsOnModel(matrix, {
      runIds: ["a", "b"],
      causes: [
        {
          run_id: "a",
          title: "Loses the A- prefix",
          status_label: "Suggested by Falcon",
        },
      ],
    });
    expect(a).toMatchObject({ failing: 2, total: 3, failingIds: ["r1", "r2"] });
    expect(a.categories).toEqual([
      { name: "refund", count: 2, rowIds: ["r1", "r2"] },
    ]);
    expect(a.tasks[0]).toMatchObject({ id: "r1", passed: 0, tries: 3 });
    expect(a.cause.title).toBe("Loses the A- prefix");
    expect(b).toMatchObject({ failing: 1, cause: null });
    expect(b.categories[0].count).toBe(b.categories[0].rowIds.length);
  });
});
