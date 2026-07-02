import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import TagsCell from "../TagsCell";

// Stub the real popover (it depends on react-query / network) so these tests
// stay focused on TagsCell wiring: the cell must open the popover and hand it
// the row identity + current tags.
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ open, traceId, spanId, currentTags, onClose }) => (
    <div
      data-testid="add-tags-popover"
      data-open={String(open)}
      data-trace-id={traceId ?? ""}
      data-span-id={spanId ?? ""}
      data-current-tags={JSON.stringify(currentTags ?? [])}
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

describe("TagsCell", () => {
  it("renders tag chips for an array of strings", () => {
    render(<TagsCell value={["production", "v2"]} />);
    expect(screen.getByText("production")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("shows overflow count for more than 2 tags", () => {
    render(<TagsCell value={["a", "b", "c", "d"]} />);
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.queryByText("c")).not.toBeInTheDocument();
  });

  it("renders nothing for empty array", () => {
    const { container } = render(<TagsCell value={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for null value", () => {
    const { container } = render(<TagsCell value={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for undefined value", () => {
    const { container } = render(<TagsCell value={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders single tag without overflow", () => {
    render(<TagsCell value={["solo"]} />);
    expect(screen.getByText("solo")).toBeInTheDocument();
    expect(screen.queryByText(/\+/)).not.toBeInTheDocument();
  });

  it("opens the tag popover with the trace id when the cell is clicked", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell value={["production"]} traceId="trace-1" />,
    );

    const popover = screen.getByTestId("add-tags-popover");
    expect(popover).toHaveAttribute("data-open", "false");

    await user.click(container.firstChild);

    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-trace-id",
      "trace-1",
    );
    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-current-tags",
      JSON.stringify(["production"]),
    );
  });

  it("threads the span id through to the popover for span rows", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell value={["latency"]} spanId="span-9" />,
    );

    await user.click(container.firstChild);

    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-span-id",
      "span-9",
    );
  });

  it("exposes an add-tag affordance for rows with no tags but a known id", async () => {
    const user = userEvent.setup();
    const { container } = render(<TagsCell value={[]} traceId="trace-2" />);

    expect(screen.getByText(/tag/i)).toBeInTheDocument();

    await user.click(container.firstChild);

    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-trace-id",
      "trace-2",
    );
  });

  it("stays inert (no popover) when neither trace nor span id is available", async () => {
    const user = userEvent.setup();
    const { container } = render(<TagsCell value={["readonly"]} />);

    expect(screen.queryByTestId("add-tags-popover")).not.toBeInTheDocument();

    await user.click(container.firstChild);

    expect(screen.queryByTestId("add-tags-popover")).not.toBeInTheDocument();
  });

  it("stays read-only (no popover) when the role cannot edit tags", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell value={["production"]} traceId="trace-1" canEditTags={false} />,
    );

    // Tags still render, but the cell is not interactive.
    expect(screen.getByText("production")).toBeInTheDocument();
    expect(screen.queryByTestId("add-tags-popover")).not.toBeInTheDocument();

    await user.click(container.firstChild);
    expect(screen.queryByTestId("add-tags-popover")).not.toBeInTheDocument();
  });

  it("tags the trace (not its root span) on the trace grid even when the row carries a span_id", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell
        value={["production"]}
        traceId="trace-1"
        spanId="span-root"
        entityType="trace"
      />,
    );

    await user.click(container.firstChild);

    const popover = screen.getByTestId("add-tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-1");
    // span id is suppressed so the popover patches the trace, not its root span
    expect(popover).toHaveAttribute("data-span-id", "");
  });

  it("tags the span on the span grid when the entity context is 'span'", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell
        value={["latency"]}
        traceId="trace-1"
        spanId="span-9"
        entityType="span"
      />,
    );

    await user.click(container.firstChild);

    const popover = screen.getByTestId("add-tags-popover");
    expect(popover).toHaveAttribute("data-span-id", "span-9");
    expect(popover).toHaveAttribute("data-trace-id", "");
  });

  it("refreshes the grid via onTagsUpdated when the popover closes (not on open)", async () => {
    const user = userEvent.setup();
    const onTagsUpdated = vi.fn();
    const { container } = render(
      <TagsCell
        value={["production"]}
        traceId="trace-1"
        onTagsUpdated={onTagsUpdated}
      />,
    );

    await user.click(container.firstChild);
    // The server-side grid is refreshed on close, not per save — refreshing
    // mid-edit would rebuild the row and snap the still-open popover shut.
    expect(onTagsUpdated).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("popover-close"));
    expect(onTagsUpdated).toHaveBeenCalledTimes(1);
  });

  it("opens the popover via keyboard (Enter) for accessibility", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TagsCell value={["production"]} traceId="trace-1" />,
    );

    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-open",
      "false",
    );

    container.firstChild.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByTestId("add-tags-popover")).toHaveAttribute(
      "data-open",
      "true",
    );
  });
});
