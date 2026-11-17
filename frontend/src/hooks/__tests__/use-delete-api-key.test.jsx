/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  del: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

// Mock only the axios default instance; keep the real `endpoints` so the URLs
// are the genuine ones.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { delete: mocks.del } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));

const { endpoints } = await import("src/utils/axios");
const { handleError } = await import("src/utils/queryErrorHandler");
const { DELETE_MODAL_TYPE, useDeleteApiKey } = await import(
  "../use-delete-api-key"
);

const FALLBACK = "We couldn't delete this API key. Try again.";

// What the axios interceptor (src/utils/axios.js) rejects with.
const NO_RESPONSE = {
  message: "Network Error",
  statusCode: undefined,
  transportCode: "ERR_NETWORK",
  responseHeaders: {},
};
const NO_RESULT = { detail: "Internal Server Error", statusCode: 500 };
const RATE_LIMITED = { result: "Request was throttled.", statusCode: 429 };
const WITH_RESULT = { result: "API key not found.", statusCode: 404 };
const GATEWAY_REJECTED = {
  response: {
    status: 401,
    data: { code: "gateway_request_rejected", message: "upstream said no" },
  },
};
const GATEWAY_REMEDY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";

// The app's wiring (src/app.jsx): every failed mutation also reaches the
// app-level handleError, which runs before the hook's own onError.
function renderDeleteHook() {
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useDeleteApiKey(), { wrapper });
  return { ...hook, queryClient };
}

// Both onError callbacks have run once the mutation is marked as failed.
const lastMutationStatus = (queryClient) =>
  queryClient.getMutationCache().getAll().at(-1)?.state.status;

async function deleteWith(type, rejection) {
  mocks.del.mockRejectedValueOnce(rejection);
  const { result, queryClient } = renderDeleteHook();
  act(() => result.current.setOpenDeleteModal({ id: "key-1", type }));
  act(() => result.current.handleDeleteApiKey());
  await waitFor(() => expect(lastMutationStatus(queryClient)).toBe("error"));
  return result;
}

const errorToasts = () =>
  mocks.enqueueSnackbar.mock.calls.filter(
    ([, options]) => options?.variant === "error",
  );

const TYPES = [
  ["a provider key", DELETE_MODAL_TYPE.NORMAL],
  ["a custom model", DELETE_MODAL_TYPE.CUSTOM],
];

describe("useDeleteApiKey: a failed delete always says so, exactly once", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe.each(TYPES)("deleting %s", (_label, type) => {
    it("a request that gets no response shows the fallback and keeps the dialog open", async () => {
      const result = await deleteWith(type, NO_RESPONSE);

      expect(errorToasts()).toEqual([[FALLBACK, { variant: "error" }]]);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(result.current.openDeleteModal).toEqual({ id: "key-1", type });
      expect(result.current.isDeleting).toBe(false);
    });

    it("a response without a result shows the fallback once", async () => {
      await deleteWith(type, NO_RESULT);

      expect(errorToasts()).toEqual([[FALLBACK, { variant: "error" }]]);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("a 429, which the app-level handler leaves alone, shows the fallback once", async () => {
      await deleteWith(type, RATE_LIMITED);

      expect(errorToasts()).toEqual([[FALLBACK, { variant: "error" }]]);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("an API error with a result keeps the app-level message and adds nothing", async () => {
      await deleteWith(type, WITH_RESULT);

      expect(errorToasts()).toEqual([
        ["API key not found.", { variant: "error" }],
      ]);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("a gateway authentication error keeps the app-level remedy and adds nothing", async () => {
      await deleteWith(type, GATEWAY_REJECTED);

      expect(errorToasts()).toEqual([[GATEWAY_REMEDY, { variant: "error" }]]);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });
  });

  it("a successful delete still confirms it and closes the dialog", async () => {
    mocks.del.mockResolvedValueOnce({ data: {} });
    const { result } = renderDeleteHook();
    act(() =>
      result.current.setOpenDeleteModal({
        id: "key-1",
        type: DELETE_MODAL_TYPE.NORMAL,
      }),
    );
    act(() => result.current.handleDeleteApiKey());

    await waitFor(() => expect(result.current.openDeleteModal).toBeNull());
    expect(mocks.del).toHaveBeenCalledWith(
      endpoints.develop.apiKey.delete("key-1"),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
      "API key has been deleted",
      { variant: "success" },
    );
    expect(errorToasts()).toEqual([]);
  });
});
