import React from "react";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleError } from "src/utils/queryErrorHandler";
import {
  useDeleteConnection,
  useIntegrationConnection,
  useIntegrationConnections,
  useSyncLogs,
} from "src/api/integrations";

// Each outcome is reported once. A successful Delete says "Integration
// deleted" and nothing else: the deleted connection's own page is not read
// again (it would answer 404). A failed read of a connection, its sync history
// or the Integrations list is shown on the page, so no toast repeats it.

const mockEnqueueSnackbar = vi.fn();
const mockGet = vi.fn();
const mockDelete = vi.fn();

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: (...args) => mockGet(...args),
      delete: (...args) => mockDelete(...args),
    },
  };
});

const ID = "c0a80101-0000-4000-8000-000000000005";
const NOT_FOUND = {
  status: false,
  result: "Integration connection not found.",
  message: "Integration connection not found.",
  statusCode: 404,
};
const FAILED = {
  status: false,
  result: "Failed to list integration connections.",
  message: "Failed to list integration connections.",
  statusCode: 500,
};

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const toasts = () => mockEnqueueSnackbar.mock.calls.map(([text]) => text);

beforeEach(() => {
  client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mockEnqueueSnackbar.mockReset();
  mockGet.mockReset();
  mockDelete.mockReset();
});

describe("Integrations toasts", () => {
  it("a successful Delete from the connection's page says only that", async () => {
    let deleted = false;
    mockGet.mockImplementation((url) =>
      deleted && url.includes(ID)
        ? Promise.reject(NOT_FOUND)
        : Promise.resolve({
            data: {
              result: url.includes(ID) ? { id: ID } : { connections: [] },
            },
          }),
    );
    mockDelete.mockImplementation(() => {
      deleted = true;
      return Promise.resolve({ data: { result: { deleted: true } } });
    });
    const { result } = renderHook(
      () => ({
        page: useIntegrationConnection(ID),
        list: useIntegrationConnections({ meta: { errorHandled: true } }),
        remove: useDeleteConnection(),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.page.isSuccess).toBe(true));

    act(() => result.current.remove.mutate(ID));
    await waitFor(() => expect(result.current.remove.isSuccess).toBe(true));
    await waitFor(() =>
      expect(
        mockGet.mock.calls.filter(([url]) => !url.includes(ID)).length,
      ).toBe(2),
    );

    expect(toasts()).toEqual(["Integration deleted"]);
    expect(result.current.page.isError).toBe(false);
  });

  it.each([
    ["a connection", () => useIntegrationConnection(ID), NOT_FOUND],
    ["its sync history", () => useSyncLogs(ID), NOT_FOUND],
  ])(
    "a failed read of %s is shown on the page only",
    async (_n, useHook, err) => {
      mockGet.mockRejectedValue(err);
      const { result } = renderHook(useHook, { wrapper });
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(toasts()).toEqual([]);
    },
  );

  it("a failed list read is toasted only where no page shows it", async () => {
    mockGet.mockRejectedValue(FAILED);
    const page = renderHook(
      () => useIntegrationConnections({ meta: { errorHandled: true } }),
      { wrapper },
    );
    await waitFor(() => expect(page.result.current.isError).toBe(true));
    expect(toasts()).toEqual([]);

    // Error Feed's alert settings read the same list and show no inline
    // message for it, so the one toast stays.
    client.clear();
    const alerts = renderHook(() => useIntegrationConnections(), { wrapper });
    await waitFor(() => expect(alerts.result.current.isError).toBe(true));
    expect(toasts()).toHaveLength(1);
  });
});
