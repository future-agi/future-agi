import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomTraceRenderer from "../CustomTraceRenderer";
import { getTagCellTargetIds } from "../tagTargetIds";

vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ open, spanId, traceId }) =>
    open ? (
      <div
        data-span-id={spanId || ""}
        data-testid="tags-popover"
        data-trace-id={traceId || ""}
      />
    ) : null,
}));

const renderTagColumn = (data) =>
  render(
    <CustomTraceRenderer
      colDef={{ col: { id: "tags", name: "Tags" } }}
      data={data}
      value={["release"]}
    />,
  );

describe("getTagCellTargetIds", () => {
  it("uses trace_id for trace rows", () => {
    expect(getTagCellTargetIds({ trace_id: "trace-1" })).toEqual({
      traceId: "trace-1",
      spanId: undefined,
    });
  });

  it("uses span_id when the row provides it explicitly", () => {
    expect(
      getTagCellTargetIds({ trace_id: "trace-1", span_id: "span-1" }),
    ).toEqual({
      traceId: "trace-1",
      spanId: "span-1",
    });
  });

  it("uses id as the span id when a span row also has a trace id", () => {
    expect(getTagCellTargetIds({ trace_id: "trace-1", id: "span-1" })).toEqual({
      traceId: "trace-1",
      spanId: "span-1",
    });
  });

  it("falls back to id as the trace id when no span identity is present", () => {
    expect(getTagCellTargetIds({ id: "trace-1" })).toEqual({
      traceId: "trace-1",
      spanId: undefined,
    });
  });
});

describe("CustomTraceRenderer", () => {
  it("threads trace row ids into editable tag cells", () => {
    renderTagColumn({ trace_id: "trace-123", project_id: "project-1" });

    fireEvent.click(screen.getByText("release"));

    const popover = screen.getByTestId("tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-123");
    expect(popover).toHaveAttribute("data-span-id", "");
  });

  it("threads span row ids into editable tag cells", () => {
    renderTagColumn({
      trace_id: "trace-123",
      span_id: "span-456",
      project_id: "project-1",
    });

    fireEvent.click(screen.getByText("release"));

    const popover = screen.getByTestId("tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-123");
    expect(popover).toHaveAttribute("data-span-id", "span-456");
  });
});
