import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { handleError } from "src/utils/queryErrorHandler";
import {
  useAddSharedLinkAccess,
  useCreateSharedLink,
  useGetSharedLinks,
  useUpdateSharedLink,
} from "../shared-links";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  endpoints: {
    sharedLinks: {
      list: "/tracer/shared-links/",
      create: "/tracer/shared-links/",
      update: (id) => `/tracer/shared-links/${id}/`,
      addAccess: (id) => `/tracer/shared-links/${id}/access/`,
    },
  },
}));

const axios = (await import("src/utils/axios")).default;

// The app's QueryClient: failed reads and writes go through the global toast.
const makeWrapper = () => {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  return { queryClient, Wrapper };
};

const serverError = { statusCode: 500, result: "Internal error" };

beforeEach(() => {
  vi.clearAllMocks();
});

describe("shared-links error reporting", () => {
  it("leaves a failed link list read to the share dialog", async () => {
    axios.get.mockRejectedValueOnce(serverError);
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useGetSharedLinks("trace", "trace-1"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(queryClient.getQueryCache().getAll().at(-1)?.options.meta).toEqual({
      errorHandled: true,
    });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("leaves a failed link create to the share dialog", async () => {
    axios.post.mockRejectedValueOnce(serverError);
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useCreateSharedLink(), {
      wrapper: Wrapper,
    });

    await expect(
      result.current.mutateAsync({
        resource_type: "trace",
        resource_id: "trace-1",
        access_type: "restricted",
      }),
    ).rejects.toBe(serverError);
    expect(
      queryClient.getMutationCache().getAll().at(-1)?.options.meta,
    ).toEqual({ errorHandled: true });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("leaves a failed access update to the share dialog", async () => {
    axios.patch.mockRejectedValueOnce(serverError);
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateSharedLink(), {
      wrapper: Wrapper,
    });

    await expect(
      result.current.mutateAsync({ id: "link-1", access_type: "public" }),
    ).rejects.toBe(serverError);
    expect(
      queryClient.getMutationCache().getAll().at(-1)?.options.meta,
    ).toEqual({ errorHandled: true });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("still toasts a failed invite through the global handler", async () => {
    axios.post.mockRejectedValueOnce(serverError);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useAddSharedLinkAccess(), {
      wrapper: Wrapper,
    });

    await expect(
      result.current.mutateAsync({ linkId: "link-1", emails: ["a@b.co"] }),
    ).rejects.toBe(serverError);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ variant: "error" }),
    );
  });
});
