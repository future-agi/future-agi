import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import {
  BATCH_TERMINAL_STATUSES,
  useBatchStatus,
  useCancelBatch,
  useSubmitBatch,
} from "./useBatchJobs";

const { get, post, mockEnqueueSnackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  mockEnqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      batch: {
        submit: (id) => `/agentcc/gateways/${id}/submit-batch/`,
        get: (id) => `/agentcc/gateways/${id}/get-batch/`,
        cancel: (id) => `/agentcc/gateways/${id}/cancel-batch/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

const wrapper = ({ children }) => {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};

// The shared axios instance rejects with the response body spread flat
// (message, statusCode), never with an axios error carrying `response`.
const forbidden = {
  status: false,
  statusCode: 403,
  message:
    "Only an organization Owner or Admin can change the gateway's configuration.",
};
const gatewayRefusal = {
  status: false,
  statusCode: 400,
  message: 'Gateway error: Gateway returned 503: {"error":"unavailable"}',
};

describe("console batch jobs", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    mockEnqueueSnackbar.mockClear();
  });

  it.each([
    [
      "submitting",
      useSubmitBatch,
      { gatewayId: "default", requests: [{ model: "m" }] },
      "Only an organization Owner or Admin can submit batch jobs.",
    ],
    [
      "cancelling",
      useCancelBatch,
      { gatewayId: "default", batchId: "batch-1" },
      "Only an organization Owner or Admin can cancel batch jobs.",
    ],
  ])(
    "says only an Owner or Admin may be %s when the server refuses the role",
    async (_, useHook, input, expected) => {
      post.mockRejectedValue(forbidden);
      const { result } = renderHook(() => useHook(), { wrapper });
      result.current.mutate(input);
      await waitFor(() =>
        expect(mockEnqueueSnackbar).toHaveBeenCalledWith(expected, {
          variant: "error",
        }),
      );
    },
  );

  it.each([
    [
      "submit",
      useSubmitBatch,
      { gatewayId: "default", requests: [{ model: "m" }] },
    ],
    ["cancel", useCancelBatch, { gatewayId: "default", batchId: "batch-1" }],
  ])("shows the server's reason when a %s fails", async (_, useHook, input) => {
    post.mockRejectedValue(gatewayRefusal);
    const { result } = renderHook(() => useHook(), { wrapper });
    result.current.mutate(input);
    await waitFor(() =>
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(gatewayRefusal.message, {
        variant: "error",
      }),
    );
  });

  it("sends the requests and concurrency, and returns the batch with its org", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: { batch_id: "batch-1", org_id: "org-1", status: "processing" },
      },
    });
    const { result } = renderHook(() => useSubmitBatch(), { wrapper });
    const submitted = await result.current.mutateAsync({
      gatewayId: "default",
      requests: [{ model: "m" }],
      maxConcurrency: 3,
    });
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/default/submit-batch/",
      { requests: [{ model: "m" }], max_concurrency: 3 },
    );
    expect(submitted).toEqual({
      batch_id: "batch-1",
      org_id: "org-1",
      status: "processing",
    });
  });

  it("stops polling at every status the batch can end in", () => {
    expect([...BATCH_TERMINAL_STATUSES].sort()).toEqual([
      "cancelled",
      "completed",
      "failed",
    ]);
  });

  it("stops polling once the batch has failed", async () => {
    get.mockResolvedValue({
      data: { status: true, result: { batch_id: "batch-1", status: "failed" } },
    });
    const { result } = renderHook(() => useBatchStatus("default", "batch-1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data?.status).toBe("failed"));
    const calls = get.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 3200));
    expect(get.mock.calls.length).toBe(calls);
  }, 10000);
});
