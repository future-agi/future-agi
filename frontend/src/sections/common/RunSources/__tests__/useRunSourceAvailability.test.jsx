import React from "react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { act, renderHook } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  CAPABILITIES_QUERY_KEY,
  useCapabilities,
} from "src/hooks/useCapabilities";
import { handleError } from "src/utils/queryErrorHandler";
import { useRunSourceAvailability } from "../useRunSourceAvailability";
import {
  AVAILABILITY_STATUS,
  QUIET_RETRY_MS,
  sourceAvailable,
} from "../runSourceAvailability";

const real = vi.hoisted(() => ({ useCapabilities: null }));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  real.useCapabilities = actual.useCapabilities;
  return { ...actual, useCapabilities: vi.fn(actual.useCapabilities) };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const WORKSPACE = "00000000-0000-4000-8000-000000000001";
const OTHER_WORKSPACE = "00000000-0000-4000-8000-000000000002";
const YES = { available: true, reason: null };
const capabilitiesBody = (workspaceId = WORKSPACE) => ({
  deployment_flavor: "oss",
  features: {},
  experiment_run_sources: {
    workspace_id: workspaceId,
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: {
        available: false,
        reason: "A2A experiments are not built in this release.",
      },
      mcp_tool: {
        available: false,
        reason: "MCP tool experiments are not built in this release.",
      },
    },
  },
});

// The fields of a TanStack query result the hook reads.
const answer = (fields = {}) => ({
  data: undefined,
  isError: false,
  isFetching: false,
  errorUpdateCount: 0,
  refetch: vi.fn(),
  ...fields,
});
const answers = (fields) =>
  vi.mocked(useCapabilities).mockReturnValue(answer(fields));

beforeEach(() => {
  vi.mocked(useCapabilities).mockImplementation(real.useCapabilities);
  vi.mocked(enqueueSnackbar).mockClear();
});
afterEach(() => {
  vi.useRealTimers();
  delete axios.defaults.headers.common["X-Workspace-Id"];
});

describe("useRunSourceAvailability", () => {
  it("offers no Already running source while the report loads", () => {
    answers({ isFetching: true });
    const { result } = renderHook(() => useRunSourceAvailability());
    expect(result.current.status).toBe(AVAILABILITY_STATUS.LOADING);
    expect(sourceAvailable("http", result.current)).toBe(false);
    expect(sourceAvailable("agent", result.current)).toBe(true);
  });

  it("reads the server's answer", () => {
    answers({ data: capabilitiesBody() });
    const { result } = renderHook(() => useRunSourceAvailability());
    expect(result.current.status).toBe(AVAILABILITY_STATUS.READY);
    expect(result.current.workspaceId).toBe(WORKSPACE);
    expect(sourceAvailable("http", result.current)).toBe(true);
    expect(sourceAvailable("a2a", result.current)).toBe(false);
    expect(sourceAvailable("mcp_tool", result.current)).toBe(false);
  });

  it("keeps the same answer between renders", () => {
    const fields = answer({ data: capabilitiesBody() });
    vi.mocked(useCapabilities).mockReturnValue(fields);
    const { result, rerender } = renderHook(() => useRunSourceAvailability());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });

  it("retries a failed read quietly, with no toast", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    answers({ isError: true, errorUpdateCount: 1, refetch });
    const { result, rerender } = renderHook(() => useRunSourceAvailability());
    expect(result.current.status).toBe(AVAILABILITY_STATUS.FAILED);
    expect(sourceAvailable("http", result.current)).toBe(false);
    expect(sourceAvailable("prompt", result.current)).toBe(true);
    act(() => vi.advanceTimersByTime(QUIET_RETRY_MS - 1));
    expect(refetch).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(refetch).toHaveBeenCalledExactlyOnceWith({ cancelRefetch: false });
    // The next failure schedules the next quiet try.
    answers({ isError: true, errorUpdateCount: 2, refetch });
    rerender();
    act(() => vi.advanceTimersByTime(QUIET_RETRY_MS));
    expect(refetch).toHaveBeenCalledTimes(2);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("does not retry while it is not reading", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    answers({ isError: true, errorUpdateCount: 1, refetch });
    renderHook(() => useRunSourceAvailability({ enabled: false }));
    act(() => vi.advanceTimersByTime(QUIET_RETRY_MS * 2));
    expect(refetch).not.toHaveBeenCalled();
  });

  it("reads a server that sends no report as unknown, without retrying", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    answers({ data: { features: {} }, refetch });
    const { result } = renderHook(() => useRunSourceAvailability());
    expect(result.current.status).toBe(AVAILABILITY_STATUS.FAILED);
    expect(sourceAvailable("http", result.current)).toBe(false);
    act(() => vi.advanceTimersByTime(QUIET_RETRY_MS * 2));
    expect(refetch).not.toHaveBeenCalled();
  });

  it("reads again once when the report belongs to another workspace", () => {
    axios.defaults.headers.common["X-Workspace-Id"] = OTHER_WORKSPACE;
    const refetch = vi.fn();
    answers({ data: capabilitiesBody(WORKSPACE), refetch });
    const { result, rerender } = renderHook(() => useRunSourceAvailability());
    expect(refetch).toHaveBeenCalledExactlyOnceWith({ cancelRefetch: false });
    answers({ data: capabilitiesBody(WORKSPACE), isFetching: true, refetch });
    rerender();
    expect(result.current.status).toBe(AVAILABILITY_STATUS.LOADING);
    expect(sourceAvailable("http", result.current)).toBe(false);
    // The server answers for the workspace the request named; that answer
    // is what create will meet, so it is used, and not read again.
    answers({ data: capabilitiesBody(OTHER_WORKSPACE), refetch });
    rerender();
    expect(result.current.status).toBe(AVAILABILITY_STATUS.READY);
    expect(result.current.workspaceId).toBe(OTHER_WORKSPACE);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("marks the capabilities read as handled, so a failed read never toasts", () => {
    const get = vi.spyOn(axios, "get").mockReturnValue(new Promise(() => {}));
    const client = new QueryClient({
      queryCache: new QueryCache({ onError: handleError }),
    });
    /* eslint-disable react/prop-types */
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    /* eslint-enable react/prop-types */
    try {
      renderHook(() => useRunSourceAvailability(), { wrapper });
      const query = client
        .getQueryCache()
        .find({ queryKey: CAPABILITIES_QUERY_KEY });
      expect(query.options.meta).toEqual({ errorHandled: true });
      // What the app's QueryCache does with a failed read of this query.
      handleError({ statusCode: 500, result: "Server error" }, query);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    } finally {
      get.mockRestore();
      client.clear();
    }
  });
});
