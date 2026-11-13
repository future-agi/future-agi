import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import CustomTraceRenderer from "../CustomTraceRenderer";

// Stub the real popover (react-query / network) so these tests focus on the
// renderer's wiring: which entity it targets, and that a save refreshes the
// server-side grid via the grid api (which the cache invalidation can't do).
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ open, traceId, spanId, entity, onClose, onSuccess }) => (
    <div
      data-testid="add-tags-popover"
      data-open={String(open)}
      data-trace-id={traceId ?? ""}
      data-span-id={spanId ?? ""}
      data-entity={JSON.stringify(entity)}
    >
      {open && (
        <button
          type="button"
          data-testid="popover-close"
          onClick={() => onClose?.()}
        >
          close
        </button>
      )}
      {open && (
        <button
          type="button"
          data-testid="popover-save"
          onClick={() =>
            onSuccess?.(
              {
                tags: [{ name: "sdk" }, { name: "reviewed" }],
                manual_tags: [{ name: "reviewed" }],
                source_tag_names: ["sdk"],
                tag_revision: 4,
              },
              { entity, type: spanId ? "span" : "trace" },
            )
          }
        >
          save
        </button>
      )}
    </div>
  ),
}));

const renderTagsColumn = (overrides = {}) => {
  const refreshServerSide = vi.fn();
  const params = {
    // The grid identifies a column via colDef.context.sourceColumn; the
    // grid-level context carries entityType.
    colDef: { context: { sourceColumn: { id: "tags" } } },
    value: ["production"],
    data: { trace_id: "trace-1", span_id: "span-root" },
    api: { refreshServerSide },
    context: { entityType: "trace" },
    ...overrides,
  };
  const utils = render(<CustomTraceRenderer {...params} />);
  return { ...utils, refreshServerSide };
};

describe("CustomTraceRenderer — tags column", () => {
  it("passes the complete server tag metadata through without reconstructing identity", () => {
    const data = {
      trace_id: "trace-1",
      span_id: "root-1",
      tag_target: {
        expected_version: "18446744073709551614",
        service_name: "",
      },
      tag_revision: 3,
      source_tag_names: ["sdk"],
      manual_tags: [{ name: "reviewed" }],
    };
    renderTagsColumn({ data });
    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-entity",
      JSON.stringify(data),
    );
  });

  it("targets the trace on the trace grid even when the row carries a root span_id", async () => {
    const user = userEvent.setup();
    const { container } = renderTagsColumn();

    await user.click(container.firstChild);

    const popover = screen.getByTestId("add-tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-1");
    expect(popover).toHaveAttribute("data-span-id", "");
  });

  it("targets the span when the grid context is 'span'", async () => {
    const user = userEvent.setup();
    const { container } = renderTagsColumn({
      value: ["latency"],
      data: { trace_id: "trace-1", span_id: "span-9" },
      context: { entityType: "span" },
    });

    await user.click(container.firstChild);

    const popover = screen.getByTestId("add-tags-popover");
    expect(popover).toHaveAttribute("data-span-id", "span-9");
    expect(popover).toHaveAttribute("data-trace-id", "");
  });

  it("refreshes the server-side grid when the popover closes (not mid-edit)", async () => {
    const user = userEvent.setup();
    const { container, refreshServerSide } = renderTagsColumn();

    await user.click(container.firstChild);
    expect(refreshServerSide).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("popover-close"));
    expect(refreshServerSide).toHaveBeenCalledTimes(1);
  });

  it("gives the row the saved tags and revision before the grid refreshes", async () => {
    // The datasource serves a completed page from its cache on refresh, so
    // without this the row keeps the old revision and the next edit is a 409.
    const user = userEvent.setup();
    const data = {
      trace_id: "trace-1",
      span_id: "root-1",
      project_id: "project-1",
      tags: [{ name: "sdk" }],
      manual_tags: [],
      source_tag_names: ["sdk"],
      tag_revision: 3,
      tag_target: {
        project_id: "project-1",
        trace_id: "trace-1",
        span_id: "root-1",
        start_hour: "2026-11-08T10:00:00+00:00",
        observation_type: "span",
        service_name: "svc",
      },
    };
    const refreshCells = vi.fn();
    const refreshServerSide = vi.fn();
    const forEachNode = (visit) => visit({ data });
    const { container } = renderTagsColumn({
      data,
      value: data.tags,
      api: { refreshServerSide, refreshCells, forEachNode },
    });
    await user.click(container.firstChild);
    await user.click(screen.getByTestId("popover-save"));
    await user.click(screen.getByTestId("popover-close"));

    expect(data).toMatchObject({
      tags: [{ name: "sdk" }, { name: "reviewed" }],
      manual_tags: [{ name: "reviewed" }],
      tag_revision: 4,
    });
    expect(refreshCells).toHaveBeenCalledTimes(1);
    expect(refreshServerSide).toHaveBeenCalledTimes(1);
  });
});
