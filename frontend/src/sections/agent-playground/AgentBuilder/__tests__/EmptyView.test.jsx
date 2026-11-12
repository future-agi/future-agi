/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import EmptyView from "../EmptyView";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
let mockCanEditAgent = true;
vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({
    canEditAgent: mockCanEditAgent,
    isReadOnly: !mockCanEditAgent,
  }),
}));

// Keeps the element the menu is anchored to, so a test can check that the
// menu opens next to the tile.
let mockPopperAnchorEl;
vi.mock("../../components/NodeSelectionPopper", () => ({
  default: ({ open, anchorEl }) => {
    mockPopperAnchorEl = anchorEl;
    return open ? <div data-testid="node-selection-popper" /> : null;
  },
}));

// The template gallery runs on mock data (data/mockAgentTemplates.js) and
// loads the same unsaveable graph for every template. This sentinel shows up
// if the empty canvas mounts it at all.
vi.mock("../../components/ChooseAgentTemplateDrawer", () => ({
  default: () => <div data-testid="choose-agent-template-drawer" />,
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-icon" />,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const theme = createTheme();
function renderEmptyView() {
  return render(
    <ThemeProvider theme={theme}>
      <EmptyView />
    </ThemeProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("EmptyView — start from a template (AUD-AGENTS-01)", () => {
  beforeEach(() => {
    mockCanEditAgent = true;
  });

  // Nothing is removed: the link stays, turned off, with the reason under it,
  // and the mock gallery it used to open stays unreachable.
  it("shows 'or start from a template' turned off, with 'Not available in this release'", () => {
    renderEmptyView();

    expect(screen.getByText("Add first node")).toBeInTheDocument();
    const link = screen.getByRole("button", {
      name: "or start from a template",
    });
    expect(link).toBeDisabled();
    expect(link).toHaveAccessibleDescription("Not available in this release");
    expect(
      screen.getByText("Not available in this release"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Templates are not available yet"),
    ).not.toBeInTheDocument();
  });

  it("opens nothing from the turned-off template link", () => {
    renderEmptyView();

    fireEvent.click(
      screen.getByRole("button", { name: "or start from a template" }),
    );

    expect(
      screen.queryByTestId("choose-agent-template-drawer"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
  });

  it("does not mount the mock template gallery drawer", () => {
    renderEmptyView();

    expect(
      screen.queryByTestId("choose-agent-template-drawer"),
    ).not.toBeInTheDocument();
  });

  it("without edit permission shows no template link, as before", () => {
    mockCanEditAgent = false;
    renderEmptyView();

    expect(
      screen.queryByRole("button", { name: "or start from a template" }),
    ).not.toBeInTheDocument();
  });
});

describe("EmptyView — keyboard access to Add first node (AUD-AGENTS-03)", () => {
  beforeEach(() => {
    mockCanEditAgent = true;
  });

  it("the Add first node tile is a named button in the tab order", () => {
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    expect(tile).toHaveAttribute("tabindex", "0");
    tile.focus();
    expect(tile).toHaveFocus();
  });

  it("Enter on the tile opens the node menu", () => {
    renderEmptyView();

    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "Add first node" }), {
      key: "Enter",
    });

    expect(screen.getByTestId("node-selection-popper")).toBeInTheDocument();
  });

  it("Space on the tile opens the node menu", () => {
    renderEmptyView();

    fireEvent.keyDown(screen.getByRole("button", { name: "Add first node" }), {
      key: " ",
    });

    expect(screen.getByTestId("node-selection-popper")).toBeInTheDocument();
  });

  it("without edit permission the tile is marked disabled and Enter opens nothing", () => {
    mockCanEditAgent = false;
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    expect(tile).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(tile, { key: "Enter" });

    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
  });
});

describe("EmptyView — the node menu says it is open and closes on Escape (UX-AGENTS-A-1)", () => {
  beforeEach(() => {
    mockCanEditAgent = true;
    mockPopperAnchorEl = undefined;
  });

  it("tells screen readers the tile opens a menu and whether it is open", () => {
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    expect(tile).toHaveAttribute("aria-haspopup", "true");
    expect(tile).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(tile, { key: "Enter" });
    expect(tile).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(tile, { key: "Enter" });
    expect(tile).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
  });

  it("opens the node menu next to the tile", () => {
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    fireEvent.keyDown(tile, { key: "Enter" });

    expect(screen.getByTestId("node-selection-popper")).toBeInTheDocument();
    expect(mockPopperAnchorEl).toBe(tile);
  });

  it("Escape on the tile closes the node menu", () => {
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    tile.focus();
    fireEvent.keyDown(tile, { key: "Enter" });
    expect(screen.getByTestId("node-selection-popper")).toBeInTheDocument();

    fireEvent.keyDown(tile, { key: "Escape" });

    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
    expect(tile).toHaveAttribute("aria-expanded", "false");
    expect(tile).toHaveFocus();
  });

  it("leaves Escape alone while the menu is closed", () => {
    renderEmptyView();

    const tile = screen.getByRole("button", { name: "Add first node" });
    // fireEvent returns false only when a handler called preventDefault.
    expect(fireEvent.keyDown(tile, { key: "Escape" })).toBe(true);

    expect(
      screen.queryByTestId("node-selection-popper"),
    ).not.toBeInTheDocument();
    expect(tile).toHaveAttribute("aria-expanded", "false");
  });
});
