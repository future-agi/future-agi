import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import RequestExplorerSection from "./RequestExplorerSection";

const columnsState = {
  columns: [],
  pickerEntries: { builtin: [], custom: [] },
  stale: [],
  visibleCount: 10,
  totalCount: 10,
  declarationStatus: "success",
  unavailableCustomCount: 0,
  storageNotice: null,
  toggle: vi.fn(),
  move: vi.fn(),
  remove: vi.fn(),
  reset: vi.fn(),
  retryDeclarations: vi.fn(),
  dismissStorageNotice: vi.fn(),
};

vi.mock("./columns/useRequestColumns", () => ({
  default: () => columnsState,
}));

vi.mock("./RequestTable", () => ({
  default: ({ columns }) => (
    <div data-testid="request-table">{columns.length} columns</div>
  ),
}));

vi.mock("./SessionExplorer", () => ({
  default: () => <div data-testid="session-explorer" />,
}));

vi.mock("./RequestDetailDrawer", () => ({
  default: () => null,
}));

vi.mock("./FilterPanel", () => ({
  default: () => null,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { gateway: { requestLogExport: "/export" } },
}));

describe("RequestExplorerSection columns control", () => {
  it("shows the Columns button beside Filters on the Requests tab and opens the named dialog (R1/R39)", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);

    const button = screen.getByRole("button", { name: "Columns" });
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
    expect(screen.getByTestId("request-table")).toHaveTextContent("0 columns");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("dialog", { name: "Choose columns" }),
    ).toBeInTheDocument();
  });

  it("has no Columns control on the Sessions tab (R1)", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs?view=sessions");
    render(<RequestExplorerSection />);
    expect(screen.getByTestId("session-explorer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Columns" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("surfaces the storage notice from the hook (R16)", () => {
    columnsState.storageNotice = "Preferences could not be saved";
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);
    expect(
      screen.getByText("Preferences could not be saved"),
    ).toBeInTheDocument();
    columnsState.storageNotice = null;
  });
});
