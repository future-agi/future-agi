import React from "react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleError } from "src/utils/queryErrorHandler";
import {
  useCreateConnection,
  useDeleteConnection,
  usePauseConnection,
  useResumeConnection,
  useStartSlackInstall,
  useSyncNow,
  useUpdateConnection,
  useValidateCredentials,
} from "src/api/integrations";

// A refused integrations request says why exactly once. The credentials
// steps, the sync settings step, the edit dialog and the Slack entry points
// show it inline, so no toast repeats it; the connection page's buttons and
// Delete, which have no inline message, show it in one toast.

const mockEnqueueSnackbar = vi.fn();
const mockRequest = vi.fn();
const mockGet = vi.fn();

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
      post: (...args) => mockRequest(...args),
      patch: (...args) => mockRequest(...args),
      delete: (...args) => mockRequest(...args),
    },
  };
});

const MESSAGE = "S3 error (SignatureDoesNotMatch): the secret does not match.";

// What src/utils/axios.js rejects with: the response body plus statusCode.
const refused = {
  status: false,
  result: MESSAGE,
  message: MESSAGE,
  statusCode: 400,
};

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={
      new QueryClient({
        mutationCache: new MutationCache({ onError: handleError }),
        defaultOptions: { mutations: { retry: false } },
      })
    }
  >
    {children}
  </QueryClientProvider>
);

const toastsOf = (message) =>
  mockEnqueueSnackbar.mock.calls.filter(([text]) => text === message).length;

async function refuse(useHook, variables) {
  const { result } = renderHook(() => useHook(), { wrapper });
  act(() => result.current.mutate(variables));
  await waitFor(() => expect(result.current.isError).toBe(true));
  return result.current.error;
}

beforeEach(() => {
  mockEnqueueSnackbar.mockReset();
  mockRequest.mockReset();
  mockRequest.mockRejectedValue(refused);
  mockGet.mockReset();
  mockGet.mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url.includes("sync-logs")
          ? {
              sync_logs: mockRequest.mock.calls.length
                ? [
                    {
                      id: "completed-run",
                      completed_at: "2026-10-27T00:00:00Z",
                    },
                  ]
                : [],
            }
          : { status: "active" },
      },
    }),
  );
});

describe("A refused integrations request says why once", () => {
  it.each([
    ["Validate & Continue", useValidateCredentials, { platform: "s3" }],
    ["Connect Integration", useCreateConnection, { platform: "datadog" }],
    ["Save Changes", useUpdateConnection, { id: "c1", data: {} }],
    ["Connect Slack", useStartSlackInstall, undefined],
  ])(
    "%s shows the refusal inline and in no toast",
    async (_name, useHook, variables) => {
      const error = await refuse(useHook, variables);
      expect(error.result).toBe(MESSAGE);
      expect(toastsOf(MESSAGE)).toBe(0);
    },
  );

  it.each([
    ["Delete", useDeleteConnection],
    ["Sync Now", useSyncNow],
    ["Pause", usePauseConnection],
    ["Resume", useResumeConnection],
  ])("%s shows the refusal in one toast", async (_name, useHook) => {
    await refuse(useHook, "c1");
    expect(toastsOf(MESSAGE)).toBe(1);
  });
});

describe("Sync success is evidenced by the server", () => {
  it.each([undefined, "", "   "])(
    "does not invent a toast for %s",
    async (message) => {
      mockRequest.mockResolvedValue({ data: { result: { message } } });
      const { result } = renderHook(() => useSyncNow(), { wrapper });
      act(() => result.current.mutate("c1"));
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
    },
  );
  it.each(["Sync triggered.", "Retry started."])(
    "shows exactly %s",
    async (message) => {
      mockRequest.mockResolvedValue({ data: { result: { message } } });
      const { result } = renderHook(() => useSyncNow(), { wrapper });
      act(() => result.current.mutate("c1"));
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(mockEnqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(message, {
        variant: "success",
      });
    },
  );
});
