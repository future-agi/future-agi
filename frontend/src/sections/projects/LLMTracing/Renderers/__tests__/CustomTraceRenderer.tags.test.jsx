import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import CustomTraceRenderer from "../CustomTraceRenderer";

// Stub the real popover (react-query / network) so these tests focus on the
// renderer's wiring: which entity it targets, and that a save refreshes the
// server-side grid via the grid api (which the cache invalidation can't do).
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ open, traceId, spanId, onClose }) => (
    <div
      data-testid="add-tags-popover"
      data-open={String(open)}
      data-trace-id={traceId ?? ""}
      data-span-id={spanId ?? ""}
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
});
