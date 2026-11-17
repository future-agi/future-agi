import { describe, expect, it } from "vitest";
import { columnCategories, groupPropertiesByColumn } from "../DevelopFilterBox";

describe("filter properties grouped by column (IT-R1 G5 P9)", () => {
  const cols = [
    { id: "c1", name: "id", category: "dataset" },
    { id: "c2", name: "metadata", category: "dataset" },
    { id: "e1", name: "Correctness", category: "evaluation" },
  ];
  const paths = [
    {
      id: "c2::customer.tier",
      name: "metadata.customer.tier",
      category: "dataset",
    },
    { id: "c9::[*].role", name: "conversation[*].role", category: "dataset" },
  ];

  it("names each nested column's group after it and keeps plain columns together", () => {
    const out = groupPropertiesByColumn(cols, paths, { c9: "conversation" });
    expect(out.map((p) => [p.name, p.category])).toEqual([
      ["id", "columns"],
      ["metadata", "metadata"],
      ["metadata.customer.tier", "metadata"],
      ["Correctness", "evaluation"],
      ["conversation[*].role", "conversation"],
    ]);
    expect(out.some((p) => p.category === "dataset")).toBe(false);
    expect(columnCategories(out).map((c) => c.label)).toEqual([
      "All",
      "Columns",
      "metadata",
      "conversation",
      "Evals",
    ]);
  });
});
