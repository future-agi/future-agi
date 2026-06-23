import { describe, it, expect } from "vitest";
import { getTraceListColumnDefs } from "../common";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";

// The tags column must stay interactive even when a row has no tags, so the
// "+ Tag" affordance can render. Other columns keep rendering nothing (the
// valueFormatter shows "-") when empty.
describe("getTraceListColumnDefs — tags column cellRendererSelector", () => {
  const select = (col, params) =>
    getTraceListColumnDefs(col).cellRendererSelector(params);

  it("renders CustomTraceRenderer for an empty tags cell", () => {
    const result = select(
      { id: "tags", name: "Tags", isVisible: true },
      { value: [], colDef: { col: { id: "tags" } } },
    );
    expect(result).toEqual({ component: CustomTraceRenderer });
  });

  it("still renders CustomTraceRenderer for a populated tags cell", () => {
    const result = select(
      { id: "tags", name: "Tags", isVisible: true },
      { value: ["production"], colDef: { col: { id: "tags" } } },
    );
    expect(result).toEqual({ component: CustomTraceRenderer });
  });

  it("renders no component for a non-tags column when empty", () => {
    const result = select(
      { id: "status", name: "Status", isVisible: true },
      { value: null, colDef: { col: { id: "status" } } },
    );
    expect(result).toBeNull();
  });
});
