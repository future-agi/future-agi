import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import IntegrationDetail from "../IntegrationDetail";
import { IntegrationsScopeContext } from "../scope";

const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), toast: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: h.get, post: h.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.toast }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/loading-screen", () => ({ LoadingScreen: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("../EditIntegrationDialog", () => ({ default: () => null }));
vi.mock("../DeleteIntegrationDialog", () => ({ default: () => null }));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ connectionId: "export-1" }),
}));
// jsdom cannot lay out AG Grid. Keep the real history query/component and
// expose the rows it supplies, without loading any remote asset.
vi.mock("ag-grid-react", () => ({
  // eslint-disable-next-line react/prop-types
  AgGridReact: ({ rowData }) => (
    <div data-testid="history-rows">{JSON.stringify(rowData)}</div>
  ),
}));

let client;
let connection;
let logs;
const response = (result) => Promise.resolve({ data: { result } });
const flush = async (ms = 10) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const reads = () => h.get.mock.calls.length;
const deliveries = () =>
  screen.getByText("Request Log Deliveries (Including Repeats)").parentElement;

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  connection = {
    id: "export-1",
    platform: "datadog",
    display_name: "Local export",
    status: "active",
    total_traces_synced: 3,
    backfill_completed: true,
    last_synced_at: "2026-10-01T00:00:00Z",
  };
  logs = [
    {
      id: "previous",
      status: "success",
      completed_at: "2026-10-01T00:00:00Z",
      traces_fetched: 3,
    },
  ];
  h.get.mockImplementation((url) =>
    response(url.includes("sync-logs") ? { sync_logs: logs } : connection),
  );
  h.post.mockImplementation(() => {
    connection = { ...connection, status: "syncing" };
    logs = [{ id: "new-run", status: "partial", completed_at: null }, ...logs];
    return response({ message: "Sync triggered." });
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.useRealTimers();
});

async function page() {
  const view = render(
    <QueryClientProvider client={client}>
      <IntegrationsScopeContext.Provider
        value={{ workspaceId: "workspace-1", canManage: true }}
      >
        <IntegrationDetail />
      </IntegrationsScopeContext.Provider>
    </QueryClientProvider>,
  );
  await flush();
  expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
  return view;
}

async function start() {
  const view = await page();
  fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
  await flush();
  expect(h.post).toHaveBeenCalledTimes(1);
  return view;
}

function finish(status, total = 6) {
  connection = {
    ...connection,
    status: status === "failed" ? "error" : "active",
    total_traces_synced: total,
  };
  logs = [
    {
      id: "new-run",
      status,
      completed_at: "2026-10-27T01:00:00Z",
      traces_fetched: total - 3,
    },
    logs.at(-1),
  ];
}

describe("Sync Now completion refresh", () => {
  it.each([2, 3])(
    "retains the first dispatch and monitor across %s clicks before pending renders",
    async (clicks) => {
      let resolvePost;
      let request;
      // A queued export cannot be undone by aborting the browser's request.
      // This transport deliberately ignores cancellation.
      h.post.mockImplementation((_url, _body, config) => {
        request = config;
        return new Promise((resolve) => {
          resolvePost = resolve;
        });
      });
      await page();
      const button = screen.getByRole("button", { name: "Sync Now" });
      fireEvent.click(button);
      for (let n = 0; n < 30; n += 1) await Promise.resolve();
      expect(h.post).toHaveBeenCalledTimes(1);
      const originalSignal = request.signal;
      // React Query's pending notification has not been delivered yet.
      expect(button).toBeEnabled();
      for (let click = 1; click < clicks; click += 1) {
        fireEvent.click(button);
        for (let n = 0; n < 30; n += 1) await Promise.resolve();
      }
      await flush();
      expect(h.post).toHaveBeenCalledTimes(1);
      expect(originalSignal.aborted).toBe(false);
      expect(button).toBeDisabled();
      resolvePost({ data: { result: { message: "Sync triggered." } } });
      await flush();
      expect(button).toBeDisabled();
      finish("success");
      await flush(4000);
      expect(button).toBeEnabled();
      expect(deliveries()).toHaveTextContent("6");
      expect(h.post).toHaveBeenCalledTimes(1);
      expect(originalSignal.aborted).toBe(true);
      expect(h.toast).toHaveBeenCalledTimes(1);
      // Completion releases the guard for a genuinely new export.
      fireEvent.click(button);
      await flush();
      expect(h.post).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps the original five-minute dispatch deadline after a rapid triple click", async () => {
    const requests = [];
    h.post.mockImplementation((_url, _body, config) => {
      requests.push(config);
      return new Promise(() => {});
    });
    await page();
    const button = screen.getByRole("button", { name: "Sync Now" });
    fireEvent.click(button);
    for (let n = 0; n < 30; n += 1) await Promise.resolve();
    fireEvent.click(button);
    fireEvent.click(button);
    await flush(299990);
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(requests[0].signal.aborted).toBe(false);
    await flush(20);
    expect(requests[0].signal.aborted).toBe(true);
    const retry = screen.getByRole("button", { name: "Check sync status" });
    expect(retry).toBeEnabled();
    finish("success");
    fireEvent.click(retry);
    fireEvent.click(retry);
    fireEvent.click(retry);
    await flush();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
    expect(deliveries()).toHaveTextContent("6");
  });

  it("allows retry after snapshot and dispatch failures without retaining the guard", async () => {
    await page();
    h.get.mockRejectedValueOnce(new Error("Snapshot unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await flush();
    expect(h.post).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
    h.post.mockRejectedValueOnce(new Error("Dispatch unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await flush();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await flush();
    expect(h.post).toHaveBeenCalledTimes(2);
    finish("success");
    await flush(4000);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
  });

  it.each(["success", "no_new_data", "partial", "failed", "rate_limited"])(
    "refreshes badge, controls, deliveries and history after %s, then stops",
    async (status) => {
      await start();
      expect(screen.getByText("Syncing")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sync Now" })).toBeDisabled();
      finish(status, status === "success" ? 6 : 3);
      await flush(4000);
      expect(
        screen.getByText(status === "failed" ? "Error" : "Active"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: status === "failed" ? "Retry" : "Sync Now",
        }),
      ).toBeEnabled();
      expect(deliveries()).toHaveTextContent(status === "success" ? "6" : "3");
      expect(screen.getByTestId("history-rows")).toHaveTextContent(
        '"completed_at":"2026-10-27T01:00:00Z"',
      );
      expect(screen.getByTestId("history-rows")).toHaveTextContent(
        `"status":"${status}"`,
      );
      const stopped = reads();
      await flush(10000);
      expect(reads()).toBe(stopped);
      expect(
        h.get.mock.calls.every(
          ([, config]) => config.headers["X-Workspace-Id"] === "workspace-1",
        ),
      ).toBe(true);
    },
  );

  it("keeps watching while dispatch is queued and catches a run too fast to observe Syncing", async () => {
    h.post.mockImplementation(() => response({ message: "Sync triggered." }));
    await start();
    await flush(4000);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeDisabled();
    finish("success");
    await flush(4000);
    expect(deliveries()).toHaveTextContent("6");
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
  });

  it("does not treat an in-progress partial log or early ACTIVE write as completion", async () => {
    await start();
    connection = { ...connection, status: "active", total_traces_synced: 6 };
    await flush(4000);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeDisabled();
    finish("partial");
    await flush(4000);
    expect(screen.getByTestId("history-rows")).toHaveTextContent(
      '"completed_at":"2026-10-27T01:00:00Z"',
    );
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
  });

  it("bounds polling and explains when completion could not be confirmed", async () => {
    await start();
    await flush(310000);
    expect(h.toast).toHaveBeenCalledWith(
      expect.stringMatching(/refresh.*check|check.*refresh/i),
      { variant: "warning" },
    );
    const stopped = reads();
    await flush(10000);
    expect(reads()).toBe(stopped);
    expect(stopped).toBeLessThan(320);
  });

  it("reports a refresh failure without claiming the dispatched export failed", async () => {
    await start();
    h.get.mockRejectedValueOnce({ message: "Status read unavailable" });
    await flush(4000);
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.toast).toHaveBeenCalledWith(
      expect.stringContaining("completion could not be confirmed"),
      { variant: "warning" },
    );
    expect(h.toast).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "error",
    });
    const stopped = reads();
    await flush(10000);
    expect(reads()).toBe(stopped);
  });

  it("offers a read-only status retry after timing out with a stale Syncing badge", async () => {
    await start();
    h.get.mockImplementation(() => new Promise(() => {}));
    await flush(300000);
    const retry = screen.getByRole("button", { name: "Check sync status" });
    expect(retry).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "completion could not be confirmed",
    );
    finish("success");
    h.get.mockImplementation((url) =>
      response(url.includes("sync-logs") ? { sync_logs: logs } : connection),
    );
    fireEvent.click(retry);
    await flush();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
    expect(deliveries()).toHaveTextContent("6");
  });

  it("keeps cached details and the retry affordance when final refetches fail", async () => {
    await start();
    finish("success");
    const seen = new Set();
    h.get.mockImplementation((url) => {
      if (seen.has(url)) return Promise.reject(new Error("Status unavailable"));
      seen.add(url);
      return response(
        url.includes("sync-logs") ? { sync_logs: logs } : connection,
      );
    });
    await flush(4000);
    expect(
      screen.getByRole("button", { name: "Check sync status" }),
    ).toBeEnabled();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(h.post).toHaveBeenCalledTimes(1);
  });

  it("still reports background read failures outside an ongoing sync", async () => {
    await page();
    h.get.mockRejectedValue(new Error("Status unavailable"));
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["integrations", "connections", "export-1"],
      });
    });
    await flush();
    expect(
      screen.getByText("Failed to load integration: Status unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Sync Now" }),
    ).not.toBeInTheDocument();
  });

  it("keeps status retry available after repeated monitoring timeouts without dispatching twice", async () => {
    await start();
    h.get.mockImplementation(() => new Promise(() => {}));
    await flush(300000);
    fireEvent.click(screen.getByRole("button", { name: "Check sync status" }));
    await flush(300000);
    expect(
      screen.getByRole("button", { name: "Check sync status" }),
    ).toBeEnabled();
    expect(h.post).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch or toast a cancelled pre-dispatch history read", async () => {
    const view = await page();
    h.get.mockImplementationOnce(
      (_url, { signal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener(
            "abort",
            () =>
              reject({ transportCode: "ERR_CANCELED", message: "canceled" }),
            { once: true },
          );
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await flush();
    view.unmount();
    await flush();
    expect(h.post).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("cancels polling when the page is left", async () => {
    const view = await start();
    view.unmount();
    await flush();
    const stopped = reads();
    await flush(10000);
    expect(reads()).toBe(stopped);
    expect(h.toast).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "warning",
    });
  });
});
