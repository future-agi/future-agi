// Export says what it will download before it downloads, and shows the
// server's answer when it refuses. Export opens a dialog that counts the rows
// the table's query matches (the list endpoint's count, limit=1, the table's
// own params) and states the scope. 0 rows: says so, nothing to download. Up
// to 10,000: downloads them. Over 10,000: shows the count and the cap and
// downloads the first 10,000 in the table's order (max_rows; the server
// streams them). A refusal shows the server's message, read from the Blob.
const ORIGINAL_TZ = globalThis.process.env.TZ;
globalThis.process.env.TZ = "America/Los_Angeles";

import React from "react";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import RequestExplorerSection from "./RequestExplorerSection";

const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    gateway: {
      requestLogs: "/logs",
      requestLogSearch: "/logs/search",
      requestLogExport: "/export",
    },
  },
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

const countResponse = (count) => ({
  status: 200,
  data: { count, next: null, previous: null, results: [] },
});

let download;
function renderAt(url) {
  window.history.pushState({}, "", url);
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RequestExplorerSection />
    </QueryClientProvider>,
  );
}

async function openExport(user, format = "CSV") {
  await user.click(screen.getByRole("button", { name: "Export" }));
  await user.click(screen.getByRole("menuitem", { name: `Export ${format}` }));
  return screen.findByRole("dialog");
}

const exportCalls = () =>
  mockGet.mock.calls.filter(([url]) => url === "/export");
const countCalls = () => mockGet.mock.calls.filter(([url]) => url === "/logs");

beforeEach(() => {
  mockGet.mockReset();
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  download = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
});

afterEach(() => {
  delete URL.createObjectURL;
  delete URL.revokeObjectURL;
  vi.restoreAllMocks();
});

// Leave the zone as this file found it for whatever runs next in the worker.
afterAll(() => {
  if (ORIGINAL_TZ === undefined) delete globalThis.process.env.TZ;
  else globalThis.process.env.TZ = ORIGINAL_TZ;
});

describe("the export dialog", () => {
  it("counts with the table's query, states the scope, then downloads those rows", async () => {
    mockGet.mockImplementation(async (url) =>
      url === "/logs"
        ? countResponse(3)
        : { status: 200, data: new Blob(["started_at,request_id\n"]) },
    );
    const user = userEvent.setup();
    renderAt(
      "/dashboard/gateway/logs?model=gpt-4o&started_after=2026-10-19T17%3A30%3A00.000Z&sort=-latency_ms",
    );
    await openExport(user);

    expect(
      await screen.findByText("3 requests will be exported."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/the requests that match the table's filters/i),
    ).toBeInTheDocument();
    const [, { params: countParams }] = countCalls()[0];
    expect(countParams).toEqual({
      model: "gpt-4o",
      started_after: "2026-10-19T17:30:00.000Z",
      ordering: "-latency_ms",
      page: 1,
      limit: 1,
    });

    await user.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    const [, config] = exportCalls()[0];
    expect(config.responseType).toBe("blob");
    expect(config.params).toEqual({
      model: "gpt-4o",
      started_after: "2026-10-19T17:30:00.000Z",
      ordering: "-latency_ms",
      export_format: "csv",
      max_rows: 10000,
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("with no filter, says it holds every request the table lists, newest first", async () => {
    mockGet.mockImplementation(async () => countResponse(42));
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs");
    await openExport(user, "JSON");
    expect(
      await screen.findByText(
        /No filters are set: the export holds every request the table lists, newest first/,
      ),
    ).toBeInTheDocument();
    expect(countCalls()[0][1].params).toEqual({
      ordering: "-started_at",
      page: 1,
      limit: 1,
    });
  });

  it("at 0 rows says there is nothing to export and downloads nothing", async () => {
    mockGet.mockImplementation(async () => countResponse(0));
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs?model=nope");
    await openExport(user);
    expect(
      await screen.findByText(
        "No requests match these filters, so there is nothing to export.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(exportCalls()).toHaveLength(0);
  });

  it("over the cap shows the count and the cap before downloading the first 10,000", async () => {
    mockGet.mockImplementation(async (url) =>
      url === "/logs"
        ? countResponse(12345)
        : { status: 200, data: new Blob(["x"]) },
    );
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs?model=gpt-4o");
    await openExport(user);
    expect(
      await screen.findByText(
        /12,345 requests match\. An export holds at most 10,000 rows, so this one holds the first 10,000 in the table's order\./,
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(exportCalls()[0][1].params.max_rows).toBe(10000);
  });

  it("shows the server's message from a refused export (the Blob body)", async () => {
    const body = new Blob(
      [
        JSON.stringify({
          status: false,
          code: "export_empty",
          message:
            "No requests match these filters, so there is nothing to export.",
        }),
      ],
      { type: "application/json" },
    );
    mockGet.mockImplementation(async (url) =>
      url === "/logs" ? countResponse(2) : { status: 400, data: body },
    );
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs?model=gpt-4o");
    await openExport(user);
    await screen.findByText("2 requests will be exported.");
    await user.click(screen.getByRole("button", { name: "Download" }));
    expect(
      await screen.findByText(
        "No requests match these filters, so there is nothing to export.",
        { selector: ".MuiAlert-message" },
      ),
    ).toBeInTheDocument();
    expect(download).not.toHaveBeenCalled();
    expect(exportCalls()[0][1].validateStatus(400)).toBe(true);
  });

  it("when the count fails, says so and still lets the server decide", async () => {
    mockGet.mockImplementation(async (url) => {
      if (url === "/logs") throw { statusCode: 500, message: "boom" };
      return { status: 200, data: new Blob(["x"]) };
    });
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs?model=gpt-4o");
    await openExport(user);
    expect(
      await screen.findByText(/The matching requests could not be counted/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
  });

  it("sends a naive date from an older link as the local time it was typed in", async () => {
    mockGet.mockImplementation(async () => countResponse(1));
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/logs?started_after=2026-10-19T10%3A30");
    await openExport(user);
    await screen.findByText("1 request will be exported.");
    expect(countCalls()[0][1].params.started_after).toBe(
      "2026-10-19T17:30:00.000Z",
    );
  });
});
