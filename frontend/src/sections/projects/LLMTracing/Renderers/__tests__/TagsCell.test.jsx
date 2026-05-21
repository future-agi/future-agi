import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TagsCell from "../TagsCell";

vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ currentTags, open, spanId, traceId }) =>
    open ? (
      <div
        data-current-tags={JSON.stringify(currentTags)}
        data-span-id={spanId || ""}
        data-testid="tags-popover"
        data-trace-id={traceId || ""}
      />
    ) : null,
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

  it("opens the tag editor for a trace row when a visible tag is clicked", () => {
    render(<TagsCell traceId="trace-1" value={["production", "v2"]} />);

    fireEvent.click(screen.getByText("production"));

    const popover = screen.getByTestId("tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-1");
    expect(popover).toHaveAttribute("data-span-id", "");
    expect(popover).toHaveAttribute(
      "data-current-tags",
      JSON.stringify(["production", "v2"]),
    );
  });

  it("opens the tag editor for a span row when the overflow chip is clicked", () => {
    render(<TagsCell spanId="span-1" value={["a", "b", "c"]} />);

    fireEvent.click(screen.getByText("+1"));

    const popover = screen.getByTestId("tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "");
    expect(popover).toHaveAttribute("data-span-id", "span-1");
  });

  it("renders an add-tag action for empty editable rows", () => {
    render(<TagsCell traceId="trace-empty" value={[]} />);

    fireEvent.click(screen.getByText("+ Tag"));

    const popover = screen.getByTestId("tags-popover");
    expect(popover).toHaveAttribute("data-trace-id", "trace-empty");
    expect(popover).toHaveAttribute("data-current-tags", JSON.stringify([]));
  });
});
