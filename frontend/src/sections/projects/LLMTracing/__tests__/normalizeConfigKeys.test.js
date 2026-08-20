import { describe, it, expect } from "vitest";
import { normalizeConfigKeys } from "../common";

describe("normalizeConfigKeys", () => {
  it("camelCases snake_case keys so is_visible resolves as isVisible", () => {
    const out = normalizeConfigKeys([{ id: "trace_id", is_visible: true }]);
    expect(out[0].isVisible).toBe(true);
  });

  it("preserves id values and converts the rest of the keys", () => {
    expect(
      normalizeConfigKeys([
        { id: "x", output_type: "score", is_visible: false },
      ]),
    ).toEqual([{ id: "x", outputType: "score", isVisible: false }]);
  });

  it("returns undefined for a missing config", () => {
    expect(normalizeConfigKeys(undefined)).toBeUndefined();
  });

  // Column order is the config's order: both grids build their columnDefs in a
  // single ordered pass over this output, so a reshuffle here reorders the grid.
  it("preserves config order, including eval columns among the rest", () => {
    const out = normalizeConfigKeys([
      { id: "name", group_by: null },
      { id: "cfg-a", group_by: "Evaluation Metrics", target_type: "spans" },
      { id: "latency", group_by: null },
      { id: "cfg-b", group_by: "Evaluation Metrics", target_type: "traces" },
    ]);
    expect(out.map((c) => c.id)).toEqual(["name", "cfg-a", "latency", "cfg-b"]);
  });

  // A repeated id would make AG Grid mint a phantom `<id>_1` column, which shows
  // up as a duplicate header rather than an error.
  it("drops a repeated id, keeping the first occurrence and its position", () => {
    const out = normalizeConfigKeys([
      { id: "cfg-a", name: "first" },
      { id: "latency", name: "Latency" },
      { id: "cfg-a", name: "second" },
    ]);
    expect(out.map((c) => c.id)).toEqual(["cfg-a", "latency"]);
    expect(out[0].name).toBe("first");
  });
});
