import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import { QUERY_FAILED_RETRY_MESSAGE } from "src/utils/queryReadState";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      getCallLogs: "/calls/",
      getTracesForObserveProject: () => "/traces/",
      getSpansForObserveProject: () => "/spans/",
      projectSessionList: () => "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
      getVoiceCallDetail: "/calls/detail/",
      traceSession: "/sessions/",
      projectExperimentDetail: (id) => `/projects/${id}/`,
    },
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/components/inline-audio/inline-row-audio", () => ({
  InlineAudio: () => null,
  RecordingGroup: () => null,
}));

import TaskLivePreview from "../TaskLivePreview";

const PROJECT_ID = "00000000-0000-4000-8000-000000000902";

function PreviewHarness({
  rowType = "spans",
  projectId = PROJECT_ID,
  waitForProjectKind = false,
  filters = [],
}) {
  const { control } = useForm({
    defaultValues: {
      filters,
      startDate: null,
      endDate: null,
      evalsDetails: [],
      rowType,
    },
  });
  return (
    <TaskLivePreview
      control={control}
      projectId={projectId}
      waitForProjectKind={waitForProjectKind}
    />
  );
}

PreviewHarness.propTypes = {
  rowType: PropTypes.string,
  projectId: PropTypes.string,
  waitForProjectKind: PropTypes.bool,
  filters: PropTypes.array,
};

const voiceListPage = ({
  results = [],
  count = results.length,
  hasMore,
  nextCursor,
}) => ({
  data: {
    count,
    count_is_lower_bound: hasMore,
    total_pages: 1,
    current_page: 1,
    next: null,
    previous: null,
    results,
    config: [],
    has_more: hasMore,
    next_cursor: nextCursor,
    query_complete: true,
    query_status: "complete",
  },
});

const observeListPage = ({
  rows = [],
  total = rows.length,
  hasMore,
  nextCursor,
  totalIsLowerBound = false,
}) => ({
  data: {
    status: true,
    result: {
      config: [],
      table: rows,
      metadata: {
        total_rows: total,
        total_rows_is_lower_bound: totalIsLowerBound,
        has_more: hasMore,
        next_cursor: nextCursor,
      },
    },
  },
});

describe("TaskLivePreview sparse cursor continuation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ["spans", "/spans/", "page_number"],
    ["traces", "/traces/", "page_number"],
    ["sessions", "/sessions/", "page_number"],
    ["voiceCalls", "/calls/", "page"],
  ])(
    "submits typed filters to the %s list binding",
    async (rowType, listUrl, pageParam) => {
      const filters = [
        {
          property: "attributes",
          propertyId: "latency_ms",
          registryId: "custom_attribute:latency_ms",
          fieldCategory: "attribute",
          apiColType: "SPAN_ATTRIBUTE",
          filterConfig: {
            filterType: "number",
            filterOp: "greater_than",
            filterValue: 12.5,
          },
        },
        {
          property: "attributes",
          propertyId: "customer_tier",
          registryId: "custom_attribute:customer_tier",
          fieldCategory: "attribute",
          apiColType: "SPAN_ATTRIBUTE",
          filterConfig: {
            filterType: "text",
            filterOp: "in",
            filterValue: ["enterprise", "growth"],
          },
        },
      ];
      mocks.get.mockImplementation(() => new Promise(() => {}));
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });

      render(
        <QueryClientProvider client={queryClient}>
          <PreviewHarness rowType={rowType} filters={filters} />
        </QueryClientProvider>,
      );

      await waitFor(() =>
        expect(mocks.get.mock.calls.some(([url]) => url === listUrl)).toBe(
          true,
        ),
      );
      const [, { params }] = mocks.get.mock.calls.find(
        ([url]) => url === listUrl,
      );
      expect(params).toMatchObject({
        project_id: PROJECT_ID,
        page_size: 1,
        cursor_mode: true,
        [pageParam]: rowType === "voiceCalls" ? 1 : 0,
      });
      expect(JSON.parse(params.filters)).toEqual([
        {
          column_id: "latency_ms",
          property_id: "custom_attribute:latency_ms",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 12.5,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
        {
          column_id: "customer_tier",
          property_id: "custom_attribute:customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "in",
            filter_value: ["enterprise", "growth"],
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ]);
    },
  );

  it("waits for simulator project kind instead of starting disposable lists", async () => {
    let resolveProjectDetails;
    const pendingProjectDetails = new Promise((resolve) => {
      resolveProjectDetails = resolve;
    });
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}/`) return pendingProjectDetails;
      if (url === "/calls/") {
        return voiceListPage({
          results: [{ id: "voice-row", trace_id: "voice-trace" }],
          hasMore: false,
          nextCursor: null,
        });
      }
      if (url === "/calls/detail/") {
        return { data: { status: true, result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" waitForProjectKind />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(
          ([url]) => url === `/projects/${PROJECT_ID}/`,
        ),
      ).toHaveLength(1),
    );
    expect(
      mocks.get.mock.calls.filter(
        ([url]) => url === "/calls/" || url === "/spans/",
      ),
    ).toHaveLength(0);

    await act(async () => {
      resolveProjectDetails({ data: { result: { source: "simulator" } } });
      await pendingProjectDetails;
    });

    await screen.findByText("Row 1 of 1");
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/calls/"),
    ).toHaveLength(1);
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/spans/"),
    ).toHaveLength(0);
  });

  it("shows a retryable error when project kind cannot be resolved", async () => {
    let detailCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}/`) {
        detailCalls += 1;
        if (detailCalls === 1) throw new Error("private project lookup detail");
        return { data: { result: { source: "simulator" } } };
      }
      if (url === "/calls/") {
        return voiceListPage({
          results: [{ id: "voice-row", trace_id: "voice-trace" }],
          hasMore: false,
          nextCursor: null,
        });
      }
      if (url === "/calls/detail/") {
        return { data: { status: true, result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" waitForProjectKind />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    expect(
      mocks.get.mock.calls.filter(
        ([url]) => url === "/calls/" || url === "/spans/",
      ),
    ).toHaveLength(0);

    await act(async () => {
      screen.getByRole("button", { name: "Retry search" }).click();
    });
    await screen.findByText("Row 1 of 1");
    expect(detailCalls).toBe(2);
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/calls/"),
    ).toHaveLength(1);
    expect(screen.queryByText("private project lookup detail")).toBeNull();
  });

  it("finds a sparse span on the final allowed continuation", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") {
        const callIndex = spanListCalls;
        spanListCalls += 1;
        if (callIndex < 12) {
          return {
            data: {
              status: true,
              result: {
                config: [],
                table: [],
                metadata: {
                  total_rows: 0,
                  has_more: true,
                  next_cursor: `checkpoint-${callIndex}`,
                  total_rows_is_lower_bound: true,
                },
              },
            },
          };
        }
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-rare",
                  trace_id: "trace-rare",
                  input: "rare preview value",
                },
              ],
              metadata: {
                has_more: false,
                next_cursor: null,
                total_rows: 1,
              },
            },
          },
        };
      }
      if (url === "/traces/trace-rare/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-rare" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-rare",
                    input: "rare preview value",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    await screen.findByText("Row 1 of 1");

    const spanRequests = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    );
    expect(spanRequests).toHaveLength(13);
    expect(spanRequests[12][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-11",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText("No matching rows")).not.toBeInTheDocument(),
    );
  });

  it("loads trace previews one row at a time without eager list fan-out", async () => {
    let listCalls = 0;
    let resolveSecondPage;
    const secondPage = new Promise((resolve) => {
      resolveSecondPage = resolve;
    });
    mocks.get.mockImplementation(async (url) => {
      if (url === "/traces/") {
        listCalls += 1;
        if (listCalls === 1) {
          return observeListPage({
            rows: [{ trace_id: "trace-first" }],
            total: 37,
            totalIsLowerBound: true,
            hasMore: true,
            nextCursor: "trace-checkpoint-1",
          });
        }
        if (listCalls === 2) return secondPage;
        if (listCalls === 3) {
          return observeListPage({
            rows: [{ trace_id: "trace-third" }],
            total: 3,
            hasMore: false,
            nextCursor: null,
          });
        }
        throw new Error("Trace preview requested an extra list page");
      }
      if (url.startsWith("/traces/trace-") && url.endsWith("/")) {
        const traceId = url.slice("/traces/".length, -1);
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: traceId, input: `${traceId} detail` },
              observation_spans: [],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="traces" />
      </QueryClientProvider>,
    );

    await screen.findByText(/Row 1 of 1/);
    await screen.findByText(/trace-first detail/);
    expect(listCalls).toBe(1);
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/traces/trace-first/"),
    ).toHaveLength(1);
    expect(screen.getByText(/≥37 matching total/)).toBeVisible();
    const firstListRequest = mocks.get.mock.calls.find(
      ([url]) => url === "/traces/",
    );
    expect(firstListRequest[1].params).toEqual(
      expect.objectContaining({
        page_number: 0,
        page_size: 1,
        cursor_mode: true,
        filters: "[]",
      }),
    );

    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });
    await waitFor(() => expect(listCalls).toBe(2));

    // The current row/detail remains visible while the exact next match is in
    // flight, and clicking Next issues only the one signed continuation read.
    expect(screen.getByText(/Row 1 of 1/)).toBeVisible();
    expect(screen.getByText(/trace-first detail/)).toBeVisible();
    const secondListRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/traces/",
    )[1];
    expect(secondListRequest[1].params).toEqual(
      expect.objectContaining({
        page_size: 1,
        cursor_mode: true,
        cursor: "trace-checkpoint-1",
      }),
    );
    expect(secondListRequest[1].params).not.toHaveProperty("page_number");

    await act(async () => {
      resolveSecondPage(
        observeListPage({
          rows: [{ trace_id: "trace-second" }],
          total: 2,
          hasMore: true,
          nextCursor: "trace-checkpoint-2",
        }),
      );
      await secondPage;
    });

    await screen.findByText(/Row 2 of 2/);
    await screen.findByText(/trace-second detail/);
    expect(listCalls).toBe(2);
    expect(screen.getByText(/≥37 matching total/)).toBeVisible();
    await waitFor(() =>
      expect(
        queryClient.getQueryCache().findAll({
          predicate: (query) => query.queryKey[0] === "task-preview-list",
        }),
      ).toHaveLength(1),
    );

    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });
    await screen.findByText(/Row 3 of 3/);
    await screen.findByText(/trace-third detail/);
    expect(listCalls).toBe(3);
    const thirdListRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/traces/",
    )[2];
    expect(thirdListRequest[1].params).toEqual(
      expect.objectContaining({
        page_size: 1,
        cursor_mode: true,
        cursor: "trace-checkpoint-2",
      }),
    );
    expect(screen.getByRole("button", { name: "Next row" })).toBeDisabled();
    await waitFor(() =>
      expect(
        queryClient.getQueryCache().findAll({
          predicate: (query) => query.queryKey[0] === "task-preview-list",
        }),
      ).toHaveLength(1),
    );
  });

  it.each([
    ["spans", "/spans/"],
    ["sessions", "/sessions/"],
  ])(
    "uses one-row signed continuation requests for %s previews",
    async (rowType, listUrl) => {
      let listCalls = 0;
      const row = (suffix) =>
        rowType === "spans"
          ? {
              span_id: `span-${suffix}`,
              trace_id: `trace-${suffix}`,
            }
          : { session_id: `session-${suffix}` };
      mocks.get.mockImplementation(async (url) => {
        if (url === listUrl) {
          listCalls += 1;
          return listCalls === 1
            ? observeListPage({
                rows: [row("first")],
                total: 2,
                hasMore: true,
                nextCursor: `${rowType}-checkpoint`,
              })
            : observeListPage({
                rows: [row("second")],
                total: 2,
                hasMore: false,
                nextCursor: null,
              });
        }
        if (rowType === "spans" && url.startsWith("/traces/trace-")) {
          const traceId = url.slice("/traces/".length, -1);
          const suffix = traceId.replace("trace-", "");
          return {
            data: {
              status: true,
              result: {
                trace: { trace_id: traceId },
                observation_spans: [
                  {
                    observation_span: { id: `span-${suffix}` },
                    children: [],
                  },
                ],
              },
            },
          };
        }
        if (rowType === "sessions" && url.startsWith("/sessions/session-")) {
          const sessionId = url.slice("/sessions/".length, -1);
          return {
            data: {
              status: true,
              result: {
                session_metadata: { session_id: sessionId },
                response: [],
              },
            },
          };
        }
        throw new Error(`Unexpected GET ${url}`);
      });
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });

      render(
        <QueryClientProvider client={queryClient}>
          <PreviewHarness rowType={rowType} />
        </QueryClientProvider>,
      );

      await screen.findByText(/Row 1 of 1/);
      await waitFor(() =>
        expect(
          mocks.get.mock.calls.filter(([url]) => url !== listUrl),
        ).toHaveLength(1),
      );
      expect(listCalls).toBe(1);
      const firstRequest = mocks.get.mock.calls.find(
        ([url]) => url === listUrl,
      );
      expect(firstRequest[1].params).toEqual(
        expect.objectContaining({
          page_number: 0,
          page_size: 1,
          cursor_mode: true,
        }),
      );

      await act(async () => {
        screen.getByRole("button", { name: "Next row" }).click();
      });
      await screen.findByText(/Row 2 of 2/);
      expect(listCalls).toBe(2);
      const continuationRequest = mocks.get.mock.calls.filter(
        ([url]) => url === listUrl,
      )[1];
      expect(continuationRequest[1].params).toEqual(
        expect.objectContaining({
          page_size: 1,
          cursor_mode: true,
          cursor: `${rowType}-checkpoint`,
        }),
      );
      expect(continuationRequest[1].params).not.toHaveProperty("page_number");
      expect(screen.getByRole("button", { name: "Next row" })).toBeDisabled();
    },
  );

  it("renders the first non-empty voice-call preview batch without filling the page", async () => {
    let listCalls = 0;
    let resolveSecondPage;
    const secondPage = new Promise((resolve) => {
      resolveSecondPage = resolve;
    });
    mocks.get.mockImplementation(async (url) => {
      if (url === "/calls/") {
        const callIndex = listCalls;
        listCalls += 1;
        if (callIndex === 0) {
          return voiceListPage({
            results: [{ id: "call-fast", trace_id: "trace-voice-fast" }],
            count: 37,
            hasMore: true,
            nextCursor: "voice-fast-checkpoint-0",
          });
        }
        if (callIndex === 1) return secondPage;
        throw new Error("Voice preview requested more than one lazy row");
      }
      if (url === "/calls/detail/") {
        return { data: { result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" />
      </QueryClientProvider>,
    );

    await screen.findByText(/Row 1 of 1/);
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(([url]) => url === "/calls/detail/"),
      ).toHaveLength(1),
    );
    expect(listCalls).toBe(1);
    expect(screen.getByText(/≥37 matching total/)).toBeVisible();
    const firstListRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/calls/",
    )[0];
    expect(firstListRequest[1].params).toEqual(
      expect.objectContaining({
        page: 1,
        page_size: 1,
        cursor_mode: true,
        filters: "[]",
      }),
    );

    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });
    await waitFor(() => expect(listCalls).toBe(2));

    // The current row and its detail stay mounted while the cursor request is
    // unresolved; one click must not trigger a second detail read or fan out.
    expect(screen.getByText(/Row 1 of 1/)).toBeVisible();
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/calls/detail/"),
    ).toHaveLength(1);
    const secondListRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/calls/",
    )[1];
    expect(secondListRequest[1].params).toEqual(
      expect.objectContaining({
        page_size: 1,
        cursor_mode: true,
        cursor: "voice-fast-checkpoint-0",
      }),
    );

    await act(async () => {
      resolveSecondPage(
        voiceListPage({
          results: [{ id: "call-second", trace_id: "trace-voice-second" }],
          count: 2,
          hasMore: false,
          nextCursor: null,
        }),
      );
      await secondPage;
    });

    await screen.findByText(/Row 2 of 2/);
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(([url]) => url === "/calls/detail/"),
      ).toHaveLength(2),
    );
    expect(listCalls).toBe(2);
    expect(screen.getByText(/≥37 matching total/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Next row" })).toBeDisabled();
  });

  it("keeps the voice list row visible when optional detail hydration fails", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === "/calls/") {
        return voiceListPage({
          results: [
            {
              id: "voice-fallback",
              trace_id: "voice-fallback-trace",
              phone_number: "fallback-number",
            },
          ],
          hasMore: false,
          nextCursor: null,
        });
      }
      if (url === "/calls/detail/") {
        const error = new Error("private response validation detail");
        error.response = { status: 400 };
        throw error;
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" />
      </QueryClientProvider>,
    );

    await screen.findByText(/fallback-number/);
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/calls/detail/"),
    ).toHaveLength(1);
    expect(screen.queryByText(QUERY_FAILED_RETRY_MESSAGE)).toBeNull();
    expect(screen.queryByText("private response validation detail")).toBeNull();
  });

  it("disables backward navigation while a lazy next row is pending", async () => {
    let listCalls = 0;
    let resolveSecondPage;
    let resolveThirdPage;
    const secondPage = new Promise((resolve) => {
      resolveSecondPage = resolve;
    });
    const thirdPage = new Promise((resolve) => {
      resolveThirdPage = resolve;
    });
    mocks.get.mockImplementation(async (url) => {
      if (url === "/calls/") {
        listCalls += 1;
        if (listCalls === 1) {
          return voiceListPage({
            results: [{ id: "call-first", trace_id: "trace-voice-first" }],
            count: 3,
            hasMore: true,
            nextCursor: "voice-second-checkpoint",
          });
        }
        if (listCalls === 2) return secondPage;
        if (listCalls === 3) return thirdPage;
        throw new Error("Voice preview requested more than two lazy rows");
      }
      if (url === "/calls/detail/") {
        return { data: { result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" />
      </QueryClientProvider>,
    );

    await screen.findByText(/Row 1 of 1/);
    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });
    await waitFor(() => expect(listCalls).toBe(2));
    await act(async () => {
      resolveSecondPage(
        voiceListPage({
          results: [{ id: "call-second", trace_id: "trace-voice-second" }],
          count: 3,
          hasMore: true,
          nextCursor: "voice-third-checkpoint",
        }),
      );
      await secondPage;
    });
    await screen.findByText(/Row 2 of 2/);
    expect(screen.getByRole("button", { name: "Previous row" })).toBeEnabled();

    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });
    await waitFor(() => expect(listCalls).toBe(3));
    expect(screen.getByRole("button", { name: "Previous row" })).toBeDisabled();

    await act(async () => {
      resolveThirdPage(
        voiceListPage({
          results: [{ id: "call-third", trace_id: "trace-voice-third" }],
          count: 3,
          hasMore: false,
          nextCursor: null,
        }),
      );
      await thirdPage;
    });

    await screen.findByText(/Row 3 of 3/);
    expect(screen.getByRole("button", { name: "Previous row" })).toBeEnabled();
  });

  it("resumes a sparse voice-call preview beyond the first hop budget", async () => {
    let listCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/calls/") {
        const callIndex = listCalls;
        listCalls += 1;
        if (callIndex < 24) {
          return voiceListPage({
            hasMore: true,
            nextCursor: `voice-checkpoint-${callIndex}`,
          });
        }
        return voiceListPage({
          results: [{ id: "call-rare", trace_id: "trace-voice-rare" }],
          hasMore: false,
          nextCursor: null,
        });
      }
      if (url === "/calls/detail/") {
        return { data: { result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness rowType="voiceCalls" />
      </QueryClientProvider>,
    );

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    expect(listCalls).toBe(13);

    await act(async () => continueSearch.click());
    await screen.findByText("Row 1 of 1");

    const listRequests = mocks.get.mock.calls.filter(
      ([url]) => url === "/calls/",
    );
    expect(listRequests).toHaveLength(25);
    expect(listRequests[13][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "voice-checkpoint-12",
      }),
    );
  });

  it("resumes a valid sparse continuation beyond the first hop budget", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") {
        const callIndex = spanListCalls;
        spanListCalls += 1;
        if (callIndex < 24) {
          return {
            data: {
              status: true,
              result: {
                config: [],
                table: [],
                metadata: {
                  total_rows: 0,
                  has_more: true,
                  next_cursor: `checkpoint-${callIndex}`,
                  total_rows_is_lower_bound: true,
                },
              },
            },
          };
        }
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-beyond-budget",
                  trace_id: "trace-beyond-budget",
                  input: "found after a resumed cursor",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-beyond-budget/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-beyond-budget" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-beyond-budget",
                    input: "found after a resumed cursor",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    expect(spanListCalls).toBe(13);
    expect(
      screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();

    await act(async () => continueSearch.click());
    await screen.findByText("Row 1 of 1");
    expect(spanListCalls).toBe(25);
    const resumedRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    )[13];
    expect(resumedRequest[1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-12",
      }),
    );
  });

  it("shows a real empty state only after resumed terminal exhaustion", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url !== "/spans/") throw new Error(`Unexpected GET ${url}`);
      const callIndex = spanListCalls;
      spanListCalls += 1;
      if (callIndex < 13) {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [],
              metadata: {
                total_rows: 0,
                has_more: true,
                next_cursor: `terminal-checkpoint-${callIndex}`,
              },
            },
          },
        };
      }
      return {
        data: {
          status: true,
          result: {
            config: [],
            table: [],
            metadata: { has_more: false, next_cursor: null, total_rows: 0 },
          },
        },
      };
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    expect(spanListCalls).toBe(13);
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();

    await act(async () => continueSearch.click());
    await screen.findByText("No matching rows");
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(spanListCalls).toBe(14);
  });

  it("retries a transport failure from the retained preview checkpoint", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") {
        const callIndex = spanListCalls;
        spanListCalls += 1;
        if (callIndex < 13) {
          return {
            data: {
              status: true,
              result: {
                config: [],
                table:
                  callIndex === 0
                    ? [
                        {
                          span_id: "span-retained",
                          trace_id: "trace-retained",
                          input: "retained preview row",
                        },
                      ]
                    : [],
                metadata: {
                  total_rows: 0,
                  has_more: true,
                  next_cursor: `retry-checkpoint-${callIndex}`,
                },
              },
            },
          };
        }
        if (callIndex === 13) {
          throw new Error("temporary transport failure");
        }
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-after-retry",
                  trace_id: "trace-after-retry",
                  input: "preview resumed after retry",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-after-retry/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-after-retry" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-after-retry",
                    input: "preview resumed after retry",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      if (url === "/traces/trace-retained/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-retained" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-retained",
                    input: "retained preview row",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    await screen.findByText("Row 1 of 1");
    expect(screen.getByText(/retained preview row/)).toBeVisible();
    await act(async () => {
      screen.getByRole("button", { name: "Next row" }).click();
    });

    const retrySearch = await screen.findByRole("button", {
      name: "Retry search",
    });
    expect(screen.getByText("The exact preview was paused.")).toBeVisible();
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();

    const failedResumeRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    )[13];
    expect(failedResumeRequest[1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "retry-checkpoint-12",
      }),
    );

    await act(async () => retrySearch.click());
    await screen.findByText("Row 1 of 2");
    expect(screen.getByText(/retained preview row/)).toBeVisible();

    const successfulResumeRequest = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    )[14];
    expect(successfulResumeRequest[1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "retry-checkpoint-12",
      }),
    );
  });

  it("retries a cold initial list failure without requiring a scope change", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") {
        spanListCalls += 1;
        if (spanListCalls === 1) {
          throw new Error("temporary initial transport failure");
        }
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-after-cold-retry",
                  trace_id: "trace-after-cold-retry",
                  input: "preview recovered after cold retry",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-after-cold-retry/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-after-cold-retry" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-after-cold-retry",
                    input: "preview recovered after cold retry",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    const retrySearch = await screen.findByRole("button", {
      name: "Retry search",
    });
    await act(async () => retrySearch.click());

    await screen.findByText("Row 1 of 1");
    expect(
      screen.getByText(/preview recovered after cold retry/),
    ).toBeVisible();
    expect(spanListCalls).toBe(2);
  });

  it("loads the preview through the legacy numbered fallback", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url, config) => {
      if (url === "/spans/") {
        spanListCalls += 1;
        if (spanListCalls === 1) {
          const error = new Error("legacy cursor field");
          error.response = {
            status: 400,
            data: {
              attr: "cursor_mode",
              detail: "cursor_mode: Unknown field.",
            },
          };
          throw error;
        }
        expect(config.params).toEqual(
          expect.objectContaining({ page_number: 0 }),
        );
        expect(config.params).not.toHaveProperty("cursor_mode");
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-legacy",
                  trace_id: "trace-legacy",
                  input: "legacy preview value",
                },
              ],
              metadata: { total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-legacy/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-legacy" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-legacy",
                    input: "legacy preview value",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    await screen.findByText("Row 1 of 1");
    expect(screen.getByText(/legacy preview value/)).toBeVisible();
    expect(spanListCalls).toBe(2);
    const firstSpanRequest = mocks.get.mock.calls.find(
      ([url]) => url === "/spans/",
    );
    expect(firstSpanRequest[1].params).toEqual(
      expect.objectContaining({ cursor_mode: true, page_number: 0 }),
    );
  });

  it("fails closed without looping when the API repeats a signed cursor", async () => {
    mocks.get.mockResolvedValue({
      data: {
        status: true,
        result: {
          config: [],
          table: [],
          metadata: {
            total_rows: 0,
            has_more: true,
            next_cursor: "repeated-cursor",
            total_rows_is_lower_bound: true,
          },
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    await screen.findByText(QUERY_FAILED_RETRY_MESSAGE);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
  });

  it("fails closed when a cursor cycles at a later attempt boundary", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url !== "/spans/") throw new Error(`Unexpected GET ${url}`);
      const callIndex = spanListCalls;
      spanListCalls += 1;
      return {
        data: {
          status: true,
          result: {
            config: [],
            table: [],
            metadata: {
              total_rows: 0,
              has_more: true,
              next_cursor: callIndex === 25 ? "cycle-0" : `cycle-${callIndex}`,
              total_rows_is_lower_bound: true,
            },
          },
        },
      };
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    await act(async () => continueSearch.click());

    await screen.findByText(QUERY_FAILED_RETRY_MESSAGE);
    expect(spanListCalls).toBe(26);
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
  });

  it("does not let a superseded project response overwrite the active preview", async () => {
    let resolveOldResponse;
    let oldSignal;
    const oldResponse = new Promise((resolve) => {
      resolveOldResponse = resolve;
    });
    mocks.get.mockImplementation(async (url, options = {}) => {
      if (url === "/spans/" && options.params?.project_id === "project-old") {
        oldSignal = options.signal;
        return oldResponse;
      }
      if (url === "/spans/" && options.params?.project_id === "project-new") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-new",
                  trace_id: "trace-new",
                  input: "fresh preview value",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-new/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-new" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-new",
                    input: "fresh preview value",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-old" />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-new" />
      </QueryClientProvider>,
    );
    await screen.findByText(/fresh preview value/);
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOldResponse({
        data: {
          status: true,
          result: {
            config: [],
            table: [
              {
                span_id: "span-old",
                trace_id: "trace-old",
                input: "stale preview value",
              },
            ],
            metadata: { has_more: false, next_cursor: null, total_rows: 1 },
          },
        },
      });
      await Promise.resolve();
    });

    expect(screen.getByText(/fresh preview value/)).toBeInTheDocument();
    expect(screen.queryByText(/stale preview value/)).not.toBeInTheDocument();
  });

  it("never carries a resumed cursor into a different project", async () => {
    let oldListCalls = 0;
    let resolveOldResume;
    let oldResumeSignal;
    const oldResume = new Promise((resolve) => {
      resolveOldResume = resolve;
    });
    mocks.get.mockImplementation(async (url, options = {}) => {
      if (url === "/spans/" && options.params?.project_id === "project-old") {
        const callIndex = oldListCalls;
        oldListCalls += 1;
        if (callIndex < 13) {
          return {
            data: {
              status: true,
              result: {
                config: [],
                table: [],
                metadata: {
                  total_rows: 0,
                  has_more: true,
                  next_cursor: `old-checkpoint-${callIndex}`,
                  total_rows_is_lower_bound: true,
                },
              },
            },
          };
        }
        oldResumeSignal = options.signal;
        return oldResume;
      }
      if (url === "/spans/" && options.params?.project_id === "project-new") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-new-scope",
                  trace_id: "trace-new-scope",
                  input: "new scope preview",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-new-scope/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-new-scope" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-new-scope",
                    input: "new scope preview",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-old" />
      </QueryClientProvider>,
    );

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    await act(async () => continueSearch.click());
    await waitFor(() => expect(oldListCalls).toBe(14));

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-new" />
      </QueryClientProvider>,
    );

    await screen.findByText(/new scope preview/);
    const newProjectRequest = mocks.get.mock.calls.find(
      ([url, options]) =>
        url === "/spans/" && options.params?.project_id === "project-new",
    );
    expect(newProjectRequest[1].params).not.toHaveProperty("cursor");
    expect(oldResumeSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOldResume({
        data: {
          status: true,
          result: {
            config: [],
            table: [
              {
                span_id: "span-old-scope",
                trace_id: "trace-old-scope",
                input: "stale resumed preview",
              },
            ],
            metadata: { has_more: false, next_cursor: null, total_rows: 1 },
          },
        },
      });
      await Promise.resolve();
    });

    expect(screen.getByText(/new scope preview/)).toBeInTheDocument();
    expect(screen.queryByText(/stale resumed preview/)).not.toBeInTheDocument();
  });

  it("starts a fresh list read when returning to an earlier scope", async () => {
    let oldScopeCalls = 0;
    mocks.get.mockImplementation(async (url, options = {}) => {
      const projectId = options.params?.project_id;
      if (url === "/spans/" && projectId === "project-old") {
        const callIndex = oldScopeCalls;
        oldScopeCalls += 1;
        if (callIndex < 13) {
          return {
            data: {
              status: true,
              result: {
                config: [],
                table: [],
                metadata: {
                  total_rows: 0,
                  has_more: true,
                  next_cursor: `old-scope-checkpoint-${callIndex}`,
                },
              },
            },
          };
        }
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-old-fresh",
                  trace_id: "trace-old-fresh",
                  input: "fresh read after returning to old scope",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/spans/" && projectId === "project-new") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-new",
                  trace_id: "trace-new",
                  input: "new scope row",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-new/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-new" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-new",
                    input: "new scope row",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      if (url === "/traces/trace-old-fresh/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-old-fresh" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-old-fresh",
                    input: "fresh read after returning to old scope",
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-old" />
      </QueryClientProvider>,
    );

    await screen.findByRole("button", { name: "Continue search" });
    expect(oldScopeCalls).toBe(13);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-new" />
      </QueryClientProvider>,
    );
    await screen.findByText(/new scope row/);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-old" />
      </QueryClientProvider>,
    );
    await screen.findByText(/fresh read after returning to old scope/);

    const returnedRequest = mocks.get.mock.calls.filter(
      ([url, options]) =>
        url === "/spans/" && options.params?.project_id === "project-old",
    )[13];
    expect(returnedRequest[1].params).not.toHaveProperty("cursor");
    expect(oldScopeCalls).toBe(14);
  });

  it("does not reuse cached detail data after the selected project changes", async () => {
    let detailCalls = 0;
    mocks.get.mockImplementation(async (url, options = {}) => {
      if (url === "/spans/") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-shared",
                  trace_id: "trace-shared",
                  input: `${options.params?.project_id} list value`,
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-shared/") {
        detailCalls += 1;
        const detail =
          detailCalls === 1 ? "old project detail" : "new project detail";
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-shared" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-shared",
                    input: detail,
                  },
                  children: [],
                },
              ],
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-old" />
      </QueryClientProvider>,
    );

    await screen.findByText(/old project detail/);
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness projectId="project-new" />
      </QueryClientProvider>,
    );

    await screen.findByText(/new project detail/);
    expect(detailCalls).toBe(2);
    expect(screen.queryByText(/old project detail/)).not.toBeInTheDocument();
  });

  it("renders a sanitized failure state when row detail cannot be loaded", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  span_id: "span-detail-error",
                  trace_id: "trace-detail-error",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/traces/trace-detail-error/") {
        throw new Error("Code: 159. DB::Exception: internal detail");
      }
      throw new Error(`Unexpected GET ${url}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PreviewHarness />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    expect(screen.queryByText(/DB::Exception/)).not.toBeInTheDocument();
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
  });
});
