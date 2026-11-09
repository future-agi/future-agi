import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import FilterPanel from "../FilterPanel";

// Mock ComplexFilter since it has complex internal state
vi.mock("src/components/ComplexFilter/ComplexFilter", () => ({
  default: () => <div data-testid="complex-filter">MockComplexFilter</div>,
}));

describe("FilterPanel", () => {
  const defaultProps = {
    anchorEl: document.createElement("button"),
    open: true,
    onClose: vi.fn(),
    filters: [{ id: "1" }],
    setFilters: vi.fn(),
    filterDefinition: [],
    defaultFilter: { id: "default" },
  };

  it("keeps Basic and hides the unbuilt SQL teaser when open", () => {
    render(<FilterPanel {...defaultProps} />);
    expect(screen.getByText("Basic")).toBeInTheDocument();
    expect(screen.queryByText("SQL")).toBeNull();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("renders Add filter button", () => {
    render(<FilterPanel {...defaultProps} />);
    expect(screen.getByText("Add filter")).toBeInTheDocument();
  });

  it("renders Apply button", () => {
    render(<FilterPanel {...defaultProps} />);
    expect(screen.getByText("Apply")).toBeInTheDocument();
  });

  it("renders ComplexFilter inside", () => {
    render(<FilterPanel {...defaultProps} />);
    expect(screen.getByTestId("complex-filter")).toBeInTheDocument();
  });

  it("does not render when open is false", () => {
    render(<FilterPanel {...defaultProps} open={false} />);
    expect(screen.queryByText("Basic")).not.toBeInTheDocument();
  });

  it("calls onClose when Apply is clicked", () => {
    const setFilters = vi.fn();
    const onClose = vi.fn();
    render(
      <FilterPanel
        {...defaultProps}
        setFilters={setFilters}
        onClose={onClose}
      />,
    );
    screen.getByText("Apply").click();
    expect(setFilters).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
