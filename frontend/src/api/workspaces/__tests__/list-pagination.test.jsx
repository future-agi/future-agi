/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  org: { currentOrganizationId: "org-1", isReady: true },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => h.get(...args) },
  endpoints: { workspaces: { list: "/accounts/workspaces/list/" } },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => h.org,
}));

import {
  useWorkspaceFromList,
  useWorkspacesList,
  workspacesListKey,
} from "../list";

const workspace = (id) => ({ id: `ws-${id}`, user_ws_level: 8 });
const firstRows = Array.from({ length: 100 }, (_, i) => workspace(i + 1));
const page = (current_page, results, hasNext = false) => ({
  data: {
    current_page,
    results,
    next: hasNext ? `?page=${current_page + 1}&limit=100` : null,
  },
});
const firstPage = page(1, firstRows, true);
const listKey = [...workspacesListKey, "org-1"];

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

let client;
const Wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

function seedFirstPage() {
  client.setQueryData(listKey, { pages: [firstPage], pageParams: [1] });
}

describe("H15-C25-004 workspace lookup pagination", () => {
  beforeEach(() => {
    h.get.mockReset();
    h.org = { currentOrganizationId: "org-1", isReady: true };
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
  });

  afterEach(() => {
    cleanup();
    client.clear();
  });

  it.each(["ws-201", "missing"])(
    "searches all remaining pages before settling the lookup for %s",
    async (id) => {
      const last = deferred();
      h.get
        .mockResolvedValueOnce(firstPage)
        .mockResolvedValueOnce(
          page(2, Array.from({ length: 100 }, (_, i) => workspace(i + 101)), true),
        )
        .mockReturnValueOnce(last.promise);
      const states = [];
      const { result } = renderHook(() => {
        const value = useWorkspaceFromList(id);
        states.push(value);
        return value;
      }, { wrapper: Wrapper });

      await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
      expect(result.current.workspace).toBeNull();
      expect(result.current.isLoading).toBe(true);
      expect(result.current.isError).toBe(false);
      // No intermediate successful first/second page may become a denial.
      expect(states.every((value) => value.isLoading)).toBe(true);

      await act(async () => last.resolve(page(3, [workspace(201)])));
      await waitFor(() => expect(result.current.isLoading).toBe(false));
      expect(result.current.workspace).toEqual(
        id === "ws-201" ? workspace(201) : null,
      );
      expect(result.current.isError).toBe(false);
      expect(h.get.mock.calls.map(([, { params }]) => params)).toEqual([
        { page: 1, limit: 100 },
        { page: 2, limit: 100 },
        { page: 3, limit: 100 },
      ]);
    },
  );

  it.each(["switcher", "found", "disabled", "no-id"])(
    "does not fetch more pages for a %s consumer",
    async (kind) => {
      seedFirstPage();
      const useConsumer = kind === "switcher"
        ? () => useWorkspacesList()
        : () => useWorkspaceFromList(
          kind === "found" ? "ws-1" : kind === "no-id" ? undefined : "ws-101",
          { enabled: kind !== "disabled" },
        );
      const { result } = renderHook(useConsumer, { wrapper: Wrapper });

      await act(async () => {});
      expect(result.current.isLoading).toBe(false);
      expect(h.get).not.toHaveBeenCalled();
    },
  );

  it("stops on a next-page error and resumes only after an explicit retry", async () => {
    h.get
      .mockResolvedValueOnce(firstPage)
      .mockRejectedValueOnce(new Error("second page unavailable"));
    const { result } = renderHook(() => useWorkspaceFromList("ws-101"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isLoading).toBe(false);
    expect(result.current.workspace).toBeNull();
    expect(h.get).toHaveBeenCalledTimes(2);

    h.get
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce(page(2, [workspace(101)], true));
    await act(async () => { await result.current.refetch(); });
    await waitFor(() => expect(result.current.workspace).toEqual(workspace(101)));
    expect(result.current.isError).toBe(false);
    expect(result.current.isLoading).toBe(false);
    // Finding the target stops pagination even if there are more pages.
    expect(h.get.mock.calls.map(([, { params }]) => params.page)).toEqual([1, 2, 1, 2]);
  });

  it("shares one next-page request between simultaneous lookup consumers", async () => {
    seedFirstPage();
    const next = deferred();
    h.get.mockReturnValue(next.promise);
    const { result } = renderHook(() => ({
      first: useWorkspaceFromList("ws-101"),
      second: useWorkspaceFromList("ws-101"),
    }), { wrapper: Wrapper });

    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    expect(result.current.first.isLoading).toBe(true);
    expect(result.current.second.isLoading).toBe(true);
    await act(async () => next.resolve(page(2, [workspace(101)])));
    await waitFor(() => {
      expect(result.current.first.workspace).toEqual(workspace(101));
      expect(result.current.second.workspace).toEqual(workspace(101));
    });
    expect(h.get).toHaveBeenCalledTimes(1);
  });

  it("waits for an in-flight list refetch before requesting the missing page", async () => {
    seedFirstPage();
    const refresh = deferred();
    const next = deferred();
    h.get.mockReturnValueOnce(refresh.promise).mockReturnValueOnce(next.promise);
    const list = renderHook(() => useWorkspacesList(), { wrapper: Wrapper });
    let refetch;
    act(() => { refetch = list.result.current.refetch(); });
    const { result } = renderHook(() => useWorkspaceFromList("ws-101"), {
      wrapper: Wrapper,
    });

    expect(h.get).toHaveBeenCalledTimes(1);
    expect(result.current.isLoading).toBe(true);
    // The refetch returns unchanged data: pagination must still resume.
    await act(async () => {
      refresh.resolve(firstPage);
      await refetch;
    });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    await act(async () => next.resolve(page(2, [workspace(101)])));
    await waitFor(() => expect(result.current.workspace).toEqual(workspace(101)));
  });

  it("does not resolve a lookup from the previous organization's late page", async () => {
    seedFirstPage();
    const oldPage = deferred();
    h.get.mockReturnValueOnce(oldPage.promise).mockResolvedValueOnce(page(1, []));
    const { result, rerender } = renderHook(() => useWorkspaceFromList("ws-101"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));

    h.org = { currentOrganizationId: "org-2", isReady: true };
    rerender();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => oldPage.resolve(page(2, [workspace(101)])));
    expect(result.current.workspace).toBeNull();
    expect(result.current.isError).toBe(false);
    expect(client.getQueryData([...workspacesListKey, "org-2"]).pages).toEqual([
      page(1, []),
    ]);
    expect(h.get).toHaveBeenCalledTimes(2);
  });
});
