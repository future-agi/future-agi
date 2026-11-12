import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import BulkActionsBar from "../BulkActionsBar";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// This test owns these prop fixtures instead of importing the parent views.
// Register only its glyphs, just as the production owners do before rendering.
for (const name of [
  "mdi:clipboard-list-outline",
  "mdi:compare-horizontal",
  "mdi:folder-plus-outline",
  "solar:play-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

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

  it("labels lower-bound filter selections without claiming an exact total", () => {
    render(
      <BulkActionsBar
        {...defaultProps}
        selectedCount={26}
        selectedCountIsLowerBound
        allMatching
      />,
    );
    expect(screen.getByText("All matching filter (≥26)")).toBeInTheDocument();
    expect(
      screen.queryByText("All 26 matching filter"),
    ).not.toBeInTheDocument();
  });

  it("does not expose single-row actions for a lower-bound count of one", async () => {
    render(
      <BulkActionsBar
        {...defaultProps}
        selectedCount={1}
        selectedCountIsLowerBound
      />,
    );
    fireEvent.click(screen.getByText("Actions"));
    await screen.findByText("Move to dataset");
    expect(screen.queryByText("Annotate")).not.toBeInTheDocument();
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
            icon: "solar:clipboard-list-linear",
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

  it("shows an enabled action's tooltip and the R2 actions (R2)", async () => {
    const onAction = vi.fn();
    render(
      <BulkActionsBar
        {...defaultProps}
        selectedCount={1}
        onAction={onAction}
        actions={[
          {
            id: "compare_traces",
            label: "Compare 2 traces",
            icon: "mdi:compare-horizontal",
            disabled: true,
            disabledReason: "Select exactly 2 traces",
          },
          {
            id: "add_to_dataset",
            label: "Add to dataset",
            icon: "mdi:folder-plus-outline",
            tooltip: "Add to a dataset to run experiments on",
          },
        ]}
      />,
    );
    screen.getByText("Actions").click();
    const compare = await screen.findByRole("menuitem", {
      name: "Compare 2 traces",
    });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    const add = await screen.findByRole("menuitem", { name: "Add to dataset" });
    expect(document.querySelector("[data-missing-icon]")).toBeNull();
    fireEvent.mouseOver(add);
    expect(
      await screen.findByText("Add to a dataset to run experiments on"),
    ).toBeInTheDocument();
    add.click();
    expect(onAction).toHaveBeenCalledWith("add_to_dataset", expect.any(Object));
  });
});

// Item 6: in user mode (user detail > Sessions) Replay Sessions is the first
// bulk action and is disabled with a reason. Keyboard users must still land
// on the menu, reach the item and hear why it is unavailable.
const REPLAY_REASON =
  "Replay needs a project. Open the session from its project to replay it.";

const sessionActions = (replayDisabled) => [
  {
    id: "replay",
    label: "Replay Sessions",
    icon: "solar:play-linear",
    ...(replayDisabled && { disabled: true, disabledReason: REPLAY_REASON }),
  },
  {
    id: "add_to_dataset",
    label: "Add to dataset",
    icon: "mdi:folder-plus-outline",
  },
  {
    id: "annotation-queue",
    label: "Add to annotation queue",
    icon: "mdi:clipboard-list-outline",
  },
];

const openMenu = async (actions, onAction) => {
  render(
    <BulkActionsBar
      selectedCount={1}
      onClearSelection={vi.fn()}
      onAction={onAction}
      actions={actions}
    />,
  );
  fireEvent.click(screen.getByText("Actions"));
  await screen.findByText("Replay Sessions");
  // In tests a glyph the file did not register renders as an image named
  // "Missing offline icon: <name>", which joins the item's accessible name.
  expect(document.querySelector("[data-missing-icon]")).toBeNull();
  return screen.getByRole("menuitem", { name: "Replay Sessions" });
};

describe("BulkActionsBar keyboard access to a disabled action", () => {
  it("focuses the first item, a disabled one, when the menu opens", async () => {
    const replay = await openMenu(sessionActions(true), vi.fn());
    await waitFor(() => expect(replay).toHaveFocus());
    expect(replay).toHaveAttribute("aria-disabled", "true");
  });

  it("reaches the disabled item with ArrowDown and ArrowUp", async () => {
    const replay = await openMenu(sessionActions(true), vi.fn());
    await waitFor(() => expect(replay).toHaveFocus());
    const add = screen.getByRole("menuitem", { name: "Add to dataset" });
    const queue = screen.getByRole("menuitem", {
      name: "Add to annotation queue",
    });

    fireEvent.keyDown(replay, { key: "ArrowDown" });
    expect(add).toHaveFocus();
    fireEvent.keyDown(add, { key: "ArrowUp" });
    expect(replay).toHaveFocus();
    // Wrapping from the last item also lands on the disabled first one.
    fireEvent.keyDown(replay, { key: "ArrowUp" });
    expect(queue).toHaveFocus();
    fireEvent.keyDown(queue, { key: "ArrowDown" });
    expect(replay).toHaveFocus();
  });

  it("describes the focused disabled item with its reason", async () => {
    const replay = await openMenu(sessionActions(true), vi.fn());
    await waitFor(() => expect(replay).toHaveFocus());
    // The reason is the item's description, not its name.
    await waitFor(() =>
      expect(replay).toHaveAccessibleDescription(REPLAY_REASON),
    );
    fireEvent.mouseOver(replay);
    expect(await screen.findByText(REPLAY_REASON)).toBeInTheDocument();
    await waitFor(() =>
      expect(replay).toHaveAccessibleDescription(REPLAY_REASON),
    );
  });

  it("does not run the disabled action on Enter or click", async () => {
    const onAction = vi.fn();
    const replay = await openMenu(sessionActions(true), onAction);
    await waitFor(() => expect(replay).toHaveFocus());
    fireEvent.keyDown(replay, { key: "Enter" });
    replay.click();
    expect(onAction).not.toHaveBeenCalled();
    // The menu stays open on the item.
    expect(replay).toBeInTheDocument();
  });

  it("keeps an enabled Replay Sessions working by keyboard and click", async () => {
    const onAction = vi.fn();
    const replay = await openMenu(sessionActions(false), onAction);
    expect(replay).not.toHaveAttribute("aria-disabled");
    await waitFor(() => expect(replay).toHaveFocus());
    fireEvent.keyDown(replay, { key: "Enter" });
    expect(onAction).toHaveBeenCalledExactlyOnceWith(
      "replay",
      expect.any(Object),
    );
  });

  it("keeps enabled items working when a disabled item comes first", async () => {
    const onAction = vi.fn();
    const replay = await openMenu(sessionActions(true), onAction);
    await waitFor(() => expect(replay).toHaveFocus());
    fireEvent.keyDown(replay, { key: "ArrowDown" });
    const add = screen.getByRole("menuitem", { name: "Add to dataset" });
    expect(add).toHaveFocus();
    expect(add).not.toHaveAttribute("aria-disabled");
    fireEvent.keyDown(add, { key: "Enter" });
    expect(onAction).toHaveBeenCalledExactlyOnceWith(
      "add_to_dataset",
      expect.any(Object),
    );
  });
});
