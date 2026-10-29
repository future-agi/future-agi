import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import RequestExplorerSection from "./RequestExplorerSection";

const columnsState = {
  columns: [],
  pickerEntries: { builtin: [], custom: [] },
  stale: [],
  visibleCount: 10,
  totalCount: 10,
  atCap: false,
  storageKey: "agentcc.requestLogs.columns.v1:user-a:org-1:requests",
  declarationStatus: "success",
  declarationsAvailable: true,
  declarationsFetching: false,
  reservedDeclared: false,
  unavailableCustomCount: 0,
  toggle: vi.fn(),
  move: vi.fn(),
  remove: vi.fn(),
  reset: vi.fn(),
  retryDeclarations: vi.fn(),
};

const { mockUseRequestColumns } = vi.hoisted(() => ({
  mockUseRequestColumns: vi.fn(),
}));

vi.mock("./columns/useRequestColumns", () => ({
  default: (...args) => mockUseRequestColumns(...args),
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
  beforeEach(() => {
    mockUseRequestColumns.mockReset();
    mockUseRequestColumns.mockReturnValue(columnsState);
  });

  it("shows the Columns button beside Filters on the Requests tab and opens the named dialog", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);
    expect(mockUseRequestColumns).toHaveBeenCalledWith({ enabled: true });

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

  it("has no Columns control and fetches no declarations on the Sessions tab", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs?view=sessions");
    render(<RequestExplorerSection />);
    expect(screen.getByTestId("session-explorer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Columns" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockUseRequestColumns).toHaveBeenCalledWith({ enabled: false });
    expect(mockUseRequestColumns).not.toHaveBeenCalledWith({ enabled: true });
  });

  it("passes the declaration refetch state to the picker's Retry", async () => {
    const user = userEvent.setup();
    mockUseRequestColumns.mockReturnValue({
      ...columnsState,
      declarationStatus: "error",
      declarationsFetching: true,
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);
    await user.click(screen.getByRole("button", { name: "Columns" }));
    expect(
      screen.getByRole("button", { name: "Retry loading custom properties" }),
    ).toBeDisabled();
  });

  it("passes cached declaration availability through when the refresh fails", async () => {
    const user = userEvent.setup();
    mockUseRequestColumns.mockReturnValue({
      ...columnsState,
      declarationStatus: "error",
      declarationsAvailable: true,
      pickerEntries: {
        builtin: [],
        custom: [
          {
            id: "metadata:tenant",
            label: "tenant",
            checked: true,
            inConfig: true,
            canMoveUp: false,
            canMoveDown: false,
          },
        ],
      },
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);
    await user.click(screen.getByRole("button", { name: "Columns" }));
    expect(screen.getByRole("checkbox", { name: "tenant" })).toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Showing previously loaded properties",
    );
    expect(
      screen.getByRole("button", { name: "Retry loading custom properties" }),
    ).toBeEnabled();
  });

  it("keeps the Columns button disabled until the preference key is known", () => {
    mockUseRequestColumns.mockReturnValue({
      ...columnsState,
      storageKey: null,
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<RequestExplorerSection />);
    expect(screen.getByRole("button", { name: "Columns" })).toBeDisabled();
  });
});
