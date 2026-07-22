import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BulkActionsBar from "../BulkActionsBar";

describe("BulkActionsBar", () => {
  const defaultProps = {
    selectedCount: 3,
    onClearSelection: vi.fn(),
    onAction: vi.fn(),
    isSimulator: false,
  };

  it("renders nothing when selectedCount is 0", () => {
    const { container } = render(
      <BulkActionsBar {...defaultProps} selectedCount={0} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders selection count", () => {
    render(<BulkActionsBar {...defaultProps} />);
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });

  it("renders filter-mode label when allMatching is true", () => {
    render(
      <BulkActionsBar {...defaultProps} selectedCount={700} allMatching />,
    );
    expect(screen.getByText("All 700 matching filter")).toBeInTheDocument();
    expect(screen.queryByText("700 selected")).not.toBeInTheDocument();
  });

  it("renders Actions button", () => {
    render(<BulkActionsBar {...defaultProps} />);
    expect(screen.getByText("Actions")).toBeInTheDocument();
  });

  it("renders clear (×) button", () => {
    render(<BulkActionsBar {...defaultProps} />);
    // The × button is an IconButton — find it by its position
    const buttons = screen.getAllByRole("button");
    // Should have Actions button + × button
    expect(buttons.length).toBeGreaterThanOrEqual(2);
  });

  it("calls onClearSelection when × is clicked", () => {
    const onClear = vi.fn();
    render(<BulkActionsBar {...defaultProps} onClearSelection={onClear} />);
    // Last button is the × clear button
    const buttons = screen.getAllByRole("button");
    const clearButton = buttons[buttons.length - 1];
    clearButton.click();
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("opens dropdown menu when Actions is clicked", async () => {
    render(<BulkActionsBar {...defaultProps} />);
    screen.getByText("Actions").click();
    // Menu items should appear (via MUI Menu portal)
    expect(await screen.findByText("Move to dataset")).toBeInTheDocument();
    expect(screen.getByText("Add tags")).toBeInTheDocument();
    expect(screen.getByText("Add to annotation queue")).toBeInTheDocument();
  });

  it("hides Annotate when more than one row is selected", async () => {
    render(<BulkActionsBar {...defaultProps} selectedCount={3} />);
    screen.getByText("Actions").click();
    await screen.findByText("Move to dataset");
    expect(screen.queryByText("Annotate")).not.toBeInTheDocument();
  });

  it("shows Annotate when exactly one row is selected", async () => {
    render(<BulkActionsBar {...defaultProps} selectedCount={1} />);
    screen.getByText("Actions").click();
    expect(await screen.findByText("Annotate")).toBeInTheDocument();
  });

  it("calls onAction with action id when menu item is clicked", async () => {
    const onAction = vi.fn();
    render(<BulkActionsBar {...defaultProps} onAction={onAction} />);
    screen.getByText("Actions").click();
    const menuItem = await screen.findByText("Move to dataset");
    menuItem.click();
    expect(onAction).toHaveBeenCalledWith("dataset", expect.any(Object));
  });

  it("keeps disabled actions visible without firing them", async () => {
    const onAction = vi.fn();
    render(
      <BulkActionsBar
        {...defaultProps}
        onAction={onAction}
        actions={[
          {
            id: "annotation-queue",
            label: "Add to annotation queue",
            icon: "mdi:clipboard-list-outline",
            disabled: true,
            disabledReason: "Selected calls are still in progress.",
          },
        ]}
      />,
    );

    screen.getByText("Actions").click();
    const menuItem = await screen.findByRole("menuitem", {
      name: "Add to annotation queue",
    });

    expect(menuItem).toHaveAttribute("aria-disabled", "true");
    menuItem.click();
    expect(onAction).not.toHaveBeenCalled();
  });
});
