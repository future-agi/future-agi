import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const h = vi.hoisted(() => ({
  org: { currentOrganizationId: null, isReady: false },
  queryResult: { isLoading: false, isError: false, data: undefined },
  lastOptions: null,
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => h.org,
}));

vi.mock("@tanstack/react-query", () => ({
  useInfiniteQuery: (options) => {
    h.lastOptions = options;
    return h.queryResult;
  },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { workspaces: { list: "/workspaces/" } },
}));

import { useWorkspacesList, useWorkspaceFromList } from "../list";

// Review comment 5 on PR #2000: spreading the v5 result object reads every
// property, which marks them all tracked and defeats the render optimization.
// Returning an explicit set keeps sidebar consumers off the re-render path for
// transitions they never read.
const CONTRACT = [
  "data",
  "fetchNextPage",
  "isPending",
  "isFetchingNextPage",
  "isError",
  "isLoading",
];

describe("useWorkspacesList — returns an explicit set, not the whole query", () => {
  beforeEach(() => {
    h.org = { currentOrganizationId: "org-1", isReady: true };
    h.queryResult = {
      data: [{ id: "ws-1" }],
      fetchNextPage: () => {},
      isPending: false,
      isFetchingNextPage: false,
      isError: false,
      isLoading: false,
      // properties no consumer reads — these are the ones whose transitions
      // were re-rendering the sidebar on every background refetch
      isFetching: true,
      fetchStatus: "fetching",
      dataUpdatedAt: 123,
      refetch: () => {},
    };
    h.lastOptions = null;
  });

  it("exposes exactly the properties its consumers read", () => {
    const { result } = renderHook(() => useWorkspacesList());
    expect(Object.keys(result.current).sort()).toEqual([...CONTRACT].sort());
  });

  it("does not leak untracked query internals", () => {
    const { result } = renderHook(() => useWorkspacesList());
    ["isFetching", "fetchStatus", "dataUpdatedAt", "refetch"].forEach((k) =>
      expect(result.current[k]).toBeUndefined(),
    );
  });

  it("still carries the values consumers depend on", () => {
    const { result } = renderHook(() => useWorkspacesList());
    expect(result.current.data).toEqual([{ id: "ws-1" }]);
    expect(typeof result.current.fetchNextPage).toBe("function");
    expect(result.current.isPending).toBe(false);
  });

  it("useWorkspaceFromList adds workspace and keeps the same shape", () => {
    const { result } = renderHook(() => useWorkspaceFromList("ws-1"));
    expect(Object.keys(result.current).sort()).toEqual(
      [...CONTRACT, "workspace"].sort(),
    );
    expect(result.current.workspace).toEqual({ id: "ws-1" });
  });
});

describe("useWorkspacesList — loading state tracks the same value as enabled", () => {
  beforeEach(() => {
    h.org = { currentOrganizationId: null, isReady: false };
    // What v5 reports for a disabled query: pending, never loading.
    h.queryResult = {
      isLoading: false,
      isError: false,
      isPending: true,
      data: undefined,
    };
    h.lastOptions = null;
  });

  it("reports loading while the org is still being resolved", () => {
    const { result } = renderHook(() => useWorkspacesList());
    expect(h.lastOptions.enabled).toBe(false);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isError).toBe(false);
  });

  it("reports an error once the org resolved to nothing, rather than hanging", () => {
    // seedFromMembership sets isReady(true) in its catch and in the no-orgs
    // case while leaving the id null. `enabled` keeps the query off, so it can
    // never report success or failure itself — reporting loading here would
    // leave the role guard on a LoadingScreen and the switcher on skeletons
    // for good.
    h.org = { currentOrganizationId: null, isReady: true };
    const { result } = renderHook(() => useWorkspacesList());
    expect(h.lastOptions.enabled).toBe(false);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isError).toBe(true);
    // The switcher renders skeletons on isPending, which a disabled query
    // reports forever.
    expect(result.current.isPending).toBe(false);
  });

  it("defers to the query once an org id exists", () => {
    h.org = { currentOrganizationId: "org-1", isReady: true };
    const { result } = renderHook(() => useWorkspacesList());
    expect(h.lastOptions.enabled).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });

  it("does not force loading when the caller disabled the hook", () => {
    h.org = { currentOrganizationId: null, isReady: false };
    const { result } = renderHook(() => useWorkspacesList({ enabled: false }));
    expect(h.lastOptions.enabled).toBe(false);
    expect(result.current.isLoading).toBe(false);
  });
});
