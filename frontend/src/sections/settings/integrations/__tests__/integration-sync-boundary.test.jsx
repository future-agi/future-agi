import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useIntegrationConnection,
  useIntegrationConnections,
  useSyncLogs,
  useSyncNow,
} from "src/api/integrations";
import { IntegrationsScopeContext } from "../scope";

const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), toast: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: h.get, post: h.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.toast }));

const ID = "local-connection";
const response = (result) => Promise.resolve({ data: { result } });
const flush = async (ms = 10) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
let client;
let dispatched;
let terminal;
let requests;
let readCounts;

// Intentionally ignore AbortSignal: the operation must settle even if a
// transport/adapter never acknowledges cancellation or its own timeout.
function stalled(config, honorTimeout = false) {
  requests.push(config);
  return new Promise((_resolve, reject) => {
    if (honorTimeout && config.timeout) {
      setTimeout(() => reject(new Error("Read timed out")), config.timeout);
    }
  });
}

// eslint-disable-next-line react/prop-types
function Wrapper({ children }) {
  return (
    <QueryClientProvider client={client}>
      <IntegrationsScopeContext.Provider
        value={{ workspaceId: "workspace-local" }}
      >
        {children}
      </IntegrationsScopeContext.Provider>
    </QueryClientProvider>
  );
}

function mount() {
  return renderHook(
    () => {
      useIntegrationConnection(ID);
      useIntegrationConnections();
      useSyncLogs(ID);
      return useSyncNow();
    },
    { wrapper: Wrapper },
  );
}

async function start() {
  const view = mount();
  await flush();
  act(() => view.result.current.mutate(ID));
  await flush();
  expect(h.post).toHaveBeenCalledTimes(1);
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  dispatched = false;
  terminal = false;
  requests = [];
  readCounts = new Map();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  h.get.mockImplementation((url, config) => {
    if (dispatched) {
      const count = (readCounts.get(url) || 0) + 1;
      readCounts.set(url, count);
      if (!terminal || count > 1 || url.endsWith("/connections/")) {
        return stalled(config);
      }
    }
    const connection = { id: ID, status: "active" };
    if (url.includes("sync-logs")) {
      return response({
        sync_logs: dispatched
          ? [{ id: "new", completed_at: "2026-10-27T00:00:00Z" }]
          : [],
      });
    }
    return response(
      url.endsWith("/connections/")
        ? { connections: [connection] }
        : connection,
    );
  });
  h.post.mockImplementation(() => {
    dispatched = true;
    return response({ message: "Sync triggered." });
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.useRealTimers();
});

describe("Sync completion cancellation boundary", () => {
  it.each(["snapshot", "dispatch", "polling", "reconciliation"])(
    "coalesces mutate and mutateAsync re-entry throughout %s, then releases at the deadline",
    async (phase) => {
      const { result } = mount();
      await flush();
      terminal = phase === "reconciliation";
      if (phase === "snapshot") {
        h.get.mockImplementation((_url, config) => stalled(config));
      }
      if (phase === "dispatch") {
        h.post.mockImplementation((_url, _body, config) => stalled(config));
      }
      let first;
      act(() => {
        first = result.current.mutateAsync(ID);
        // Observe rejected timeouts immediately, avoiding unhandled rejections.
        first.catch(() => {});
      });
      await flush();
      const originalRequests = [...requests];
      let repeated;
      act(() => {
        result.current.mutate(ID);
        repeated = result.current.mutateAsync(ID);
        repeated.catch(() => {});
      });
      await flush();
      expect(repeated).toBe(first);
      expect(h.post).toHaveBeenCalledTimes(phase === "snapshot" ? 0 : 1);
      expect(originalRequests.every(({ signal }) => !signal.aborted)).toBe(
        true,
      );
      expect(result.current.isPending).toBe(true);
      await flush(300000);
      expect(result.current.isPending).toBe(false);
      expect(requests.every(({ signal }) => signal.aborted)).toBe(true);
      // Let the next operation take a fresh snapshot before stalling again.
      dispatched = false;
      readCounts.clear();
      let next;
      act(() => {
        next = result.current.mutateAsync(ID);
        next.catch(() => {});
      });
      expect(next).not.toBe(first);
      await flush();
      expect(result.current.isPending).toBe(true);
      expect(h.post).toHaveBeenCalledTimes(phase === "snapshot" ? 0 : 2);
    },
  );

  it("releases a canceled connection without letting its late POST disrupt the next connection", async () => {
    let resolvePost;
    const requestsById = [];
    h.post.mockImplementation((_url, _body, config) => {
      requestsById.push(config);
      return new Promise((resolve) => {
        resolvePost = resolve;
      });
    });
    const { result, rerender } = renderHook(({ id }) => useSyncNow(id), {
      wrapper: Wrapper,
      initialProps: { id: ID },
    });
    act(() => result.current.mutate(ID));
    await flush();
    const resolvePrevious = resolvePost;
    rerender({ id: "another-local-connection" });
    await flush();
    expect(requestsById[0].signal.aborted).toBe(true);
    act(() => result.current.mutate("another-local-connection"));
    await flush();
    expect(h.post).toHaveBeenCalledTimes(2);
    resolvePrevious({ data: { result: { message: "Sync triggered." } } });
    await flush();
    expect(h.toast).not.toHaveBeenCalled();
    expect(requestsById[1].signal.aborted).toBe(false);
    expect(result.current.isPending).toBe(true);
    await flush(300000);
    expect(result.current.isPending).toBe(false);
  });

  it.each([false, true])(
    "settles stalled polling and final reads (transport timeout honored: %s)",
    async (honorTimeout) => {
      const initialGet = h.get.getMockImplementation();
      h.get.mockImplementation((url, config) =>
        dispatched ? stalled(config, honorTimeout) : initialGet(url, config),
      );
      const { result } = await start();
      expect(result.current.isPending).toBe(true);
      await flush(300000);
      expect(result.current.isPending).toBe(false);
      expect(h.toast).toHaveBeenCalledWith(
        expect.stringContaining("completion could not be confirmed"),
        { variant: "warning" },
      );
      expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
      expect(client.isFetching()).toBe(0);
    },
  );

  it("includes final active connection, history and list refetches in the original deadline", async () => {
    terminal = true;
    const { result } = await start();
    expect(requests.length).toBeGreaterThan(0);
    expect(result.current.isPending).toBe(true);
    await flush(300000);
    expect(result.current.isPending).toBe(false);
    expect(client.isFetching()).toBe(0);
    expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
    const stopped = h.get.mock.calls.length;
    await flush(10000);
    expect(h.get).toHaveBeenCalledTimes(stopped);
  });

  it("does not give late reconciliation a fresh five-minute deadline", async () => {
    const initialGet = h.get.getMockImplementation();
    const seen = new Set();
    terminal = true;
    h.get.mockImplementation((url, config) => {
      if (!dispatched) return initialGet(url, config);
      if (seen.has(url) || url.endsWith("/connections/"))
        return stalled(config);
      seen.add(url);
      return new Promise((resolve) => {
        setTimeout(() => resolve(initialGet(url, config)), 295000);
      });
    });
    const { result } = await start();
    await flush(295000);
    expect(result.current.isPending).toBe(true);
    expect(requests.length).toBeGreaterThan(0);
    await flush(5000);
    expect(result.current.isPending).toBe(false);
    expect(client.isFetching()).toBe(0);
  });

  it.each([false, true])(
    "navigation cancels stalled %s terminal reconciliation without waiting for the deadline",
    async (isTerminal) => {
      terminal = isTerminal;
      const view = await start();
      view.unmount();
      await flush();
      expect(client.getMutationCache().getAll().at(-1).state.status).not.toBe(
        "pending",
      );
      expect(client.isFetching()).toBe(0);
      expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
      expect(h.toast).not.toHaveBeenCalledWith(expect.anything(), {
        variant: "warning",
      });
    },
  );

  it("cancels when navigation changes the connection without unmounting the hook", async () => {
    const { result, rerender } = renderHook(({ id }) => useSyncNow(id), {
      wrapper: Wrapper,
      initialProps: { id: ID },
    });
    act(() => result.current.mutate(ID));
    await flush();
    expect(result.current.isPending).toBe(true);
    rerender({ id: "another-local-connection" });
    await flush();
    expect(result.current.isPending).toBe(false);
    expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
    expect(h.toast).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "warning",
    });
  });

  it("bounds the pre-dispatch snapshot even when the read never settles", async () => {
    const { result } = mount();
    await flush();
    h.get.mockImplementation((_url, config) => stalled(config));
    act(() => result.current.mutate(ID));
    await flush(300010);
    expect(result.current.isPending).toBe(false);
    expect(h.post).not.toHaveBeenCalled();
    expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
  });

  it("bounds dispatch and aborts its request rather than locking Sync Now forever", async () => {
    h.post.mockImplementation((_url, _body, config) => stalled(config));
    const { result } = await start();
    await flush(300000);
    expect(result.current.isPending).toBe(false);
    expect(requests.every(({ signal }) => signal?.aborted)).toBe(true);
  });
});
