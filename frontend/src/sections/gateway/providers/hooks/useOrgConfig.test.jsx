import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { useActivateOrgConfig, useCreateOrgConfig } from "./useOrgConfig";

const { post, mockEnqueueSnackbar } = vi.hoisted(() => ({
  post: vi.fn(),
  mockEnqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post },
  endpoints: {
    gateway: {
      orgConfig: {
        create: "/agentcc/org-configs/",
        activate: (id) => `/agentcc/org-configs/${id}/activate/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

const wrapper = ({ children }) => {
  const client = new QueryClient({
    // The hook reports the error itself; the cache-level handler just keeps
    // React Query's own rejection from surfacing as unhandled.
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};

// The shared axios instance rejects with the response body spread flat, not
// with an axios error: there is no `response` on it.
const rejected = {
  status: false,
  statusCode: 400,
  message:
    "The gateway cannot accept this config. routing.default_strategy: Extra inputs are not permitted",
};

describe("org config mutations", () => {
  beforeEach(() => {
    post.mockReset();
    mockEnqueueSnackbar.mockClear();
  });

  it.each([
    ["activating a version", useActivateOrgConfig, "cfg-1"],
    ["saving a new version", useCreateOrgConfig, { routing: {} }],
  ])(
    "shows the server's reason when %s is rejected",
    async (_, useHook, input) => {
      post.mockRejectedValue(rejected);
      const { result } = renderHook(() => useHook(), { wrapper });

      result.current.mutate(input);

      await waitFor(() =>
        expect(mockEnqueueSnackbar).toHaveBeenCalledWith(rejected.message, {
          variant: "error",
        }),
      );
    },
  );
});
