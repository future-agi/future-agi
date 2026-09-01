import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  projectGetCallLogs: "/tracer/trace/list_voice_calls/",
  agentGetCallLogs: vi.fn((id, version) => {
    if (!id || !version) {
      throw new Error("missing path param");
    }
    return `/simulate/agent-definitions/${id}/versions/${version}/call-executions/`;
  }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: axiosMocks.get,
  },
  endpoints: {
    project: {
      getCallLogs: axiosMocks.projectGetCallLogs,
    },
    agentDefinitions: {
      getCallLogs: axiosMocks.agentGetCallLogs,
    },
  },
}));

function createWrapper(onClient) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  onClient?.(client);

  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  Wrapper.propTypes = {
    children: PropTypes.node,
  };

  return Wrapper;
}

describe("useCallLogs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axiosMocks.get.mockResolvedValue({ data: { results: [], total_pages: 1 } });
  });

  it("does not build agent-version URLs before the version exists", () => {
    expect(() =>
      renderHook(
        () =>
          useCallLogs({
            module: "simulate",
            id: "agent-1",
            version: undefined,
            page: 1,
            pageLimit: 25,
            params: {},
          }),
        { wrapper: createWrapper() },
      ),
    ).not.toThrow();

    expect(axiosMocks.agentGetCallLogs).not.toHaveBeenCalled();
    expect(axiosMocks.get).not.toHaveBeenCalled();
  });

  it("does not require an agent version for project voice-call queries", async () => {
    renderHook(
      () =>
        useCallLogs({
          module: "project",
          id: "project-1",
          version: undefined,
          page: 1,
          pageLimit: 25,
          params: { project_id: "project-1" },
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(axiosMocks.get).toHaveBeenCalledTimes(1));
    expect(axiosMocks.agentGetCallLogs).not.toHaveBeenCalled();
    expect(axiosMocks.get).toHaveBeenCalledWith(axiosMocks.projectGetCallLogs, {
      params: {
        page: 1,
        page_size: 25,
        project_id: "project-1",
      },
    });
  });

  it("keeps exact project cursor pages out of implicit refetch and retry paths", async () => {
    let queryClient;
    const wrapper = createWrapper((client) => {
      queryClient = client;
    });
    const { result } = renderHook(
      () =>
        useCallLogs({
          module: "project",
          id: "project-1",
          version: undefined,
          page: 1,
          pageLimit: 25,
          params: { project_id: "project-1" },
        }),
      { wrapper },
    );

    await waitFor(() => expect(axiosMocks.get).toHaveBeenCalledTimes(1));
    const query = queryClient.getQueryCache().find({
      queryKey: result.current.queryKey,
      exact: true,
    });

    expect(query.options).toEqual(
      expect.objectContaining({
        staleTime: Infinity,
        refetchOnWindowFocus: false,
        refetchOnMount: false,
        refetchOnReconnect: false,
        retry: false,
      }),
    );
  });

  it("uses an opaque voice continuation without also sending a numbered page", async () => {
    renderHook(
      () =>
        useCallLogs({
          module: "project",
          id: "project-1",
          page: 2,
          pageLimit: 25,
          params: { project_id: "project-1" },
          paginationParams: {
            cursor_mode: true,
            cursor: "signed-voice-page-2",
            page_size: 25,
          },
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(axiosMocks.get).toHaveBeenCalledTimes(1));
    expect(axiosMocks.get).toHaveBeenCalledWith(axiosMocks.projectGetCallLogs, {
      params: {
        project_id: "project-1",
        cursor_mode: true,
        cursor: "signed-voice-page-2",
        page_size: 25,
      },
    });
  });

  it("preserves exact voice totals across short cursor responses", async () => {
    const pagination = createListCursorPagination({
      pageParam: "page",
      pageOffset: 1,
    });
    axiosMocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "call-1" }],
            has_more: true,
            next_cursor: "after-call-1",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            results: Array.from({ length: 15 }, (_, index) => ({
              id: `call-${index + 2}`,
            })),
            count: 16,
            count_is_lower_bound: false,
            total_pages: 1,
            has_more: false,
            next_cursor: null,
          },
        },
      });

    const paginationParams = pagination.requestParams(0, { page_size: 25 });
    const { result } = renderHook(
      () =>
        useCallLogs({
          module: "project",
          id: "project-1",
          page: 1,
          pageLimit: 25,
          params: { project_id: "project-1" },
          paginationParams,
          paginationRevision: 0,
          cursorPagination: pagination,
          paginationGeneration: pagination.generation(),
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.data?.results).toHaveLength(16));
    expect(axiosMocks.get).toHaveBeenNthCalledWith(
      1,
      axiosMocks.projectGetCallLogs,
      {
        params: {
          project_id: "project-1",
          cursor_mode: true,
          page: 1,
          page_size: 25,
        },
        signal: expect.any(AbortSignal),
      },
    );
    expect(axiosMocks.get).toHaveBeenNthCalledWith(
      2,
      axiosMocks.projectGetCallLogs,
      {
        params: {
          project_id: "project-1",
          page_size: 25,
          cursor_mode: true,
          cursor: "after-call-1",
        },
        signal: expect.any(AbortSignal),
      },
    );
    expect(result.current.data.__exactPage).toEqual(
      expect.objectContaining({
        pending: false,
        isLastPage: true,
        canPrefetch: false,
      }),
    );
    expect(result.current.data).toEqual(
      expect.objectContaining({
        count: 16,
        count_is_lower_bound: false,
        total_pages: 1,
      }),
    );
  });

  it("keeps the searched custom-property filter on voice p1 and p2", async () => {
    const pagination = createListCursorPagination({
      pageParam: "page",
      pageOffset: 1,
    });
    const propertyFilters = JSON.stringify([
      {
        column_id: "prompt_slug",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_op: "equals",
          filter_value: "rejected",
        },
      },
    ]);
    axiosMocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "voice-call-1" }],
            count: 2,
            count_is_lower_bound: false,
            has_more: true,
            next_cursor: "signed-voice-property-page-2",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "voice-call-2" }],
            count: 2,
            count_is_lower_bound: false,
            has_more: false,
            next_cursor: null,
          },
        },
      });

    const { result, rerender } = renderHook(
      ({ page }) =>
        useCallLogs({
          module: "project",
          id: "project-colly",
          page,
          pageLimit: 1,
          params: {
            project_id: "project-colly",
            filters: propertyFilters,
          },
          cursorPagination: pagination,
          paginationGeneration: pagination.generation(),
        }),
      { initialProps: { page: 1 }, wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "voice-call-1" }]),
    );
    rerender({ page: 2 });
    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "voice-call-2" }]),
    );

    expect(axiosMocks.get.mock.calls[0][1].params).toEqual({
      project_id: "project-colly",
      filters: propertyFilters,
      cursor_mode: true,
      page: 1,
      page_size: 1,
    });
    expect(axiosMocks.get.mock.calls[1][1].params).toEqual({
      project_id: "project-colly",
      filters: propertyFilters,
      cursor_mode: true,
      cursor: "signed-voice-property-page-2",
      page_size: 1,
    });
    expect(axiosMocks.get.mock.calls[1][1].params).not.toHaveProperty("page");
    expect(result.current.data.results[0].id).not.toBe("voice-call-1");
  });

  it("returns to a completed voice page after its query-cache entry is removed", async () => {
    const pagination = createListCursorPagination({
      pageParam: "page",
      pageOffset: 1,
    });
    let queryClient;
    const wrapper = createWrapper((client) => {
      queryClient = client;
    });
    axiosMocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "voice-page-1" }],
            has_more: true,
            next_cursor: "signed-voice-page-2",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "voice-page-2" }],
            has_more: false,
            next_cursor: null,
          },
        },
      });

    const { result, rerender } = renderHook(
      ({ page }) =>
        useCallLogs({
          module: "project",
          id: "project-1",
          page,
          pageLimit: 1,
          params: { project_id: "project-1" },
          cursorPagination: pagination,
          paginationGeneration: pagination.generation(),
        }),
      { initialProps: { page: 1 }, wrapper },
    );

    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "voice-page-1" }]),
    );
    const firstPageQueryKey = result.current.queryKey;

    rerender({ page: 2 });
    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "voice-page-2" }]),
    );
    queryClient.removeQueries({ queryKey: firstPageQueryKey, exact: true });

    rerender({ page: 1 });
    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "voice-page-1" }]),
    );
    expect(axiosMocks.get).toHaveBeenCalledTimes(2);
  });

  it("retries voice page one once without cursor fields on a legacy API", async () => {
    const pagination = createListCursorPagination({
      pageParam: "page",
      pageOffset: 1,
    });
    axiosMocks.get
      .mockRejectedValueOnce({
        response: {
          status: 400,
          data: {
            attr: "cursor_mode",
            detail: "cursor_mode: Unknown field.",
            details: { cursor_mode: ["Unknown field."] },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            results: [{ id: "legacy-call" }],
            count: 1,
            total_pages: 1,
          },
        },
      });

    const paginationParams = pagination.requestParams(0, { page_size: 25 });
    const { result } = renderHook(
      () =>
        useCallLogs({
          module: "project",
          id: "project-1",
          page: 1,
          pageLimit: 25,
          params: { project_id: "project-1" },
          paginationParams,
          cursorPagination: pagination,
          paginationGeneration: pagination.generation(),
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data?.results).toEqual([{ id: "legacy-call" }]),
    );
    expect(axiosMocks.get).toHaveBeenCalledTimes(2);
    expect(axiosMocks.get.mock.calls[0][1].params).toEqual({
      project_id: "project-1",
      page_size: 25,
      cursor_mode: true,
      page: 1,
    });
    expect(axiosMocks.get.mock.calls[1][1].params).toEqual({
      project_id: "project-1",
      page_size: 25,
      page: 1,
    });
    expect(pagination.mode()).toBe("numbered");
  });

  it("does not prefetch agent call logs without an agent version", () => {
    const queryClient = { prefetchQuery: vi.fn() };

    prefetchCallLogs(queryClient, {
      module: "simulate",
      id: "agent-1",
      version: undefined,
      page: 1,
      pageLimit: 25,
      params: {},
    });

    expect(queryClient.prefetchQuery).not.toHaveBeenCalled();
    expect(axiosMocks.agentGetCallLogs).not.toHaveBeenCalled();
  });
});

describe("getCallLogsColumnDefs", () => {
  it("keeps voice metric columns visible while page-size changes refetch rows", () => {
    const headers = getCallLogsColumnDefs([], true, null, "project")
      .filter((column) => !column.hide)
      .map((column) => column.headerName);

    expect(headers).toEqual(
      expect.arrayContaining([
        "Call Details",
        "Status",
        "Duration",
        "Avg Latency",
        "Turn Count",
        "Tokens",
        "Cost",
      ]),
    );
  });

  it("uses the canonical grid label for all 15 filterable voice fields", () => {
    const columnsByField = new Map(
      getCallLogsColumnDefs([], false, null, "project").map((column) => [
        column.field,
        column,
      ]),
    );

    expect(VOICE_CALL_FILTER_FIELDS).toHaveLength(15);
    VOICE_CALL_FILTER_FIELDS.forEach((field) => {
      expect(columnsByField.get(field.responseKey)?.headerName).toBe(
        field.columnLabel || field.label,
      );
    });
  });
});

import {
  getCallLogsColumnDefs,
  getAgentLatencyFilterValue,
  prefetchCallLogs,
  useCallLogs,
} from "../helper";
import { createListCursorPagination } from "src/sections/projects/LLMTracing/listCursorPagination";
import { VOICE_CALL_FILTER_FIELDS } from "src/sections/projects/LLMTracing/voiceCallFilterFields";

// The Avg Latency cell displays `avg_agent_latency_ms || turnLatencyAverage`,
// but only the former is what this column filters on — `turnLatencyAverage` is
// a separate backend column (aliased `response_time`). Offering the filter when
// the fallback is on screen would filter a number the row never showed.
describe("getAgentLatencyFilterValue", () => {
  it("returns the value when the cell is showing avg_agent_latency_ms", () => {
    expect(
      getAgentLatencyFilterValue({ data: { avg_agent_latency_ms: 820 } }),
    ).toBe(820);
    expect(
      getAgentLatencyFilterValue({
        data: { avg_agent_latency_ms: 820, turnLatencyAverage: 640 },
      }),
    ).toBe(820);
  });

  it("returns null when the cell is showing the turnLatencyAverage fallback", () => {
    // 0 passes the wrapper's null/undefined/"" guard, so without this the click
    // would apply `avg_agent_latency_ms equals 0` against a cell reading 640ms.
    expect(
      getAgentLatencyFilterValue({
        data: { avg_agent_latency_ms: 0, turnLatencyAverage: 640 },
      }),
    ).toBeNull();
    expect(
      getAgentLatencyFilterValue({
        data: { avg_agent_latency_ms: null, turnLatencyAverage: 640 },
      }),
    ).toBeNull();
  });

  it("returns null when there is no latency at all", () => {
    expect(getAgentLatencyFilterValue({ data: {} })).toBeNull();
    expect(getAgentLatencyFilterValue({})).toBeNull();
    expect(
      getAgentLatencyFilterValue({ data: { avg_agent_latency_ms: "n/a" } }),
    ).toBeNull();
  });
});
