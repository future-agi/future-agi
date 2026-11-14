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
      {/* The real popover stays mounted when closed, so a save sent before
          the user closed it is still acknowledged afterwards. */}
      <button
        type="button"
        data-testid="popover-late-ack"
        onClick={() =>
          onSuccess?.(
            {
              tags: [{ name: "sdk" }, { name: "late" }],
              manual_tags: [{ name: "late" }],
              source_tag_names: ["sdk"],
              tag_revision: 4,
            },
            { entity, type: spanId ? "span" : "trace" },
          )
        }
      >
        late ack
      </button>
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

  it("refreshes a grid without a declared row kind when the popover closes (not mid-edit)", async () => {
    const user = userEvent.setup();
    const { container, refreshServerSide } = renderTagsColumn({ context: {} });

    await user.click(container.firstChild);
    expect(refreshServerSide).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("popover-close"));
    expect(refreshServerSide).toHaveBeenCalledTimes(1);
  });

  it.each(["trace", "span"])(
    "does not refresh the Observe %s grid when the popover closes",
    async (entityType) => {
      // A refresh there re-reads cached cursor pages; from page 2 it left a
      // later "page 1" showing page 2's rows.
      const user = userEvent.setup();
      const { container, refreshServerSide } = renderTagsColumn({
        context: { entityType },
      });
      await user.click(container.firstChild);
      await user.click(screen.getByTestId("popover-close"));
      expect(refreshServerSide).not.toHaveBeenCalled();
    },
  );

  it("gives the row the saved tags and revision when the popover closes", async () => {
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
    expect(refreshServerSide).not.toHaveBeenCalled();
  });

  it("applies a save acknowledged after the editor was closed", async () => {
    // Escape while a save is in flight closes the popover first. Without this
    // the row keeps its old tags, so the next edit starts from them and its
    // replacement write drops the tag that was just saved.
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
    await user.click(screen.getByTestId("popover-close"));
    expect(refreshCells).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("popover-late-ack"));

    expect(data).toMatchObject({
      tags: [{ name: "sdk" }, { name: "late" }],
      manual_tags: [{ name: "late" }],
      tag_revision: 4,
    });
    expect(refreshCells).toHaveBeenCalledTimes(1);
    expect(refreshServerSide).not.toHaveBeenCalled();
  });

  it("keeps a save acknowledged while the editor is open for its close", async () => {
    const user = userEvent.setup();
    const refreshCells = vi.fn();
    const forEachNode = vi.fn();
    const { container } = renderTagsColumn({
      api: { refreshServerSide: vi.fn(), refreshCells, forEachNode },
    });
    await user.click(container.firstChild);
    await user.click(screen.getByTestId("popover-late-ack"));
    // Not applied mid-edit: a grid update would remount the open editor.
    expect(forEachNode).not.toHaveBeenCalled();
  });
});
