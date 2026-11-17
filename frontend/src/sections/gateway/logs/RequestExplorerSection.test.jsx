import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import {
  render,
  screen,
  userEvent,
  within,
  fireEvent,
  waitFor,
} from "src/utils/test-utils";
import axiosInstance from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { SAVED_VIEWS_KEY } from "./savedViews";
import { REQUEST_TIME_WINDOWS } from "./explorerFilters";
import { BUILTIN_COLUMNS } from "./columns/columnModel";
import { grid } from "./__tests__/fakeAgGrid";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import RequestExplorerSection from "./RequestExplorerSection";
import { GatewayContext } from "../context/GatewayContext";

const Page = () => {
  const [client] = React.useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <GatewayContext.Provider value={{ gatewayId: null }}>
          <RequestExplorerSection />
        </GatewayContext.Provider>
      </QueryClientProvider>
    </HelmetProvider>
  );
};

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

vi.mock("./RequestTable", async (importOriginal) => {
  const { default: ActualTable } = await importOriginal();
  return {
    default: (props) => {
      table.props = props;
      return table.real ? (
        <ActualTable {...props} />
      ) : (
        <div data-testid="request-table">{props.columns.length} columns</div>
      );
    },
  };
});
vi.mock("ag-grid-react", () => import("./__tests__/fakeAgGrid"));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("./FilterPanel", async (importOriginal) => {
  const { default: ActualPanel } = await importOriginal();
  return {
    default: (props) => (table.real ? <ActualPanel {...props} /> : null),
  };
});
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
vi.mock("./hooks/useMetadataValues", () => ({
  default: () => ({ data: {} }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    gateway: {
      requestLogExport: "/export",
      requestLogs: "/logs",
      requestLogSearch: "/search",
      requestLogVolume: "/volume",
      requestLogDetail: (id) => `/details/${id}`,
    },
  },
}));

const params = () => new URLSearchParams(window.location.search);

beforeEach(() => {
  enqueueSnackbar.mockClear();
  mockUseRequestColumns.mockReset();
  mockUseRequestColumns.mockReturnValue(columnsState);
  window.localStorage.clear();
  table.real = false;
  axiosInstance.get.mockReset();
  axiosInstance.get.mockResolvedValue({
    data: { result: { results: [], count: 0, series: [] } },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

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
    const skip = screen.getByRole("link", {
      name: "Skip to the request list",
    });
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

  // All undoes a quick filter only: on a key's "View logs for this key" link,
  // Errors then All still shows that key's logs with the search and time
  // range the user had. Clear all is the full reset.
  it("All removes only the quick filter and keeps a deep link's scope", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?api_key_id=key-1&model=gpt-4o&search=timeout&started_after=2026-11-01T10:00:00Z",
    );
    render(<Page />);
    const search = screen.getByPlaceholderText(
      "Search model, provider, request id, error",
    );
    expect(
      screen.getByRole("button", { name: "All", pressed: true }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Applied filters" })).getByText(
        "API key: key-1",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Errors" }));
    expect(params().get("is_error")).toBe("true");
    expect(params().get("api_key_id")).toBe("key-1");

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(params().get("is_error")).toBeNull();
    expect(params().get("api_key_id")).toBe("key-1");
    expect(params().get("model")).toBe("gpt-4o");
    expect(params().get("search")).toBe("timeout");
    expect(params().get("started_after")).toBe("2026-11-01T10:00:00Z");
    expect(
      screen.getByRole("button", { name: "All", pressed: true }),
    ).toBeInTheDocument();
    const applied = within(
      screen.getByRole("list", { name: "Applied filters" }),
    );
    expect(applied.getByText("API key: key-1")).toBeInTheDocument();
    expect(applied.getByText("Model: gpt-4o")).toBeInTheDocument();
    expect(applied.getByText(/^From:/)).toBeInTheDocument();
    expect(search).toHaveValue("timeout");
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

    await user.click(await screen.findByRole("button", { name: "Clear all" }));
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
    expect(toggle).toBeChecked();
    // The page owns the single cadence for rows and volume; no table poller.
    expect(table.props.refetchInterval).toBe(false);
  });

  it("does not auto-refresh an older page", () => {
    window.localStorage.setItem("gateway-logs:auto-refresh", "on");
    window.history.pushState({}, "", "/dashboard/gateway/logs?page=2");
    render(<Page />);
    expect(screen.getByRole("switch", { name: /Auto-refresh/ })).toBeDisabled();
    expect(table.props.refetchInterval).toBe(false);
  });

  // A time-range chip fixes the window's end at the click,
  // so Auto-refresh reloads that window and no newer request can appear. The
  // switch must say so instead of promising the newest requests, and the
  // applied chip shows as selected.
  it("says Auto-refresh cannot show newer requests once a time-range chip fixes the end", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("gateway-logs:auto-refresh", "on");
    window.history.pushState({}, "", "/dashboard/gateway/logs");
    render(<Page />);
    const toggle = screen.getByRole("switch", { name: /Auto-refresh/ });
    const timeRange = screen.getByRole("group", { name: "Time range" });
    await user.click(within(timeRange).getByText("Last 15 minutes"));
    expect(params().get("started_before")).toBeTruthy();
    expect(toggle).toBeChecked();
    await user.hover(toggle);
    const tooltip = await screen.findByRole("tooltip", {
      name: /Remove the To filter to follow new requests\./,
    });
    expect(tooltip).toHaveTextContent(
      "Reloads this time range every 10 seconds. The range has a fixed end, so newer requests do not appear.",
    );
    expect(tooltip).not.toHaveTextContent("Reloads the newest requests");
    expect(
      screen.getByRole("button", { name: "Last 15 minutes", pressed: true }),
    ).toBeInTheDocument();
    for (const name of ["Last hour", "Last 24 hours", "Last 7 days"]) {
      expect(
        screen.getByRole("button", { name, pressed: false }),
      ).toBeInTheDocument();
    }
  });
});

async function openViewNaming(user, name) {
  await user.click(screen.getByRole("button", { name: "Saved filters" }));
  await user.click(
    screen.getByRole("menuitem", { name: "Save current filters" }),
  );
  await user.type(screen.getByLabelText("Name"), name);
}

async function nameView(user, name) {
  await openViewNaming(user, name);
  await user.click(screen.getByRole("button", { name: "Save" }));
}

describe("Truthful saved filters", () => {
  it("saves against the latest storage when another tab changes filters while naming", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      SAVED_VIEWS_KEY,
      JSON.stringify([
        { name: "Existing", query: "model=original" },
        { name: "Removed elsewhere", query: "provider=old" },
      ]),
    );
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?is_error=true&page=3",
    );
    render(<Page />);
    await openViewNaming(user, "This tab");
    // Another tab edits, deletes and adds views after this dialog opens.
    const latest = [
      { name: "Existing", query: "model=updated" },
      { name: "Other tab", query: "application=checkout" },
    ];
    window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(latest));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual([
      ...latest,
      { name: "This tab", query: "is_error=true" },
    ]);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      'Saved "This tab" in this browser',
      { variant: "success" },
    );
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    expect(
      screen.queryByRole("menuitem", { name: /^Removed elsewhere/ }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: /^Other tab/ }));
    expect(params().get("application")).toBe("checkout");
  });

  it.each(["Cancel", "Replace"])(
    "rechecks a name saved by another tab before Enter, then honors %s with current storage",
    async (decision) => {
      const user = userEvent.setup();
      window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
      render(<Page />);
      await openViewNaming(user, " Shared ");
      const latest = [
        { name: "Shared", query: "model=other-tab" },
        { name: "Other tab", query: "provider=other" },
      ];
      window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(latest));
      const write = vi.spyOn(Storage.prototype, "setItem");
      await user.keyboard("{Enter}");
      const dialog = await screen.findByRole("dialog", {
        name: "Replace saved filter Shared?",
      });
      expect(write).not.toHaveBeenCalled();
      expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual(
        latest,
      );
      expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.anything(), {
        variant: "success",
      });
      // Storage can change again while the replacement prompt is open.
      const newest = [
        ...latest,
        { name: "Later tab", query: "application=later" },
      ];
      window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(newest));
      write.mockClear();
      await user.click(within(dialog).getByRole("button", { name: decision }));
      expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual(
        decision === "Replace"
          ? [{ name: "Shared", query: "is_error=true" }, ...newest.slice(1)]
          : newest,
      );
      expect(
        write.mock.calls.filter(([key]) => key === SAVED_VIEWS_KEY),
      ).toHaveLength(decision === "Replace" ? 1 : 0);
    },
  );

  it("keeps failed writes visit-only while checking and saving against another tab's latest filters", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
    render(<Page />);
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
      throw new Error("Storage full");
    });
    await nameView(user, "Temporary errors");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Not saved in this browser",
    );
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.click(screen.getByRole("button", { name: "Fallbacks" }));
    await openViewNaming(user, "Shared");
    const latest = [
      { name: "Shared", query: "model=other-tab" },
      { name: "Other tab", query: "application=checkout" },
    ];
    window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(latest));
    await user.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Replace saved filter Shared?",
    });
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual(
      latest,
    );
    await user.click(within(dialog).getByRole("button", { name: "Replace" }));
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual([
      { name: "Shared", query: "fallback_used=true" },
      latest[1],
    ]);
    // Saving a different filter must not silently persist or discard this one.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Not saved in this browser",
    );
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    expect(screen.getByText("Filters for this visit")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /^Other tab/ }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("menuitem", { name: /^Temporary errors/ }),
    );
    expect(params().get("is_error")).toBe("true");
    expect(params().get("fallback_used")).toBeNull();
  });

  it("keeps a failed save usable in memory, warns and can still copy its view link", async () => {
    const user = userEvent.setup();
    const clipboard = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Blocked");
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
    render(<Page />);
    await nameView(user, "Temporary errors");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Not saved in this browser",
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    await user.click(
      screen.getByRole("menuitem", { name: /^Temporary errors/ }),
    );
    expect(params().get("is_error")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Copy view link" }));
    expect(
      new URL(clipboard.mock.calls.at(-1)[0]).searchParams.get("is_error"),
    ).toBe("true");
    expect(window.localStorage.getItem(SAVED_VIEWS_KEY)).toBeNull();
  });

  it("successful saves survive remount; duplicate Cancel preserves the original and Replace writes once", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
    const first = render(<Page />);
    await nameView(user, "Investigation");
    first.unmount();
    window.history.pushState({}, "", "/dashboard/gateway/logs?model=new-model");
    render(<Page />);
    const write = vi.spyOn(Storage.prototype, "setItem");
    await nameView(user, "Investigation");
    let dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Replace saved filter Investigation?");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(write).not.toHaveBeenCalled();
    expect(
      JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))[0].query,
    ).toBe("is_error=true");
    await nameView(user, "Investigation");
    dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Replace" }));
    expect(
      write.mock.calls.filter(([key]) => key === SAVED_VIEWS_KEY),
    ).toHaveLength(1);
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual([
      { name: "Investigation", query: "model=new-model" },
    ]);
  });

  it("failed delete warns without claiming persisted success or losing the copy-link action", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      SAVED_VIEWS_KEY,
      JSON.stringify([{ name: "Errors", query: "is_error=true" }]),
    );
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Blocked");
    });
    window.history.pushState({}, "", "/dashboard/gateway/logs?is_error=true");
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Saved filters" }));
    await user.click(
      screen.getByRole("button", { name: "Delete saved filter Errors" }),
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete filter",
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Not saved in this browser",
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
    expect(
      screen.getByRole("button", { name: "Copy view link" }),
    ).toBeEnabled();
    expect(window.localStorage.getItem(SAVED_VIEWS_KEY)).toContain("Errors");
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

const SORTED_ROWS = ["z-first", "a-middle", "m-last"].map((id, index) => ({
  id,
  request_id: `req-${id}`,
  model: "gpt-4o",
  provider: "openai",
  status_code: 500,
  latency_ms: 900 - index * 100,
  started_at: "2026-11-09T11:00:00Z",
  is_error: true,
}));

function realResults() {
  table.real = true;
  mockUseRequestColumns.mockReturnValue({
    ...columnsState,
    columns: BUILTIN_COLUMNS,
  });
  axiosInstance.get.mockImplementation(async (url) => {
    if (url?.startsWith("/details/")) {
      const id = url.slice("/details/".length);
      return {
        data: {
          result: SORTED_ROWS.find((row) => row.id === id) || {
            id,
            request_id: `req-${id}`,
          },
        },
      };
    }
    if (url === "/volume") return { data: { result: { series: [] } } };
    if (url === "/export") return { status: 200, data: new Blob(["requests"]) };
    return { data: { result: { results: SORTED_ROWS, count: 3 } } };
  });
}

const investigation =
  "/dashboard/gateway/logs?search=timeout&model=gpt-4o&is_error=true&sort=-latency_ms&pageSize=50&page=3";

// Real list/search, volume, export and detail hooks; only transport and AG Grid
// layout are mocked, so these assertions pin the API boundary as well as the URL.
describe("Incident investigation", () => {
  it.each([false, true])(
    "one opt-in tick refreshes rows and volume, preserves explicit bounds (%s) and stops when off/unmounted",
    async (snapshot) => {
      vi.useFakeTimers({ toFake: ["Date"] });
      const now = new Date("2026-11-09T12:00:00Z");
      vi.setSystemTime(now);
      const visibility = vi
        .spyOn(document, "visibilityState", "get")
        .mockReturnValue("visible");
      realResults();
      const bounds = snapshot
        ? "?started_after=2026-11-08T12:00:00Z&started_before=2026-11-09T12:00:00Z"
        : "";
      window.history.pushState({}, "", `/dashboard/gateway/logs${bounds}`);
      const { unmount } = render(<Page />);
      await waitFor(() =>
        expect(
          axiosInstance.get.mock.calls.some(([url]) => url === "/volume"),
        ).toBe(true),
      );
      const firstVolume = axiosInstance.get.mock.calls
        .filter(([url]) => url === "/volume")
        .at(-1)[1].params;
      vi.useFakeTimers();
      vi.setSystemTime(now);
      const interval = vi.spyOn(globalThis, "setInterval");
      fireEvent.click(screen.getByRole("switch", { name: /Auto-refresh/ }));
      expect(interval.mock.calls.filter(([, ms]) => ms === 10000)).toHaveLength(
        1,
      );
      axiosInstance.get.mockClear();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      const rowReads = axiosInstance.get.mock.calls.filter(
        ([url, config]) =>
          url === "/logs" && config.params.ordering === "-started_at",
      );
      const volumeReads = axiosInstance.get.mock.calls.filter(
        ([url]) => url === "/volume",
      );
      expect(rowReads).toHaveLength(1);
      expect(volumeReads).toHaveLength(1);
      expect(volumeReads[0][1].params.end).toBe(
        snapshot ? firstVolume.end : "2026-11-09T12:00:10.000Z",
      );
      if (snapshot) {
        expect(volumeReads[0][1].params.start).toBe(firstVolume.start);
        expect(rowReads[0][1].params.started_after).toBe(firstVolume.start);
        expect(rowReads[0][1].params.started_before).toBe(firstVolume.end);
        expect(window.location.search).toBe(bounds);
      }
      visibility.mockReturnValue("hidden");
      axiosInstance.get.mockClear();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(axiosInstance.get).not.toHaveBeenCalled();
      visibility.mockReturnValue("visible");
      fireEvent.click(screen.getByRole("switch", { name: /Auto-refresh/ }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(axiosInstance.get).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("switch", { name: /Auto-refresh/ }));
      unmount();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(axiosInstance.get).not.toHaveBeenCalled();
    },
  );

  it.each([
    { snapshot: false, first: "rows" },
    { snapshot: false, first: "volume" },
    { snapshot: true, first: "rows" },
    { snapshot: true, first: "volume" },
  ])(
    "coalesces slow reads across ticks until both finish (snapshot=$snapshot, first=$first)",
    async ({ snapshot, first }) => {
      vi.useFakeTimers({ toFake: ["Date"] });
      const now = new Date("2026-11-09T12:00:00Z");
      vi.setSystemTime(now);
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
      realResults();
      const initialRead = axiosInstance.get.getMockImplementation();
      const volumeResponse = (requests, errors) => ({
        data: {
          result: {
            series: [
              {
                bucket: "2026-11-09T11:00:00Z",
                request_count: requests,
                error_count: errors,
              },
            ],
          },
        },
      });
      axiosInstance.get.mockImplementation((url, config) =>
        url === "/volume"
          ? Promise.resolve(volumeResponse(3, 1))
          : initialRead(url, config),
      );
      const bounds = snapshot
        ? "?started_after=2026-11-08T12:00:00Z&started_before=2026-11-09T12:00:00Z"
        : "";
      window.history.pushState({}, "", `/dashboard/gateway/logs${bounds}`);
      const { unmount } = render(<Page />);
      await waitFor(() => expect(grid.props.rowData).toHaveLength(3));
      await screen.findByRole("img", { name: /3 requests, 1 errors/ });
      const initialVolume = axiosInstance.get.mock.calls
        .filter(([url]) => url === "/volume")
        .at(-1)[1].params;
      const pending = { rows: [], volume: [] };
      axiosInstance.get.mockImplementation((url, config) => {
        const kind =
          url === "/volume"
            ? "volume"
            : url === "/logs" && config.params.ordering === "-started_at"
              ? "rows"
              : null;
        if (!kind) return initialRead(url, config);
        return new Promise((resolve) => {
          pending[kind].push({ resolve, params: config.params });
        });
      });
      vi.useFakeTimers();
      vi.setSystemTime(now);
      const advanceTo = async (elapsed) => {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(
            now.getTime() + elapsed - Date.now(),
          );
        });
        // Flush React Query's notifications after React commits the volume key.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1);
        });
      };
      fireEvent.click(screen.getByRole("switch", { name: /Auto-refresh/ }));
      await advanceTo(10000);
      expect(pending.rows).toHaveLength(1);
      expect(pending.volume).toHaveLength(1);
      expect(pending.volume[0].params.end).toBe(
        snapshot ? initialVolume.end : "2026-11-09T12:00:10.000Z",
      );
      for (const elapsed of [20000, 30000]) {
        await advanceTo(elapsed);
        expect(pending.rows).toHaveLength(1);
        expect(pending.volume).toHaveLength(1);
      }
      expect(screen.getByText(/Updating requests/)).toBeInTheDocument();
      expect(screen.getByText(/Updating request volume/)).toBeInTheDocument();
      expect(grid.props.rowData).toHaveLength(3);
      expect(
        screen.getByRole("img", { name: /3 requests, 1 errors/ }),
      ).toBeInTheDocument();

      const freshRow = { ...SORTED_ROWS[0], id: "fresh", model: "fresh-model" };
      const responses = {
        rows: { data: { result: { results: [freshRow], count: 1 } } },
        volume: volumeResponse(7, 2),
      };
      await act(async () => pending[first][0].resolve(responses[first]));
      await advanceTo(31000);
      if (first === "rows") {
        expect(
          within(screen.getByTestId("ag-grid")).getByText("fresh-model"),
        ).toBeInTheDocument();
        expect(screen.getByText(/Updating request volume/)).toBeInTheDocument();
      } else {
        expect(
          screen.getByRole("img", { name: /7 requests, 2 errors/ }),
        ).toBeInTheDocument();
        expect(screen.getByText(/Updating requests/)).toBeInTheDocument();
      }
      // One completed read must not let the next tick restart the slower one.
      await advanceTo(40000);
      expect(pending.rows).toHaveLength(1);
      expect(pending.volume).toHaveLength(1);
      const second = first === "rows" ? "volume" : "rows";
      await act(async () => pending[second][0].resolve(responses[second]));
      await advanceTo(42000);
      expect(grid.props.rowData).toEqual([freshRow]);
      expect(
        within(screen.getByTestId("ag-grid")).getByText("fresh-model"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("img", { name: /7 requests, 2 errors/ }),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Updating requests/)).not.toBeInTheDocument();
      expect(
        screen.queryByText(/Updating request volume/),
      ).not.toBeInTheDocument();

      await advanceTo(50000);
      expect(pending.rows).toHaveLength(2);
      expect(pending.volume).toHaveLength(2);
      expect(pending.volume[1].params.end).toBe(
        snapshot ? initialVolume.end : "2026-11-09T12:00:50.000Z",
      );
      if (snapshot) {
        expect(pending.volume[1].params.start).toBe(initialVolume.start);
        expect(pending.rows[1].params.started_after).toBe(initialVolume.start);
        expect(pending.rows[1].params.started_before).toBe(initialVolume.end);
      }
      expect(window.location.search).toBe(bounds);
      unmount();
    },
  );

  it("marks retained rows updating and stale after a failed refresh while preserving Retry", async () => {
    realResults();
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?started_after=2026-11-08T00:00:00Z&started_before=2026-11-09T00:00:00Z",
    );
    render(<Page />);
    await waitFor(() => expect(grid.props.rowData).toHaveLength(3));
    let reject;
    axiosInstance.get.mockImplementation((url) =>
      url === "/logs"
        ? new Promise((_resolve, fail) => {
            reject = fail;
          })
        : Promise.resolve({ data: { result: { series: [] } } }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(/Updating requests/);
    expect(grid.props.rowData).toHaveLength(3);
    await act(async () => reject(new Error("Read failed")));
    expect(
      await screen.findByText(/Request logs could not be refreshed/),
    ).toHaveTextContent("stale");
    expect(grid.props.rowData).toHaveLength(3);
    realResults();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.queryByText(/Request logs could not be refreshed/),
      ).not.toBeInTheDocument(),
    );
  });

  it.each([
    ["Last 15 minutes", 15],
    ["Last hour", 60],
    ["Last 24 hours", 1440],
    ["Last 7 days", 10080],
  ])(
    "%s fixes one clock window for the URL, list, volume and export",
    async (label, minutes) => {
      vi.useFakeTimers({ toFake: ["Date"] });
      const now = new Date("2026-11-09T12:34:56.789Z");
      vi.setSystemTime(now);
      realResults();
      window.history.pushState({}, "", investigation);
      render(<Page />);
      fireEvent.click(screen.getByRole("button", { name: label }));
      const bounds = {
        started_after: new Date(now.getTime() - minutes * 60000).toISOString(),
        started_before: now.toISOString(),
      };
      expect(Object.fromEntries(params())).toEqual({
        search: "timeout",
        model: "gpt-4o",
        is_error: "true",
        sort: "-latency_ms",
        pageSize: "50",
        ...bounds,
      });
      await waitFor(() =>
        expect(axiosInstance.get).toHaveBeenCalledWith("/search", {
          params: expect.objectContaining({
            ...bounds,
            q: "timeout",
            model: "gpt-4o",
            is_error: "true",
            page: 1,
            ordering: "-latency_ms",
          }),
        }),
      );
      await waitFor(() =>
        expect(axiosInstance.get).toHaveBeenCalledWith("/volume", {
          params: expect.objectContaining({
            ...bounds,
            start: bounds.started_after,
            end: bounds.started_before,
            q: "timeout",
          }),
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Export" }));
      fireEvent.click(
        await screen.findByRole("menuitem", { name: "Export JSON" }),
      );
      const download = await screen.findByRole("button", {
        name: "Download",
      });
      await waitFor(() => expect(download).toBeEnabled());
      const create = URL.createObjectURL;
      const revoke = URL.revokeObjectURL;
      URL.createObjectURL = vi.fn(() => "blob:export");
      URL.revokeObjectURL = vi.fn();
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
        () => {},
      );
      try {
        fireEvent.click(download);
        await waitFor(() =>
          expect(axiosInstance.get).toHaveBeenCalledWith(
            "/export",
            expect.objectContaining({
              params: expect.objectContaining({
                ...bounds,
                q: "timeout",
                model: "gpt-4o",
                is_error: "true",
                ordering: "-latency_ms",
              }),
            }),
          ),
        );
      } finally {
        URL.createObjectURL = create;
        URL.revokeObjectURL = revoke;
      }
    },
  );

  it("does not let a pending search discard the newly selected time window", async () => {
    realResults();
    window.history.pushState({}, "", investigation);
    render(<Page />);
    fireEvent.change(
      screen.getByPlaceholderText("Search model, provider, request id, error"),
      { target: { value: "timeout server" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Last hour" }));
    const start = params().get("started_after");
    const end = params().get("started_before");
    await waitFor(() => expect(params().get("search")).toBe("timeout server"));
    expect(params().get("started_after")).toBe(start);
    expect(params().get("started_before")).toBe(end);
    expect(params().get("model")).toBe("gpt-4o");
    expect(params().get("is_error")).toBe("true");
  });

  it("names each time-range button by its window; the tooltip only describes it", () => {
    realResults();
    window.history.pushState({}, "", investigation);
    render(<Page />);
    const group = screen.getByRole("group", { name: "Time range" });
    const hint = "Applies a fixed time window ending now";
    const buttons = (name) => within(group).queryAllByRole("button", { name });
    expect(buttons(hint)).toHaveLength(0);
    for (const { label } of REQUEST_TIME_WINDOWS) {
      expect(buttons(label)).toHaveLength(1);
      expect(buttons(label)[0]).toHaveAccessibleDescription(hint);
    }
  });

  it("opens Custom without changing filters until Apply", async () => {
    realResults();
    window.history.pushState({}, "", investigation);
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(window.location.search).toBe(
      new URL(investigation, window.location.origin).search,
    );
    expect(await screen.findByLabelText("Start Date")).toBeInTheDocument();
  });

  it("traverses visible server-sorted rows without changing filters, sort, page or scroll, and reopens a copied link", async () => {
    const user = userEvent.setup();
    realResults();
    window.history.pushState({}, "", investigation);
    const { unmount } = render(<Page />);
    await waitFor(() =>
      expect(
        screen.getByTestId("ag-grid").querySelectorAll("tbody tr"),
      ).toHaveLength(3),
    );
    const gridElement = screen.getByTestId("ag-grid");
    gridElement.scrollTop = 176;
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const originalFilters = table.props.filters;
    expect(grid.props.rowData.map((row) => row.id)).toEqual([
      "z-first",
      "a-middle",
      "m-last",
    ]);
    fireEvent.click(gridElement.querySelector('[data-row-id="a-middle"]'));
    expect(
      await screen.findByRole("heading", { name: "req-a-middle" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 of 3 on this page")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next request" }));
    expect(params().get("log")).toBe("m-last");
    expect(
      await screen.findByRole("heading", { name: "req-m-last" }),
    ).toBeInTheDocument();
    expect(axiosInstance.get).toHaveBeenCalledWith("/details/m-last");
    expect(screen.getByRole("button", { name: "Next request" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous request" }));
    await user.click(screen.getByRole("button", { name: "Previous request" }));
    expect(
      await screen.findByRole("heading", { name: "req-z-first" }),
    ).toBeInTheDocument();
    expect(params().get("log")).toBe("z-first");
    expect(
      screen.getByRole("button", { name: "Previous request" }),
    ).toBeDisabled();
    expect(table.props.filters).toEqual(originalFilters);
    expect(screen.getByTestId("ag-grid")).toBe(gridElement);
    expect(gridElement.scrollTop).toBe(176);
    expect(scroll).not.toHaveBeenCalled();
    const clipboard = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    await user.click(screen.getByRole("button", { name: "Copy request link" }));
    const copied = clipboard.mock.calls[0][0];
    expect(new URL(copied).searchParams.get("log")).toBe("z-first");
    const originalQuery = window.location.search;
    unmount();
    window.history.replaceState({}, "", copied);
    render(<Page />);
    expect(
      await screen.findByRole("heading", { name: "req-z-first" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe(originalQuery);
    expect(table.props.filters).toEqual(originalFilters);
  });

  it("never navigates an absent selection or through previous-query rows while a new page loads", async () => {
    realResults();
    window.history.pushState({}, "", `${investigation}&log=absent`);
    render(<Page />);
    expect(
      await screen.findByRole("heading", { name: "req-absent" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByText("This request is not in the loaded page."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Previous request" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next request" })).toBeDisabled();
    act(() => table.props.onSelectLog("a-middle"));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Next request" }),
      ).toBeEnabled(),
    );
    let complete;
    const pending = new Promise((resolve) => {
      complete = resolve;
    });
    axiosInstance.get.mockImplementation((url) =>
      url === "/search"
        ? pending
        : Promise.resolve({ data: { result: { series: [] } } }),
    );
    act(() => table.props.setFilter("page", "4"));
    expect(screen.getByText(/Updating requests/)).toHaveTextContent(
      "Showing previous results",
    );
    expect(screen.getByRole("button", { name: "Next request" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next request" }));
    expect(params().get("log")).toBe("a-middle");
    await act(async () =>
      complete({
        data: {
          result: { results: [SORTED_ROWS[0], SORTED_ROWS[2]], count: 2 },
        },
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByText("This request is not in the loaded page."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Next request" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Previous request" }),
    ).toBeDisabled();
  });
});
