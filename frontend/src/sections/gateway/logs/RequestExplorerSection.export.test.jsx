import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
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

const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: { gateway: { requestLogExport: "/export", requestLogs: "/logs" } },
}));

vi.mock("./columns/useRequestColumns", () => ({
  default: () => ({
    columns: [],
    pickerEntries: { builtin: [], custom: [] },
    stale: [],
    visibleCount: 10,
    totalCount: 10,
    atCap: false,
    storageKey: "key",
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
  }),
}));

const { snack } = vi.hoisted(() => ({ snack: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => snack(...args),
}));

vi.mock("../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: { available: false } }),
}));
vi.mock("./RequestTable", () => ({ default: () => null }));
// The volume strip reads analytics through the same axios; this file is
// about the export call only.
vi.mock("./RequestVolumeStrip", () => ({ default: () => null }));
vi.mock("./RequestDetailDrawer", () => ({ default: () => null }));
vi.mock("./FilterPanel", () => ({ default: () => null }));

describe("RequestExplorerSection export", () => {
  beforeEach(() => {
    mockGet.mockReset();
    snack.mockReset();
    // The Export dialog counts with the list endpoint first.
    mockGet.mockImplementation(async (url) =>
      url === "/logs"
        ? { status: 200, data: { count: 1, results: [] } }
        : { status: 200, data: new Blob(["x"]) },
    );
    // jsdom has no object URLs; the download link only needs one.
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;
    vi.restoreAllMocks();
  });

  it("exports with the agent and billing filters the table is showing", async () => {
    const user = userEvent.setup();
    // jsdom cannot follow the download link.
    const download = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    window.history.pushState(
      {},
      "",
      "/dashboard/gateway/logs?model=gpt-5&billing_mode=managed&run_id=run-7&agent_id=planner&step=plan%2Cact&policy_variant=candidate&error_class=timeout",
    );
    render(<Page />);

    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(screen.getByRole("menuitem", { name: "Export CSV" }));
    await screen.findByText("1 request will be exported.");
    await user.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    const [url, { params, responseType }] = mockGet.mock.calls[1];
    expect(url).toBe("/export");
    expect(responseType).toBe("blob");
    expect(params).toEqual({
      model: "gpt-5",
      billing_mode: "managed",
      run_id: "run-7",
      agent_id: "planner",
      step: "plan,act",
      policy_variant: "candidate",
      error_class: "timeout",
      ordering: "-started_at",
      export_format: "csv",
      max_rows: 10000,
    });
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
  });

  // The refusal is shown in the Export dialog, where the download was asked
  // for, not as a passing notification.
  it("shows the server's reason when the export is refused", async () => {
    const user = userEvent.setup();
    const refusal = {
      status: 400,
      data: new Blob(
        [JSON.stringify({ status: false, message: "No data to export" })],
        {
          type: "application/json",
        },
      ),
    };
    mockGet.mockImplementation(async (url) =>
      url === "/logs"
        ? { status: 200, data: { count: 2, results: [] } }
        : refusal,
    );
    window.history.pushState({}, "", "/dashboard/gateway/logs?session_id=nope");
    render(<Page />);

    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(screen.getByRole("menuitem", { name: "Export JSON" }));
    await screen.findByText("2 requests will be exported.");
    await user.click(screen.getByRole("button", { name: "Download" }));

    expect(
      await screen.findByText("No data to export", {
        selector: ".MuiAlert-message",
      }),
    ).toBeInTheDocument();
    const exportCall = mockGet.mock.calls.find(([url]) => url === "/export");
    expect(exportCall[1].params).toEqual({
      session_id: "nope",
      ordering: "-started_at",
      export_format: "json",
      max_rows: 10000,
    });
  });
});
