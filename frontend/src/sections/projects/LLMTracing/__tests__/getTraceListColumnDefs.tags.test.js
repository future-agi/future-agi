import { describe, it, expect } from "vitest";
import { getTraceListColumnDefs } from "../common";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";

// The tags column must stay interactive even when a row has no tags, so the
// "+ Tag" affordance can render. Other columns keep rendering nothing (the
// valueFormatter shows "-") when empty.
describe("getTraceListColumnDefs — tags column cellRendererSelector", () => {
  // Pass the real built colDef so the selector reads the column from
  // colDef.context.sourceColumn, exactly as the grid wires it.
  const select = (col, value) => {
    const colDef = getTraceListColumnDefs(col);
    return colDef.cellRendererSelector({ value, colDef });
  };

  it("renders CustomTraceRenderer for an empty tags cell", () => {
    const result = select({ id: "tags", name: "Tags", isVisible: true }, []);
    expect(result).toEqual({ component: CustomTraceRenderer });
  });

  it("still renders CustomTraceRenderer for a populated tags cell", () => {
    const result = select(
      { id: "tags", name: "Tags", isVisible: true },
      ["production"],
    );
    expect(result).toEqual({ component: CustomTraceRenderer });
  });

  it("renders no component for a non-tags column when empty", () => {
    const result = select(
      { id: "status", name: "Status", isVisible: true },
      null,
    );
    expect(result).toBeNull();
  });
});
