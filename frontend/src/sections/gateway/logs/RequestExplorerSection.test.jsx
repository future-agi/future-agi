import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import RequestExplorerSection from "./RequestExplorerSection";

const Page = () => (
  <HelmetProvider>
    <QueryClientProvider client={new QueryClient()}>
      <RequestExplorerSection />
    </QueryClientProvider>
  </HelmetProvider>
);

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

const { mockUseRequestColumns, table } = vi.hoisted(() => ({
  mockUseRequestColumns: vi.fn(),
  table: { props: null },
}));

vi.mock("./columns/useRequestColumns", () => ({
  default: (...args) => mockUseRequestColumns(...args),
}));

vi.mock("./RequestTable", () => ({
  default: (props) => {
    table.props = props;
    return (
      <div data-testid="request-table">{props.columns.length} columns</div>
    );
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

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

const params = () => new URLSearchParams(window.location.search);

beforeEach(() => {
  mockUseRequestColumns.mockReset();
  mockUseRequestColumns.mockReturnValue(columnsState);
  window.localStorage.clear();
});

afterEach(() => vi.restoreAllMocks());

describe("Logs page frame", () => {
  it("shows the request volume above the table", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    const strip = screen.getByTestId("request-volume");
    expect(
      strip.compareDocumentPosition(screen.getByTestId("request-table")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("is the FA page frame with an 18 px title and one view", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Request logs" }),
    ).toBeInTheDocument();
    // One view: no Requests | Sessions tabs.
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.getByTestId("request-table")).toBeInTheDocument();
  });

  // A keyboard user needed 20 Tab stops from the search box
  // to reach the first log row. The page's first stop skips to it.
  it("opens with a skip link that puts focus on the first log row", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    const api = {
      getDisplayedRowCount: () => 25,
      getAllDisplayedColumns: () => ["timestamp-col", "status-col"],
      ensureIndexVisible: vi.fn(),
      setFocusedCell: vi.fn(),
    };
    act(() => table.props.onGridReady({ api }));
    await user.tab();
    const skip = screen.getByRole("link", { name: "Skip to the request list" });
    expect(skip).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(api.ensureIndexVisible).toHaveBeenCalledWith(0);
    expect(api.setFocusedCell).toHaveBeenCalledWith(0, "timestamp-col");
  });

  it("sends the old Sessions tab URL to the Sessions page", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs?view=sessions");
    render(<Page />);
    expect(window.location.pathname).toBe("/dashboard/gateway/sessions");
  });

  it("applies a quick filter and All clears it", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs?page=3");
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Errors" }));
    expect(params().get("is_error")).toBe("true");
    expect(params().get("page")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Fallbacks" }));
    expect(params().get("fallback_used")).toBe("true");
    expect(params().get("is_error")).toBeNull();
    await user.click(screen.getByRole("button", { name: "All" }));
    expect(window.location.search).toBe("");
  });

  it("shows each applied filter as a chip that removes it", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?session_id=sess-1&run_id=run-7&model=gpt-4o",
    );
    render(<Page />);
    const applied = within(
      screen.getByRole("list", { name: "Applied filters" }),
    );
    expect(applied.getByText("Session: sess-1")).toBeInTheDocument();
    expect(applied.getByText("Run: run-7")).toBeInTheDocument();
    expect(applied.getByText("Model: gpt-4o")).toBeInTheDocument();
    await user.click(
      applied.getByRole("button", { name: "Remove Session: sess-1" }),
    );
    expect(params().get("session_id")).toBeNull();
    expect(params().get("run_id")).toBe("run-7");
    expect(table.props.onClearFilters).toBeInstanceOf(Function);
  });

  // One name per billing mode: the managed route's Open
  // logs link applies billing_mode=managed, and its chip reads the mode's
  // name, never the raw value.
  it("names the billing mode chip by the mode's name", () => {
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?billing_mode=managed",
    );
    render(<Page />);
    const applied = within(
      screen.getByRole("list", { name: "Applied filters" }),
    );
    expect(
      applied.getByText("Billing mode: Future AGI credits"),
    ).toBeInTheDocument();
    expect(applied.queryByText(/Billing: managed/)).toBeNull();
  });

  it("saves the current filters in this browser and applies them again", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?is_error=true&application=checkout",
    );
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    expect(screen.getByText(/Saved in this browser/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("menuitem", { name: "Save current filters" }),
    );
    await user.type(screen.getByLabelText("Name"), "Checkout errors");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await user.click(await screen.findByRole("button", { name: "All" }));
    expect(window.location.search).toBe("");

    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    await user.click(
      screen.getByRole("menuitem", { name: /^Checkout errors/ }),
    );
    expect(params().get("is_error")).toBe("true");
    expect(params().get("application")).toBe("checkout");
  });

  it("asks before deleting a saved filter, naming it, and Cancel keeps it", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    await user.click(
      screen.getByRole("menuitem", { name: "Save current filters" }),
    );
    await user.type(screen.getByLabelText("Name"), "Errors only");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await user.click(
      await screen.findByRole("button", { name: "Saved filters" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Delete saved filter Errors only" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete saved filter Errors only?");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(window.localStorage.getItem("gateway-logs:saved-filters")).toContain(
      "Errors only",
    );

    await user.click(
      await screen.findByRole("button", { name: "Saved filters" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Delete saved filter Errors only" }),
    );
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Delete filter",
      }),
    );
    expect(
      window.localStorage.getItem("gateway-logs:saved-filters") || "",
    ).not.toContain("Errors only");
  });

  // At 390 px the three toolbar buttons share 358 px: "Saved filters" broke
  // onto two lines. Labels never wrap inside a button, and on a phone the
  // button reads "Saved" (its name stays "Saved filters").
  it("keeps each toolbar button on one line, with a short Saved label for phones", () => {
    render(<Page />);
    const saved = screen.getByRole("button", { name: "Saved filters" });
    for (const name of ["Saved filters", "Filters", "Columns"]) {
      expect(
        getComputedStyle(screen.getByRole("button", { name })).whiteSpace,
      ).toBe("nowrap");
    }
    expect(within(saved).getByText("Saved")).toBeInTheDocument();
  });

  it("auto-refreshes the newest page only when turned on", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    expect(table.props.refetchInterval).toBe(false);
    const toggle = screen.getByRole("switch", { name: /Auto-refresh/ });
    await user.click(toggle);
    expect(table.props.refetchInterval).toBe(10000);
  });

  it("does not auto-refresh an older page", () => {
    window.localStorage.setItem("gateway-logs:auto-refresh", "on");
    window.history.pushState({}, "", "/dashboard/gateway/logs?page=2");
    render(<Page />);
    expect(screen.getByRole("switch", { name: /Auto-refresh/ })).toBeDisabled();
    expect(table.props.refetchInterval).toBe(false);
  });
});

describe("Logs columns control", () => {
  it("keeps managed-only columns hidden when managed models are unavailable, including historical rows", () => {
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    act(() => table.props.onResults([{ billing_mode: "managed" }]));
    expect(mockUseRequestColumns).toHaveBeenLastCalledWith({
      enabled: true,
      managed: false,
    });
  });
  it("shows Columns beside Filters and opens the named dialog", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    expect(mockUseRequestColumns).toHaveBeenCalledWith({
      enabled: true,
      managed: false,
    });

    const button = screen.getByRole("button", { name: "Columns" });
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByRole("button", { name: /^Filters/ }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("request-table")).toHaveTextContent("0 columns");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("dialog", { name: "Choose columns" }),
    ).toBeInTheDocument();
  });

  it("passes the declaration refetch state to the picker's Retry", async () => {
    const user = userEvent.setup();
    mockUseRequestColumns.mockReturnValue({
      ...columnsState,
      declarationStatus: "error",
      declarationsFetching: true,
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
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
    render(<Page />);
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
    render(<Page />);
    expect(screen.getByRole("button", { name: "Columns" })).toBeDisabled();
  });
});
