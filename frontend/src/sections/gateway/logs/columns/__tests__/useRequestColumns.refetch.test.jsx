import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useRequestColumns from "../useRequestColumns";
import { DEFAULT_CONFIG } from "../columnModel";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    gateway: { customProperties: { list: "/agentcc/custom-properties/" } },
  },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useAuthContext: () => ({ user: { id: "user-a" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1", isReady: true }),
}));

const KEY = "agentcc.requestLogs.columns.v1:user-a:org-1:requests";
const TENANT = { name: "tenant", organization: "org-1" };
let client;

function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node.isRequired };

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  get.mockReset();
  localStorage.clear();
  localStorage.setItem(
    KEY,
    JSON.stringify({
      ...DEFAULT_CONFIG,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
    }),
  );
});
afterEach(() => {
  client.clear();
  localStorage.clear();
});

const columnIds = (result) => result.current.columns.map((column) => column.id);

describe("useRequestColumns with the real declarations query", () => {
  it("retains cached custom columns through a rejected refetch and reconciles recovery", async () => {
    get.mockResolvedValueOnce({ data: { result: [TENANT] } });
    const { result } = renderHook(() => useRequestColumns(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(columnIds(result)).toContain("metadata:tenant"));
    const record = localStorage.getItem(KEY);

    get.mockRejectedValueOnce(new Error("temporary outage"));
    await act(async () => {
      await result.current.retryDeclarations();
    });
    await waitFor(() => expect(result.current.declarationStatus).toBe("error"));
    expect(client.getQueryData(["agentcc-custom-properties", {}])).toEqual([
      TENANT,
    ]);
    expect(columnIds(result)).toContain("metadata:tenant");
    expect(result.current.declarationsAvailable).toBe(true);
    expect(result.current.unavailableCustomCount).toBe(0);
    expect(localStorage.getItem(KEY)).toBe(record);

    get.mockResolvedValueOnce({ data: { result: [] } });
    await act(async () => {
      await result.current.retryDeclarations();
    });
    await waitFor(() =>
      expect(result.current.declarationStatus).toBe("success"),
    );
    expect(columnIds(result)).not.toContain("metadata:tenant");
    expect(result.current.stale).toEqual(["metadata:tenant"]);
    expect(localStorage.getItem(KEY)).toBe(record);
  });

  it("does not expose custom or stale names when the initial fetch fails", async () => {
    get.mockRejectedValueOnce(new Error("initial outage"));
    const { result } = renderHook(() => useRequestColumns(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.declarationStatus).toBe("error"));
    expect(columnIds(result)).not.toContain("metadata:tenant");
    expect(result.current.declarationsAvailable).toBe(false);
    expect(result.current.unavailableCustomCount).toBe(1);
    expect(result.current.pickerEntries.custom).toEqual([]);
    expect(result.current.stale).toEqual([]);
  });

  it("retains an empty cached snapshot when its refresh fails", async () => {
    get.mockResolvedValueOnce({ data: { result: [] } });
    const { result } = renderHook(() => useRequestColumns(), {
      wrapper: Wrapper,
    });
    await waitFor(() =>
      expect(result.current.declarationStatus).toBe("success"),
    );
    get.mockRejectedValueOnce(new Error("temporary outage"));
    await act(async () => {
      await result.current.retryDeclarations();
    });
    await waitFor(() => expect(result.current.declarationStatus).toBe("error"));
    expect(client.getQueryData(["agentcc-custom-properties", {}])).toEqual([]);
    expect(result.current.declarationsAvailable).toBe(true);
    expect(result.current.unavailableCustomCount).toBe(0);
    expect(result.current.stale).toEqual(["metadata:tenant"]);
  });
});
