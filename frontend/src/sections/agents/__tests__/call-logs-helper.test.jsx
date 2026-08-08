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

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

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
});

import {
  getCallLogsColumnDefs,
  prefetchCallLogs,
  useCallLogs,
} from "../helper";
import { createListCursorPagination } from "src/sections/projects/LLMTracing/listCursorPagination";
