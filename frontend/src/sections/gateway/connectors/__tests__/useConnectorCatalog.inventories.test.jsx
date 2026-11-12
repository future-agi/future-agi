import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CATALOG_QUERY_KEY,
  useAddConnector,
  useConnectorCatalog,
  useDisconnectAccount,
  useRemoveConnector,
  useStoreConnectorKey,
} from "../hooks/useConnectorCatalog";
import {
  MY_CONNECTIONS_QUERY_KEY,
  useMyConnections,
} from "../hooks/useMyConnections";
import { connected, listOf } from "../__fixtures__/my-connections";

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    gateway: {
      connectorCatalog: "/agentcc/connectors/catalog/",
      connectorAdd: (id) => `/agentcc/connectors/apps/${id}/add/`,
      mcpConnections: "/agentcc/mcp/connect/",
      mcpCredentials: "/agentcc/mcp/credentials/",
    },
  },
}));
vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ catalog: true }),
  useMCPReleaseGuard: () => () => {},
}));

const CATALOG = "/agentcc/connectors/catalog/";
const ACCOUNTS = "/agentcc/mcp/connect/";
const OLD_ROWS = [connected("docs")];
const accountKey = [...MY_CONNECTIONS_QUERY_KEY, true];
const inactiveKey = [...MY_CONNECTIONS_QUERY_KEY, false];

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

let client;
beforeEach(() => {
  vi.resetAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  api.get.mockImplementation(async (url) => {
    if (url === CATALOG) return { data: { results: [], next: null } };
    if (url === ACCOUNTS) return { data: listOf(OLD_ROWS) };
    throw new Error(`unexpected GET ${url}`);
  });
  api.post.mockResolvedValue({ data: { result: { changed: true } } });
  api.delete.mockResolvedValue({ data: { result: { changed: true } } });
});
afterEach(() => {
  cleanup();
  client.clear();
});

async function mounted(useOperation) {
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => {
    const catalog = useConnectorCatalog({ gatewayId: "gw", upstreamOAuth: true });
    const { data, isFetching } = useMyConnections({ k2Apps: true });
    const operation = useOperation("gw");
    // Query 5 tracks property reads. Subscribe during render, as the page
    // does, rather than first reading isFetching after the refetch started.
    return { catalog, accounts: { data, isFetching }, operation };
  }, { wrapper });
  await waitFor(() => {
    expect(hook.result.current.catalog.isSuccess).toBe(true);
    expect(hook.result.current.accounts.data).toEqual(OLD_ROWS);
  });
  return hook;
}

const operations = [
  ["remove app", useRemoveConnector, (op) => op.mutateAsync({ appId: "docs" })],
  ["disconnect account", useDisconnectAccount, (op) => op.mutateAsync({
    url: "/agentcc/mcp/connect/docs/",
  })],
  ["add app", useAddConnector, (op) => op.add("docs", { provider: "docs-mcp" })],
  ["store shared key", useStoreConnectorKey, (op) => op.store(
    "docs", { kind: "bearer" }, "synthetic-test-value",
  )],
];

describe("R3-H6-002 inventory refresh", () => {
  it.each(operations)(
    "%s refetches both inventories and stays pending until accounts finish",
    async (_name, useOperation, run) => {
      const hook = await mounted(useOperation);
      client.setQueryData(inactiveKey, OLD_ROWS);
      const read = deferred();
      api.get.mockImplementation(async (url) => url === ACCOUNTS
        ? read.promise
        : { data: { results: [], next: null } });
      let finished = false;
      let completion;
      act(() => {
        completion = run(hook.result.current.operation).then(() => {
          finished = true;
        });
      });
      await waitFor(() => {
        expect(api.get.mock.calls.filter(([url]) => url === ACCOUNTS)).toHaveLength(2);
        expect(api.get.mock.calls.filter(([url]) => url === CATALOG)).toHaveLength(2);
        expect(hook.result.current.operation.isPending).toBe(true);
        expect(client.getQueryState(accountKey).fetchStatus).toBe("fetching");
        // Query's React notification is scheduled after the GET starts.
        expect(hook.result.current.accounts.isFetching).toBe(true);
      });
      expect(finished).toBe(false);
      expect(client.getQueryState(inactiveKey).isInvalidated).toBe(true);
      expect(hook.result.current.accounts.isFetching).toBe(true);
      await act(async () => {
        read.resolve({ data: listOf([]) });
        await completion;
      });
      await waitFor(() => {
        expect(hook.result.current.accounts.data).toEqual([]);
        expect(hook.result.current.operation.isPending).toBe(false);
      });
      expect(finished).toBe(true);
    },
  );

  it("refreshes on a refused removal without discarding the still-connected account", async () => {
    const hook = await mounted(useRemoveConnector);
    const refusal = new Error("Removal refused");
    api.delete.mockRejectedValueOnce(refusal);
    await act(async () => {
      await expect(hook.result.current.operation.mutateAsync({ appId: "docs" }))
        .rejects.toBe(refusal);
    });
    expect(api.get.mock.calls.filter(([url]) => url === ACCOUNTS)).toHaveLength(2);
    expect(api.get.mock.calls.filter(([url]) => url === CATALOG)).toHaveLength(2);
    expect(client.getQueryData(accountKey)).toEqual(OLD_ROWS);
  });

  it.each(["old first", "fresh first"])(
    "replaces the uncached initial accounts read after removal (%s)",
    async (responseOrder) => {
      const initialRead = deferred();
      const freshRead = deferred();
      let reads = 0;
      api.get.mockImplementation(async (url) => {
        if (url === CATALOG) return { data: { results: [], next: null } };
        if (url === ACCOUNTS) {
          reads += 1;
          return reads === 1 ? initialRead.promise : freshRead.promise;
        }
        throw new Error(`unexpected GET ${url}`);
      });
      const observedAccounts = [];
      const unsubscribe = client.getQueryCache().subscribe(() => {
        observedAccounts.push(client.getQueryData(accountKey));
      });
      try {
        const wrapper = ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        );
        // Unlike mounted(), leave the first accounts GET unresolved with no
        // cached data: invalidateQueries alone reuses that in-flight read.
        const hook = renderHook(() => ({
          catalog: useConnectorCatalog({ gatewayId: "gw", upstreamOAuth: true }),
          accounts: useMyConnections({ k2Apps: true }),
          operation: useRemoveConnector("gw"),
        }), { wrapper });
        await waitFor(() => {
          expect(hook.result.current.catalog.isSuccess).toBe(true);
          expect(reads).toBe(1);
          expect(hook.result.current.accounts.isFetching).toBe(true);
        });
        expect(client.getQueryData(accountKey)).toBeUndefined();
        expect(hook.result.current.accounts.data).toBeUndefined();

        let finished = false;
        let removal;
        act(() => {
          removal = hook.result.current.operation.mutateAsync({ appId: "docs" })
            .then(() => { finished = true; });
        });
        // Require a new GET before resolving either response, not merely an
        // invalidation flag or a later focus/remount that repairs stale data.
        await waitFor(() => {
          expect(reads).toBe(2);
          expect(api.get.mock.calls.filter(([url]) => url === CATALOG)).toHaveLength(2);
          expect(hook.result.current.operation.isPending).toBe(true);
        });
        expect(finished).toBe(false);
        expect(hook.result.current.accounts.isFetching).toBe(true);
        expect(client.getQueryData(accountKey)).toBeUndefined();

        const resolveInitialRead = async () => {
          await act(async () => {
            initialRead.resolve({ data: listOf(OLD_ROWS) });
            await initialRead.promise;
          });
        };
        if (responseOrder === "old first") {
          await resolveInitialRead();
          expect(client.getQueryData(accountKey)).toBeUndefined();
          expect(hook.result.current.accounts.data).toBeUndefined();
          expect(hook.result.current.accounts.isFetching).toBe(true);
          expect(hook.result.current.operation.isPending).toBe(true);
          expect(finished).toBe(false);
        }

        await act(async () => {
          freshRead.resolve({ data: listOf([]) });
          await removal;
        });
        await waitFor(() => {
          expect(hook.result.current.accounts.data).toEqual([]);
          expect(hook.result.current.accounts.isFetching).toBe(false);
          expect(hook.result.current.operation.isPending).toBe(false);
        });
        expect(finished).toBe(true);
        if (responseOrder === "fresh first") await resolveInitialRead();

        expect(reads).toBe(2);
        expect(client.getQueryData(accountKey)).toEqual([]);
        expect(hook.result.current.accounts.data).toEqual([]);
        // Observe every cache update so even a transient resurrection fails.
        expect(observedAccounts.filter((rows) => rows?.length)).toEqual([]);
        expect(observedAccounts).toContainEqual([]);
      } finally {
        unsubscribe();
      }
    },
  );

  it("does not resurrect a removed account from a read started before removal", async () => {
    const hook = await mounted(useRemoveConnector);
    const before = deferred();
    const after = deferred();
    let reads = 0;
    api.get.mockImplementation(async (url) => {
      if (url !== ACCOUNTS) return { data: { results: [], next: null } };
      reads += 1;
      return reads === 1 ? before.promise : after.promise;
    });
    let oldRead;
    act(() => {
      oldRead = client.refetchQueries({ queryKey: MY_CONNECTIONS_QUERY_KEY });
    });
    await waitFor(() => expect(reads).toBe(1));
    let removal;
    act(() => {
      removal = hook.result.current.operation.mutateAsync({ appId: "docs" });
    });
    await waitFor(() => expect(reads).toBe(2));
    await act(async () => {
      after.resolve({ data: listOf([]) });
      await removal;
      before.resolve({ data: listOf(OLD_ROWS) });
      await oldRead;
    });
    await waitFor(() => expect(hook.result.current.accounts.data).toEqual([]));
    expect(client.getQueryData(accountKey)).toEqual([]);
  });
});
