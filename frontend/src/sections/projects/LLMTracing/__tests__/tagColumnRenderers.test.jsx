import { describe, expect, it } from "vitest";
import { getTraceListColumnDefs } from "../common";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";

describe("trace tag column renderers", () => {
  it("keeps the custom renderer active for empty tag cells", () => {
    const colDef = getTraceListColumnDefs({
      id: "tags",
      isVisible: true,
      name: "Tags",
    });

    const renderer = colDef.cellRendererSelector({
      colDef,
      value: [],
    });

    expect(renderer).toEqual({ component: CustomTraceRenderer });
  });

  it("continues to skip renderers for empty non-tag cells", () => {
    const colDef = getTraceListColumnDefs({
      id: "trace_name",
      isVisible: true,
      name: "Trace Name",
    });

    expect(
      colDef.cellRendererSelector({
        colDef,
        value: "",
      }),
    ).toBeNull();
  });
});
