import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSyncNow } from "src/api/integrations";
import { IntegrationsScopeContext } from "../scope";

const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), toast: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: h.get, post: h.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.toast }));

const ID = "local-connection";
const response = (result) => ({ data: { result } });
const flush = async (ms = 10) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
let client;
let logs;
let invalidate;
let writeCache;

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

const mount = () => renderHook(() => useSyncNow(ID), { wrapper: Wrapper });
const expectSettled = () => {
  expect(
    client
      .getMutationCache()
      .getAll()
      .some(({ state }) => state.status === "pending"),
  ).toBe(false);
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  logs = [];
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false, gcTime: Infinity },
    },
  });
  invalidate = vi.spyOn(client, "invalidateQueries");
  writeCache = vi.spyOn(client, "setQueryData");
  h.get.mockImplementation((url) =>
    Promise.resolve(
      response(
        url.includes("sync-logs")
          ? { sync_logs: logs }
          : { id: ID, status: "active" },
      ),
    ),
  );
  h.post.mockResolvedValue(response({ message: "Sync triggered." }));
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.useRealTimers();
});

describe("Sync operation owns its lifetime from the synchronous call", () => {
  it.each(["mutate", "mutateAsync"])(
    "%s immediately followed by unmount never starts a snapshot or dispatch",
    async (method) => {
      const view = mount();
      let pending;
      act(() => {
        pending = view.result.current[method](ID);
        pending?.catch(() => {});
      });
      // Do not flush a microtask between the user's call and cleanup.
      view.unmount();
      await flush();
      expect(h.get).not.toHaveBeenCalled();
      expect(h.post).not.toHaveBeenCalled();
      expect(h.toast).not.toHaveBeenCalled();
      expectSettled();
      if (pending)
        await expect(pending).rejects.toMatchObject({ code: "ERR_CANCELED" });
      await flush(300000);
      expect(h.get).not.toHaveBeenCalled();
      expect(h.toast).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("keeps a queued old connection canceled when a new connection starts before preparation", async () => {
    const view = renderHook(({ id }) => useSyncNow(id), {
      wrapper: Wrapper,
      initialProps: { id: ID },
    });
    let old;
    act(() => {
      old = view.result.current.mutateAsync(ID);
      old.catch(() => {});
    });
    view.rerender({ id: "another-local-connection" });
    act(() => view.result.current.mutate("another-local-connection"));
    await flush();
    await expect(old).rejects.toMatchObject({ code: "ERR_CANCELED" });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post.mock.calls[0][0]).toContain("another-local-connection");
    expect(h.get.mock.calls.every(([, config]) => !config.signal.aborted)).toBe(
      true,
    );
    expect(view.result.current.isPending).toBe(true);
  });

  it("aborts an in-flight snapshot and ignores its late result", async () => {
    let resolveSnapshot;
    h.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSnapshot = resolve;
        }),
    );
    const view = mount();
    act(() => view.result.current.mutate(ID));
    await flush();
    const signal = h.get.mock.calls[0][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await flush();
    expectSettled();
    resolveSnapshot(response({ sync_logs: [] }));
    await flush();
    expect(h.post).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
    expect(writeCache).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("aborts mid-dispatch and never polls or reconciles a late response after remount", async () => {
    let resolvePost;
    h.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePost = resolve;
        }),
    );
    const old = mount();
    act(() => old.result.current.mutate(ID));
    await flush();
    const oldSignal = h.post.mock.calls[0][2].signal;
    old.unmount();
    expect(oldSignal.aborted).toBe(true);
    await flush();
    expectSettled();
    const next = mount();
    const stoppedReads = h.get.mock.calls.length;
    resolvePost(response({ message: "Sync triggered." }));
    await flush();
    expect(h.get).toHaveBeenCalledTimes(stoppedReads);
    expect(h.toast).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(writeCache).not.toHaveBeenCalled();
    expect(next.result.current.isPending).toBe(false);
    // The fresh mount owns its own operation and can still complete normally.
    h.post.mockImplementationOnce(() => {
      logs = [{ id: "new", completed_at: "2026-10-27T00:00:00Z" }];
      return Promise.resolve(response({ message: "Sync triggered." }));
    });
    act(() => next.result.current.mutate(ID));
    await flush();
    expect(h.post).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(next.result.current.isPending).toBe(false);
    expect(h.toast).toHaveBeenCalledTimes(1);
  });

  it("does not resurrect a pre-preparation operation when the same connection remounts", async () => {
    const old = mount();
    act(() => old.result.current.mutate(ID));
    old.unmount();
    const next = mount();
    act(() => next.result.current.mutate(ID));
    await flush();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.toast).toHaveBeenCalledTimes(1);
    next.unmount();
    await flush();
    expectSettled();
    const stoppedReads = h.get.mock.calls.length;
    await flush(300000);
    expect(h.get).toHaveBeenCalledTimes(stoppedReads);
    expect(h.toast).toHaveBeenCalledTimes(1);
  });

  it("registers the single five-minute deadline before any asynchronous preparation", async () => {
    const view = mount();
    let pending;
    act(() => {
      pending = view.result.current.mutateAsync(ID);
      pending.catch(() => {});
      // Advance only timers: preparation has not had a microtask to run yet.
      vi.advanceTimersByTime(300000);
    });
    await flush();
    expect(h.get).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
    await expect(pending).rejects.toMatchObject({ code: "ETIMEDOUT" });
    expectSettled();
  });

  it("cancels a queued GET-only recovery when unmounted in the same turn", async () => {
    const view = mount();
    h.post.mockImplementationOnce(() => new Promise(() => {}));
    act(() => view.result.current.mutate(ID));
    await flush(300000);
    expect(view.result.current.completionUnconfirmed).toBe(true);
    h.get.mockClear();
    h.toast.mockClear();
    let recovery;
    act(() => {
      recovery = view.result.current.checkStatus(ID);
    });
    recovery?.catch(() => {});
    view.unmount();
    await flush();
    expect(h.get).not.toHaveBeenCalled();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.toast).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    await flush(300000);
    expect(h.get).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("preserves failure callback arguments when preparation rejects", async () => {
    const error = new Error("Snapshot unavailable");
    const onError = vi.fn();
    const onSettled = vi.fn();
    h.get.mockRejectedValueOnce(error);
    const view = mount();
    act(() => view.result.current.mutate(ID, { onError, onSettled }));
    await flush();
    expect(view.result.current.variables).toBe(ID);
    expect(onError.mock.calls[0]).toEqual([error, ID, undefined]);
    expect(onSettled.mock.calls[0]).toEqual([undefined, error, ID, undefined]);
    expect(h.post).not.toHaveBeenCalled();
  });

  it("preserves public mutation variables and callback arguments", async () => {
    const onSuccess = vi.fn();
    const onSettled = vi.fn();
    h.post.mockImplementationOnce(() => {
      logs = [{ id: "new", completed_at: "2026-10-27T00:00:00Z" }];
      return Promise.resolve(response({ message: "Sync triggered." }));
    });
    const view = mount();
    act(() => view.result.current.mutate(ID, { onSuccess, onSettled }));
    await flush();
    expect(view.result.current.variables).toBe(ID);
    expect(onSuccess.mock.calls[0][1]).toBe(ID);
    expect(onSettled.mock.calls[0][2]).toBe(ID);
  });
});
