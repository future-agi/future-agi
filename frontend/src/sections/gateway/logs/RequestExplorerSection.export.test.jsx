import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import RequestExplorerSection from "./RequestExplorerSection";

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

vi.mock("./RequestTable", () => ({ default: () => null }));
vi.mock("./SessionExplorer", () => ({ default: () => null }));
vi.mock("./RequestDetailDrawer", () => ({ default: () => null }));
vi.mock("./FilterPanel", () => ({ default: () => null }));

describe("RequestExplorerSection export", () => {
  beforeEach(() => {
    mockGet.mockReset();
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
    render(<RequestExplorerSection />);

    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(screen.getByRole("menuitem", { name: "Export CSV" }));
    await screen.findByText("1 request will be exported.");
    await user.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    const [url, { params }] = mockGet.mock.calls[1];
    expect(url).toBe("/export");
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
});
