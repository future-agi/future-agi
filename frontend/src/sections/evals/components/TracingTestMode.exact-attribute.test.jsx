import React from "react";
import { Box } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ANALYTICS_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { SPAN_REFERENCE_ERROR } from "src/sections/projects/LLMTracing/spanReadReference";
import * as listReads from "src/sections/projects/LLMTracing/listCursorPagination";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import {
  ATTRIBUTE_LOOKUP_UNAVAILABLE_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  exactFields: ["final_status"],
  exactReadState: "complete",
  fetchNextAttributePage: vi.fn(),
  hasNextAttributePage: false,
  isFetchingNextAttributePage: false,
  isNextAttributePageError: false,
}));

vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      getProjectById: (id) => `/projects/${id}`,
      getSpansForObserveProject: () => "/spans/",
      getTracesForObserveProject: () => "/traces/",
      projectSessionList: () => "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
      getObservationSpan: (id) => `/span-details/${id}/`,
      listProjects: () => "/projects/",
      traceSession: "/sessions/",
      getCallLogs: "/calls/",
      getVoiceCallDetail: "/calls/detail/",
      getEvalAttributeList: () => "/eval-attributes/",
    },
    develop: {
      eval: {
        evalPlayground: "/eval-playground/",
        executeCompositeEval: (id) => `/composite/${id}/execute/`,
        executeCompositeEvalAdhoc: "/composite/execute/",
      },
    },
  },
}));

vi.mock("./useExactEvalAttributeFields", async (importOriginal) => ({
  ...(await importOriginal()),
  useExactEvalAttributeFields: () => ({
    data: mocks.exactFields,
    queryReadState: mocks.exactReadState,
    isFetching: false,
    fetchNextPage: mocks.fetchNextAttributePage,
    hasNextPage: mocks.hasNextAttributePage,
    isFetchingNextPage: mocks.isFetchingNextAttributePage,
    isFetchNextPageError: mocks.isNextAttributePageError,
  }),
}));

vi.mock(
  "src/sections/tasks/components/TaskLivePreview",
  async (importOriginal) => ({
    buildApiFilterArray: (await importOriginal()).buildApiFilterArray,
  }),
);
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: ({ toolbarStart }) => toolbarStart || null,
}));
vi.mock("./DatasetTestMode", () => ({ JsonValueTree: () => null }));
vi.mock("./SpanRowList", () => ({ default: () => null }));
vi.mock("./EvalResultDisplay", () => ({ default: () => null }));
vi.mock("src/components/draggable-col-resizer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/inline-audio/inline-row-audio", () => ({
  InlineAudio: () => null,
  RecordingGroup: () => null,
}));
vi.mock("../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({
    state: { status: null, details: null, message: null },
    start: vi.fn(),
  }),
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));

import TracingTestMode from "./TracingTestMode";
import { productionTheme } from "src/sections/tasks/components/__tests__/samplingContrastTestUtils";

const PROJECT_ID = "00000000-0000-4000-8000-000000000901";
const SPAN_REFERENCE = {
  project_id: PROJECT_ID,
  start_time: "2026-09-01T12:01:02.123456Z",
  observation_type: "SPAN",
  service_name: "test-service",
  _version: "18446744073709551614",
};

function renderTaskMapping(onReadyChange, extraProps = {}, renderOptions = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TracingTestMode
        templateId="eval-template-1"
        variables={["evaluation_result"]}
        initialProjectId={PROJECT_ID}
        initialRowType="spans"
        allowCustomFieldPath
        onReadyChange={onReadyChange}
        {...extraProps}
      />
    </QueryClientProvider>,
    renderOptions,
  );
}

describe("TracingTestMode exact task attribute mapping", () => {
  afterEach(() => vi.useRealTimers());

  it.each(["spans", "traces", "sessions"])(
    "lets a slow initial %s list finish beyond its target without running an eval",
    async (rowType) => {
      vi.useFakeTimers();
      let listSignal;
      let resolveList;
      mocks.get.mockImplementation(async (url, { signal } = {}) => {
        if (url === `/projects/${PROJECT_ID}`) {
          return { data: { result: { id: PROJECT_ID, source: "api" } } };
        }
        if (url === `/${rowType}/`) {
          listSignal = signal;
          return new Promise((resolve) => {
            resolveList = resolve;
          });
        }
        throw new Error(`Unexpected GET ${url}`);
      });
      const view = renderTaskMapping(vi.fn(), {
        initialRowType: rowType,
        allowCustomFieldPath: false,
      });
      await act(async () => vi.advanceTimersByTimeAsync(1));
      expect(screen.getByPlaceholderText("Loading columns...")).toBeDisabled();
      await act(async () =>
        vi.advanceTimersByTimeAsync(ANALYTICS_REQUEST_TIMEOUT_MS + 1),
      );
      expect(listSignal.aborted).toBe(false);
      expect(
        screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
      ).not.toBeInTheDocument();
      await act(async () => {
        resolveList({
          data: {
            status: true,
            result: {
              config: [],
              table: [],
              metadata: { total_rows: 0, has_more: false, next_cursor: null },
            },
          },
        });
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(
        screen.getByPlaceholderText("Search column..."),
      ).not.toBeDisabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(
        screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
      ).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();
      view.unmount();
    },
  );

  it.each(["spans", "traces", "sessions"])(
    "resumes a timed-out %s cursor without poisoning its history or column picker",
    async (rowType) => {
      vi.useFakeTimers();
      const checkpoint = `${rowType}-unconsumed`;
      const page = (hasMore) => ({
        data: {
          status: true,
          result: {
            config: [],
            table: [],
            metadata: {
              has_more: hasMore,
              next_cursor: hasMore ? checkpoint : null,
              total_rows: 0,
            },
          },
        },
      });
      let listCalls = 0;
      let continuationSignal;
      let resolveLate;
      mocks.get.mockImplementation(async (url, { signal } = {}) => {
        if (url === `/projects/${PROJECT_ID}`) {
          return { data: { result: { id: PROJECT_ID, source: "api" } } };
        }
        if (url === `/${rowType}/`) {
          listCalls += 1;
          if (listCalls === 1)
            return new Promise((resolve) =>
              setTimeout(() => resolve(page(true)), 10_000),
            );
          if (listCalls === 2) {
            continuationSignal = signal;
            return new Promise((resolve) => {
              resolveLate = resolve;
            });
          }
          return page(false);
        }
        throw new Error(`Unexpected GET ${url}`);
      });
      const view = renderTaskMapping(vi.fn(), {
        initialRowType: rowType,
        allowCustomFieldPath: false,
      });
      await act(async () => vi.advanceTimersByTimeAsync(10_001));
      expect(listCalls).toBe(2);
      await act(async () =>
        vi.advanceTimersByTimeAsync(ANALYTICS_REQUEST_TIMEOUT_MS - 10_000 + 1),
      );
      expect(continuationSignal.aborted).toBe(true);
      expect(
        screen.getByRole("button", { name: "Continue search" }),
      ).toBeEnabled();
      expect(
        screen.getByPlaceholderText("Search column..."),
      ).not.toBeDisabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      await act(async () => {
        resolveLate(page(false));
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(listCalls).toBe(2);
      await act(async () =>
        screen.getByRole("button", { name: "Continue search" }).click(),
      );
      await act(async () => vi.advanceTimersByTimeAsync(10));
      const requests = mocks.get.mock.calls.filter(
        ([url]) => url === `/${rowType}/`,
      );
      expect(requests).toHaveLength(3);
      expect(requests[2][1].params.cursor).toBe(checkpoint);
      expect(
        screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Continue search" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByPlaceholderText("Search column..."),
      ).not.toBeDisabled();
      expect(mocks.post).not.toHaveBeenCalled();
      view.unmount();
    },
  );

  it("keeps buffered preview rows across a deadline and deduplicates the resumed page", async () => {
    vi.useFakeTimers();
    const defaultGet = mocks.get.getMockImplementation();
    const firstRow = {
      ...SPAN_REFERENCE,
      span_id: "span-1",
      trace_id: "trace-1",
      input: "generic preview value",
    };
    const secondRow = {
      ...SPAN_REFERENCE,
      span_id: "span-2",
      trace_id: "trace-2",
    };
    let listCalls = 0;
    mocks.get.mockImplementation((url, config) => {
      if (url !== "/spans/") return defaultGet(url, config);
      listCalls += 1;
      if (listCalls === 2) return new Promise(() => {});
      return Promise.resolve({
        data: {
          status: true,
          result: {
            config: [],
            table: listCalls === 1 ? [firstRow] : [secondRow, secondRow],
            metadata: {
              total_rows: 2,
              has_more: listCalls === 1,
              next_cursor: listCalls === 1 ? "buffered-unconsumed" : null,
            },
          },
        },
      });
    });
    const view = renderTaskMapping(vi.fn());
    await act(async () =>
      vi.advanceTimersByTimeAsync(ANALYTICS_REQUEST_TIMEOUT_MS + 1),
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(screen.getByText("Row 1 of 1")).toBeVisible();
    await act(async () =>
      screen.getByRole("button", { name: "Continue search" }).click(),
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(screen.getByText("Row 1 of 2")).toBeVisible();
    const requests = mocks.get.mock.calls.filter(([url]) => url === "/spans/");
    expect(requests).toHaveLength(3);
    expect(requests[2][1].params.cursor).toBe("buffered-unconsumed");
    expect(mocks.post).not.toHaveBeenCalled();
    view.unmount();
  });

  it("still rejects a real cursor cycle without restarting the preview", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation((url, config) => {
      if (url !== "/spans/") return defaultGet(url, config);
      return Promise.resolve({
        data: {
          status: true,
          result: {
            config: [],
            table: [],
            metadata: {
              total_rows: 0,
              has_more: true,
              next_cursor: "real-cycle",
            },
          },
        },
      });
    });
    const view = renderTaskMapping(vi.fn());
    expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/spans/"),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    view.unmount();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.exactFields = ["final_status"];
    mocks.exactReadState = "complete";
    mocks.hasNextAttributePage = false;
    mocks.isFetchingNextAttributePage = false;
    mocks.isNextAttributePageError = false;
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
      if (url === "/spans/") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  ...SPAN_REFERENCE,
                  span_id: "span-1",
                  trace_id: "trace-1",
                  input: "generic preview value",
                },
              ],
              metadata: { total_rows: 1 },
            },
          },
        };
      }
      if (url === "/span-details/span-1/") {
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                trace_id: "trace-1",
                id: "span-1",
                input: "generic preview value",
                span_attributes: { input: "generic preview value" },
              },
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });
  });

  it("settles cancelled eval continuations without releasing replacement column loading", async () => {
    const collect = vi.spyOn(listReads, "collectExactListRows"); // Call-through: real helper.
    const defaultGet = mocks.get.getMockImplementation();
    let resolveOld;
    let resolveCurrent;
    let oldSignal;
    const page = (hasMore) => ({
      data: {
        status: true,
        result: {
          config: [],
          table: [],
          metadata: {
            total_rows: 0,
            has_more: hasMore,
            next_cursor: hasMore ? "old-checkpoint" : null,
          },
        },
      },
    });
    mocks.get.mockImplementation((url, options = {}) => {
      if (url !== "/spans/") return defaultGet(url, options);
      if (JSON.parse(options.params.filters).length === 0) {
        if (!options.params.cursor) return Promise.resolve(page(true));
        oldSignal = options.signal;
        return new Promise((resolve) => {
          resolveOld = resolve;
        });
      }
      return new Promise((resolve) => {
        resolveCurrent = resolve;
      });
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const subject = (filters) => (
      <QueryClientProvider client={queryClient}>
        <TracingTestMode
          templateId="eval-template-1"
          variables={["evaluation_result"]}
          initialProjectId={PROJECT_ID}
          initialRowType="spans"
          localFilters={filters}
        />
      </QueryClientProvider>
    );
    const view = render(subject([]));
    await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
    const settled = vi.fn();
    const oldRead = collect.mock.results[0].value.then(settled, settled);
    const filters = [
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "current",
        },
      },
    ];
    view.rerender(subject(filters));
    await waitFor(() => expect(resolveCurrent).toBeTypeOf("function"));
    try {
      await waitFor(() =>
        expect(settled).toHaveBeenCalledWith(
          expect.objectContaining({ name: "AbortError" }),
        ),
      );
      expect(oldSignal.aborted).toBe(true);
      expect(screen.getAllByRole("progressbar")[0]).toBeVisible();
      expect(screen.getByPlaceholderText("Loading columns...")).toBeDisabled();
      expect(
        screen.queryByText("No matching spans found"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
      ).not.toBeInTheDocument();
      const currentRequest = mocks.get.mock.calls
        .filter(([url]) => url === "/spans/")
        .at(-1)[1].params;
      expect(JSON.parse(currentRequest.filters)).toEqual(filters);
      expect(currentRequest).not.toHaveProperty("cursor");
      await act(async () => resolveCurrent(await defaultGet("/spans/")));
      await screen.findByText(/generic preview value/);
      await waitFor(() =>
        expect(
          screen.getByPlaceholderText("Search column..."),
        ).not.toBeDisabled(),
      );
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();
    } finally {
      await act(async () => {
        resolveOld(page(false));
        await oldRead;
      });
      view.unmount();
      queryClient.clear();
      collect.mockRestore();
    }
  });

  it("does not offer response-only native fields as task evaluator inputs", async () => {
    renderTaskMapping(vi.fn(), { taskMapping: true });
    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.click(input);
    expect(
      await screen.findByRole("option", { name: "input", exact: true }),
    ).toBeVisible();
    expect(
      screen.queryByRole("option", { name: "id", exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "trace_id", exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "project_id", exact: true }),
    ).not.toBeInTheDocument();
  });

  it.each(["id", "trace_id", "project_id"])(
    "rejects a saved native %s mapping before Add Evaluation with an actionable message",
    async (path) => {
      const ready = vi.fn();
      renderTaskMapping(ready, {
        taskMapping: true,
        initialMapping: { evaluation_result: path },
      });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        `Native field "${path}" is not supported for task evaluation. Map this variable to a supported field such as input, output or a span attribute.`,
      );
      await waitFor(() =>
        expect(ready).toHaveBeenLastCalledWith(false, {
          evaluation_result: path,
        }),
      );
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it("rejects a typed native id mapping and recovers when mapped to input", async () => {
    const ready = vi.fn();
    renderTaskMapping(ready, { taskMapping: true });
    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.type(input, "id{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Native field "id"',
    );
    await waitFor(() =>
      expect(ready).toHaveBeenLastCalledWith(false, {
        evaluation_result: "id",
      }),
    );
    await userEvent.clear(input);
    await userEvent.type(input, "input{Enter}");
    await waitFor(() =>
      expect(ready).toHaveBeenLastCalledWith(true, {
        evaluation_result: "input",
      }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a genuine span attribute named id mappable, rather than blacklisting its name", async () => {
    const get = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (url, config) => {
      const response = await get(url, config);
      if (url === "/span-details/span-1/")
        response.data.result.observation_span.span_attributes.id = "custom-id";
      return response;
    });
    const ready = vi.fn();
    renderTaskMapping(ready, {
      taskMapping: true,
      initialMapping: { evaluation_result: "id" },
    });
    await waitFor(() =>
      expect(ready).toHaveBeenLastCalledWith(true, { evaluation_result: "id" }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a sanitized retry for an initial retained attribute error", async () => {
    mocks.exactReadState = "error";
    mocks.hasNextAttributePage = true;
    mocks.isNextAttributePageError = true;
    renderTaskMapping(vi.fn());

    expect(
      await screen.findByText(ATTRIBUTE_LOOKUP_UNAVAILABLE_MESSAGE),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Retry loading attributes" }),
    );

    expect(mocks.fetchNextAttributePage).toHaveBeenCalledOnce();
  });

  it("lists and selects final_status even when the preview row omits it", async () => {
    const onReadyChange = vi.fn();
    renderTaskMapping(onReadyChange);

    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).not.toBeDisabled());

    await userEvent.click(input);
    await userEvent.type(input, "final_status");
    const option = await screen.findByRole("option", { name: "final_status" });
    await userEvent.click(option);

    expect(input).toHaveValue("final_status");
    await waitFor(() =>
      expect(onReadyChange).toHaveBeenCalledWith(true, {
        evaluation_result: "final_status",
      }),
    );
  });

  it("loads the next retained attribute page on explicit request", async () => {
    mocks.hasNextAttributePage = true;
    renderTaskMapping(vi.fn());

    await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Load more attributes" }),
    );

    expect(mocks.fetchNextAttributePage).toHaveBeenCalledOnce();
  });

  it("labels a cursor total lower bound without presenting it as exact", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (url, ...args) => {
      if (url === "/spans/") {
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [
                {
                  ...SPAN_REFERENCE,
                  span_id: "span-1",
                  trace_id: "trace-1",
                  input: "generic preview value",
                },
              ],
              metadata: {
                total_rows: 100,
                total_rows_is_lower_bound: true,
              },
            },
          },
        };
      }
      return defaultGet(url, ...args);
    });

    renderTaskMapping(vi.fn());

    expect(
      await screen.findByText("(≥100 matching total)"),
    ).toBeInTheDocument();
    expect(screen.queryByText("(100 matching total)")).not.toBeInTheDocument();
  });

  it.each(
    [
      ["spans", "/spans/"],
      ["traces", "/traces/"],
      ["sessions", "/sessions/"],
      ["voiceCalls", "/calls/"],
    ].flatMap(([rowType, endpoint]) =>
      [
        "2026-03-10T12:37:42", // Spring DST lookback.
        "2026-07-15T12:37:42", // No DST transition in the lookback.
        "2026-11-03T12:37:42", // Autumn DST lookback.
      ].map((now) => [rowType, endpoint, now]),
    ),
  )(
    "sends the visible preview range for %s via %s at %s and resets it on 12M selection",
    async (rowType, endpoint, clock) => {
      // Freeze only Date; UI interactions and query timers still run normally.
      vi.useFakeTimers({ toFake: ["Date"] });
      const now = new Date(clock);
      vi.setSystemTime(now);
      const expectedStart = new Date(now);
      expectedStart.setDate(expectedStart.getDate() - 30);
      expectedStart.setUTCMinutes(0, 0, 0);
      const expectedEnd = new Date(now);
      expectedEnd.setDate(expectedEnd.getDate() + 1);
      expectedEnd.setHours(0, 0, 0, 0);
      const expectedYearStart = new Date(now);
      expectedYearStart.setFullYear(expectedYearStart.getFullYear() - 1);
      expectedYearStart.setUTCMinutes(0, 0, 0);
      mocks.get.mockImplementation(async (url) => {
        if (url === `/projects/${PROJECT_ID}`) {
          return {
            data: {
              result: {
                id: PROJECT_ID,
                source: rowType === "voiceCalls" ? "simulator" : "api",
              },
            },
          };
        }
        if (url === endpoint) {
          if (rowType === "voiceCalls")
            return {
              data: {
                count: 0,
                count_is_lower_bound: false,
                query_complete: true,
                query_status: "complete",
                total_pages: 0,
                current_page: 1,
                next: null,
                previous: null,
                results: [],
                config: [],
                has_more: false,
                next_cursor: null,
              },
            };
          return {
            data: {
              status: true,
              result: {
                config: [],
                table: [],
                metadata: {
                  total_rows: 0,
                  has_more: false,
                  next_cursor: null,
                },
              },
            },
          };
        }
        throw new Error(`Unexpected GET ${url}`);
      });
      const view = renderTaskMapping(vi.fn(), {
        initialRowType: rowType,
        hostsFilter: true,
        variables: [],
      });
      await screen.findByText(/No matching .* found/);
      expect(
        screen.getByText("Try changing the filters or date range."),
      ).toBeVisible();
      const calls = () =>
        mocks.get.mock.calls.filter(([url]) => url === endpoint);
      const first = calls().at(-1)[1].params;
      const initial = JSON.parse(first.filters).find(
        (f) => f.column_id === "created_at",
      );
      expect(initial.filter_config.filter_op).toBe("between");
      expect(
        initial.filter_config.filter_value.map((value) => new Date(value)),
      ).toEqual([expectedStart, expectedEnd]);
      await userEvent.click(screen.getByRole("button", { name: "Past 30D" }));
      await userEvent.click(screen.getByRole("menuitem", { name: "Past 12M" }));
      await waitFor(() => {
        const latest = JSON.parse(calls().at(-1)[1].params.filters).find(
          (f) => f.column_id === "created_at",
        );
        expect(
          latest.filter_config.filter_value.map((value) => new Date(value)),
        ).toEqual([expectedYearStart, expectedEnd]);
      });
      expect(screen.getByRole("button", { name: "Past 12M" })).toBeVisible();
      expect(calls().at(-1)[1].params).not.toHaveProperty("cursor");
      expect(mocks.post).not.toHaveBeenCalled();
      view.unmount();
    },
  );

  it("keeps an arbitrary exact path as a manual free-text mapping", async () => {
    mocks.exactFields = [];
    const onReadyChange = vi.fn();
    renderTaskMapping(onReadyChange);

    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).not.toBeDisabled());
    await userEvent.click(input);
    await userEvent.type(input, "historical.custom.path");

    expect(input).toHaveValue("historical.custom.path");
    await waitFor(() =>
      expect(onReadyChange).toHaveBeenCalledWith(true, {
        evaluation_result: "historical.custom.path",
      }),
    );
  });

  it("pauses at the cursor round bound and resumes only after explicit confirmation", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
      if (url === "/spans/") {
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
                  ...SPAN_REFERENCE,
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
      if (url === "/span-details/span-rare/") {
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                trace_id: "trace-rare",
                id: "span-rare",
                input: "rare preview value",
              },
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    renderTaskMapping(vi.fn());

    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).not.toBeDisabled());

    let spanRequests = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    );
    expect(spanRequests).toHaveLength(13);
    expect(screen.queryByText(/no span data found/i)).not.toBeInTheDocument();

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    await userEvent.click(continueSearch);

    await waitFor(() => {
      spanRequests = mocks.get.mock.calls.filter(([url]) => url === "/spans/");
      expect(spanRequests).toHaveLength(14);
    });
    expect(spanRequests[13][1].params).toEqual(
      expect.objectContaining({
        cursor_mode: true,
        cursor: "checkpoint-12",
      }),
    );
    expect(screen.getByText("Row 1 of 1")).toBeInTheDocument();
  });

  it("preserves proven rows and retries the saved cursor after a continuation transport failure", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
      if (url === "/spans/") {
        const callIndex = spanListCalls;
        spanListCalls += 1;
        if (callIndex === 13) {
          throw new Error("temporary transport failure");
        }
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
                          ...SPAN_REFERENCE,
                          span_id: "span-buffered",
                          trace_id: "trace-buffered",
                          input: "proven preview value",
                        },
                      ]
                    : [],
                metadata: {
                  total_rows: callIndex === 0 ? 1 : 0,
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
                  ...SPAN_REFERENCE,
                  span_id: "span-rare",
                  trace_id: "trace-rare",
                  input: "rare preview value",
                },
              ],
              metadata: {
                has_more: false,
                next_cursor: null,
                total_rows: 2,
              },
            },
          },
        };
      }
      if (url === "/span-details/span-buffered/") {
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                trace_id: "trace-buffered",
                id: "span-buffered",
                input: "proven preview value",
              },
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    renderTaskMapping(vi.fn());

    await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    expect(await screen.findByText("Row 1 of 1")).toBeInTheDocument();

    await userEvent.click(
      await screen.findByRole("button", { name: "Continue search" }),
    );
    await waitFor(() => {
      expect(
        mocks.get.mock.calls.filter(([url]) => url === "/spans/"),
      ).toHaveLength(14);
    });

    // The failed continuation keeps the proven row visible and exposes an
    // explicit retry instead of turning the preview into an empty/error page.
    expect(screen.getByText("Row 1 of 1")).toBeInTheDocument();
    const retry = await screen.findByRole("button", {
      name: "Continue search",
    });
    let spanRequests = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    );
    expect(spanRequests[13][1].params.cursor).toBe("checkpoint-12");

    await userEvent.click(retry);
    await waitFor(() => {
      spanRequests = mocks.get.mock.calls.filter(([url]) => url === "/spans/");
      expect(spanRequests).toHaveLength(15);
    });
    // Restoring the pre-attempt requested-cursor set makes the exact saved
    // checkpoint retryable once; the failed attempt did not poison it.
    expect(spanRequests[14][1].params.cursor).toBe("checkpoint-12");
    expect(await screen.findByText("Row 1 of 2")).toBeInTheDocument();
  });

  it.each(["light", "dark"])(
    "keeps the task mapping keyboard-focusable in %s",
    async (mode) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const view = render(
        <QueryClientProvider client={client}>
          <Box
            sx={{
              bgcolor: "background.paper",
              overflow: "hidden",
            }}
          >
            <TracingTestMode
              templateId="eval-template-1"
              variables={["evaluation_result"]}
              initialProjectId={PROJECT_ID}
              initialRowType="spans"
              allowCustomFieldPath
              taskMapping
            />
          </Box>
        </QueryClientProvider>,
        { theme: productionTheme(mode) },
      );
      const input = await screen.findByPlaceholderText(
        "Search or type a path (e.g. attributes.input.value)",
      );
      await waitFor(() => expect(input).toBeEnabled());
      const user = userEvent.setup();
      for (let step = 0; step < 40 && document.activeElement !== input; step++)
        await user.tab();
      expect(input).toHaveFocus();
      view.unmount();
      client.clear();
    },
  );

  it.each([
    [
      true,
      true,
      "Sample row could not be loaded. Retry loading the sample row.",
    ],
    [
      true,
      false,
      "Sample row could not be loaded. Retry loading the sample row. Map all variables to enable testing & adding",
    ],
    [false, false, "Map all variables to enable testing & adding"],
    [false, true, ""],
  ])(
    "separates failed row (%s) from complete mappings (%s)",
    async (failed, mapped, message) => {
      const get = mocks.get.getMockImplementation();
      let fail = failed;
      mocks.get.mockImplementation((url, config) => {
        if (url === "/spans/" && fail)
          return Promise.reject(new Error("offline"));
        return get(url, config);
      });
      const ready = vi.fn();
      const readiness = vi.fn();
      renderTaskMapping(ready, {
        taskMapping: true,
        initialMapping: mapped ? { evaluation_result: "final_status" } : {},
        onReadinessChange: readiness,
      });
      await waitFor(() =>
        expect(readiness).toHaveBeenLastCalledWith(
          expect.objectContaining({
            ready: !failed && mapped,
            allMapped: mapped,
            sampleStatus: failed ? "error" : "ready",
            message,
          }),
        ),
      );
      expect(ready).toHaveBeenLastCalledWith(
        !failed && mapped,
        mapped ? { evaluation_result: "final_status" } : {},
      );
      if (failed) {
        const retry = screen.getByRole("button", {
          name: "Retry loading sample row",
        });
        const user = userEvent.setup();
        for (
          let step = 0;
          step < 30 && document.activeElement !== retry;
          step++
        )
          await user.tab();
        expect(retry).toHaveFocus();
        fail = false;
        await user.keyboard("{Enter}");
        await waitFor(() =>
          expect(readiness).toHaveBeenLastCalledWith(
            expect.objectContaining({ sampleStatus: "ready", ready: mapped }),
          ),
        );
        if (mapped)
          expect(
            screen.getByPlaceholderText(
              "Search or type a path (e.g. attributes.input.value)",
            ),
          ).toHaveValue("final_status");
      }
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it.each(["light", "dark"])(
    "opens the normal taskMapping + hostsFilter date portal by keyboard in %s",
    async (mode) => {
      renderTaskMapping(
        vi.fn(),
        { taskMapping: true, hostsFilter: true },
        { theme: productionTheme(mode) },
      );
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: "Past 30D" }));
      await user.click(screen.getByRole("menuitem", { name: "Custom range" }));
      const input = await screen.findByLabelText("Start Date");
      for (let step = 0; step < 40 && document.activeElement !== input; step++)
        await user.tab();
      expect(input).toHaveFocus();
    },
  );

  it("does not call an unfinished sample search empty and preserves mappings on Continue search", async () => {
    const get = mocks.get.getMockImplementation();
    let pages = 0;
    mocks.get.mockImplementation((url, config) => {
      if (url === "/spans/" && pages++ < 13)
        return Promise.resolve({
          data: {
            status: true,
            result: {
              config: [],
              table: [],
              metadata: {
                total_rows: 0,
                has_more: true,
                next_cursor: `pending-${pages}`,
                total_rows_is_lower_bound: true,
              },
            },
          },
        });
      return get(url, config);
    });
    const ready = vi.fn();
    const readiness = vi.fn();
    renderTaskMapping(ready, {
      taskMapping: true,
      initialMapping: { evaluation_result: "final_status" },
      onReadinessChange: readiness,
    });
    const next = await screen.findByRole("button", { name: "Continue search" });
    expect(readiness).toHaveBeenLastCalledWith(
      expect.objectContaining({
        ready: false,
        sampleStatus: "pending",
        message: "Continue search to load a sample row.",
      }),
    );
    const user = userEvent.setup();
    for (let step = 0; step < 40 && document.activeElement !== next; step++)
      await user.tab();
    expect(next).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(ready).toHaveBeenLastCalledWith(true, {
        evaluation_result: "final_status",
      }),
    );
    expect(readiness).toHaveBeenLastCalledWith(
      expect.objectContaining({ ready: true, message: "" }),
    );
  });

  it("does not require a sample for a task with data-injected context", async () => {
    const get = mocks.get.getMockImplementation();
    mocks.get.mockImplementation((url, config) =>
      url === "/spans/"
        ? Promise.reject(new Error("offline"))
        : get(url, config),
    );
    const ready = vi.fn();
    const readiness = vi.fn();
    renderTaskMapping(ready, {
      taskMapping: true,
      hasDataInjection: true,
      onReadinessChange: readiness,
    });
    await waitFor(() =>
      expect(readiness).toHaveBeenLastCalledWith(
        expect.objectContaining({
          sampleStatus: "error",
          ready: true,
          message: "",
        }),
      ),
    );
    expect(ready).toHaveBeenLastCalledWith(true, {});
    expect(
      screen.queryByRole("button", { name: "Retry loading sample row" }),
    ).not.toBeInTheDocument();
  });

  it("blocks a failed sample detail and retries without discarding mappings", async () => {
    const get = mocks.get.getMockImplementation();
    let fail = true;
    mocks.get.mockImplementation((url, config) => {
      if (url === "/span-details/span-1/" && fail)
        return Promise.reject(new Error("offline"));
      return get(url, config);
    });
    const ready = vi.fn();
    renderTaskMapping(ready, {
      taskMapping: true,
      initialMapping: { evaluation_result: "final_status" },
    });
    const retry = await screen.findByRole("button", {
      name: "Retry loading sample row",
    });
    expect(ready).toHaveBeenLastCalledWith(false, {
      evaluation_result: "final_status",
    });
    fail = false;
    await userEvent.click(retry);
    await waitFor(() =>
      expect(ready).toHaveBeenLastCalledWith(true, {
        evaluation_result: "final_status",
      }),
    );
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/span-details/span-1/"),
    ).toHaveLength(2);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  const round8List = (table, metadata = {}) => ({
    data: {
      status: true,
      result: {
        config: [],
        table,
        metadata: {
          total_rows: table.length,
          has_more: false,
          next_cursor: null,
          ...metadata,
        },
      },
    },
  });
  const round8Row = {
    ...SPAN_REFERENCE,
    span_id: "span-1",
    trace_id: "trace-1",
  };
  const round8Fresh = { ...round8Row, _version: "18446744073709551615" };
  const round8Detail = (row) => ({
    data: { status: true, result: { observation_span: row } },
  });
  const round8Reads = (url) =>
    mocks.get.mock.calls.filter(([path]) => path === url);
  const round8ResponseError = async (status, code) => {
    const api = await vi.importActual("src/utils/axios");
    const rejectResponse = api.default.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    ).rejected;
    const config = {
      method: "get",
      url: api.endpoints.project.getObservationSpan("span-1"),
    };
    const error = await rejectResponse({
      config,
      response: {
        status,
        config,
        data: {
          status: false,
          type: status >= 500 ? "server_error" : "client_error",
          code,
          message: "Could not read the selected span.",
        },
      },
    }).catch((failure) => failure);
    expect(error).toMatchObject({ statusCode: status, code });
    expect(error.response).toBeUndefined();
    return error;
  };

  it.each(["winner", "409", "404", "raw409", "raw404"])(
    "round8 refreshes a stale task span (%s) once and selects its fresh physical reference",
    async (failure) => {
      const responseError = ["409", "404"].includes(failure)
        ? await round8ResponseError(
            Number(failure),
            failure === "409"
              ? "span_reference_changed"
              : "span_reference_not_found",
          )
        : null;
      const get = mocks.get.getMockImplementation();
      let listCalls = 0;
      mocks.get.mockImplementation(async (url, config) => {
        if (url === "/spans/")
          return round8List(
            ++listCalls === 1
              ? [round8Row]
              : [
                  { ...round8Fresh, service_name: "other-physical-span" },
                  round8Fresh,
                ],
          );
        if (url === "/span-details/span-1/") {
          if (
            config.params.expected_version === round8Row._version &&
            failure !== "winner"
          ) {
            const status = Number(failure.replace("raw", ""));
            const code =
              status === 409
                ? "span_reference_changed"
                : "span_reference_not_found";
            if (failure.startsWith("raw"))
              throw { response: { status, data: { code } } };
            throw responseError;
          }
          return round8Detail({
            ...round8Fresh,
            input: "fresh verified winner",
          });
        }
        return get(url, config);
      });
      const ready = vi.fn();
      const readiness = vi.fn();
      renderTaskMapping(ready, {
        taskMapping: true,
        initialMapping: { evaluation_result: "final_status" },
        onReadinessChange: readiness,
      });
      const retry = await screen.findByRole("button", {
        name: "Retry loading sample row",
      });
      expect(ready).toHaveBeenLastCalledWith(false, {
        evaluation_result: "final_status",
      });
      expect(
        screen.queryByText(/fresh verified winner/),
      ).not.toBeInTheDocument();
      expect(round8Reads("/spans/")).toHaveLength(1);
      const mappingInput = screen.getByPlaceholderText(
        "Search or type a path (e.g. attributes.input.value)",
      );
      await userEvent.clear(mappingInput);
      await userEvent.type(mappingInput, "reviewed_status{Enter}");
      await waitFor(() =>
        expect(ready).toHaveBeenLastCalledWith(false, {
          evaluation_result: "reviewed_status",
        }),
      );
      await userEvent.click(retry);
      await waitFor(() =>
        expect(ready).toHaveBeenLastCalledWith(true, {
          evaluation_result: "reviewed_status",
        }),
      );
      expect(readiness).toHaveBeenLastCalledWith(
        expect.objectContaining({ sampleStatus: "ready", message: "" }),
      );
      expect(round8Reads("/spans/")).toHaveLength(2);
      expect(round8Reads("/spans/")[1][1].params).not.toHaveProperty("cursor");
      expect(round8Reads("/span-details/span-1/")).toHaveLength(2);
      expect(round8Reads("/span-details/span-1/")[1][1].params).toMatchObject({
        project_id: PROJECT_ID,
        trace_id: "trace-1",
        service_name: round8Row.service_name,
        observation_type: round8Row.observation_type,
        expected_version: round8Fresh._version,
        expected_start_time: round8Row.start_time,
      });
      expect(screen.getByText("Row 2 of 2")).toBeVisible();
      expect(mappingInput).toHaveValue("reviewed_status");
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it.each([
    "stale again",
    "invalid detail",
    "different physical span",
    "missing from list",
    "invalid list row",
  ])("round8 keeps recovery bounded and blocked for %s", async (failure) => {
    const get = mocks.get.getMockImplementation();
    let listCalls = 0;
    mocks.get.mockImplementation(async (url, config) => {
      if (url === "/spans/") {
        listCalls++;
        if (listCalls === 1) return round8List([round8Row]);
        if (failure === "missing from list") return round8List([]);
        if (failure === "invalid list row")
          return round8List([{ ...round8Fresh, _version: undefined }]);
        if (failure === "different physical span")
          return round8List([
            { ...round8Fresh, service_name: "other-service" },
          ]);
        return round8List([round8Fresh]);
      }
      if (url === "/span-details/span-1/")
        return round8Detail(
          listCalls === 1
            ? round8Fresh
            : {
                ...round8Fresh,
                _version: failure === "invalid detail" ? undefined : "0",
                input: "unverified value",
              },
        );
      return get(url, config);
    });
    const ready = vi.fn();
    const readiness = vi.fn();
    renderTaskMapping(ready, {
      taskMapping: true,
      initialMapping: { evaluation_result: "final_status" },
      onReadinessChange: readiness,
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "Retry loading sample row" }),
    );
    await waitFor(() => expect(round8Reads("/spans/")).toHaveLength(2));
    await waitFor(() =>
      expect(readiness.mock.lastCall[0].sampleStatus).not.toBe("loading"),
    );
    expect(ready).toHaveBeenLastCalledWith(false, {
      evaluation_result: "final_status",
    });
    expect(ready.mock.calls.some(([value]) => value)).toBe(false);
    expect(screen.queryByText(/unverified value/)).not.toBeInTheDocument();
    expect(round8Reads("/span-details/span-1/")).toHaveLength(
      ["stale again", "invalid detail"].includes(failure) ? 2 : 1,
    );
    if (
      ["stale again", "invalid detail", "invalid list row"].includes(failure)
    ) {
      // The list parser rejects an invalid reference before row selection.
      expect(readiness.mock.lastCall[0].sampleStatus).toBe("error");
      expect(
        screen.getByRole("button", { name: "Retry loading sample row" }),
      ).toBeEnabled();
    } else {
      expect(
        screen.getByText(
          /selected span is not available in the refreshed sample list/i,
        ),
      ).toBeVisible();
    }
    expect(round8Reads("/spans/")).toHaveLength(2);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("round8 cancels fresh-list recovery on filter replacement and ignores its late response", async () => {
    const get = mocks.get.getMockImplementation();
    let calls = 0,
      resolveRefresh,
      refreshSignal;
    mocks.get.mockImplementation(async (url, config) => {
      if (url === "/spans/") {
        calls++;
        if (calls === 2) {
          refreshSignal = config.signal;
          return new Promise((resolve) => {
            resolveRefresh = resolve;
          });
        }
        return round8List(calls === 1 ? [round8Row] : []);
      }
      if (url === "/span-details/span-1/") return round8Detail(round8Fresh);
      return get(url, config);
    });
    const ready = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const show = (localFilters) => (
      <QueryClientProvider client={client}>
        <TracingTestMode
          templateId="eval-template-1"
          variables={["evaluation_result"]}
          initialProjectId={PROJECT_ID}
          initialRowType="spans"
          allowCustomFieldPath
          taskMapping
          initialMapping={{ evaluation_result: "final_status" }}
          onReadyChange={ready}
          localFilters={localFilters}
        />
      </QueryClientProvider>
    );
    const view = render(show([]));
    await userEvent.click(
      await screen.findByRole("button", { name: "Retry loading sample row" }),
    );
    await waitFor(() => expect(resolveRefresh).toBeTypeOf("function"));
    view.rerender(
      show([{ column: "span_name", operator: "equals", value: "replacement" }]),
    );
    await waitFor(() => expect(refreshSignal.aborted).toBe(true));
    await act(async () => resolveRefresh(round8List([round8Fresh])));
    expect(ready).toHaveBeenLastCalledWith(false, {
      evaluation_result: "final_status",
    });
    expect(round8Reads("/span-details/span-1/")).toHaveLength(1);
    view.unmount();
    client.clear();
  });

  it.each(["offline", "span_reference_unavailable"])(
    "round8 retries transient %s detail without refreshing the list",
    async (failure) => {
      const responseError =
        failure === "offline"
          ? new Error("offline")
          : await round8ResponseError(503, failure);
      const get = mocks.get.getMockImplementation();
      let attempts = 0;
      mocks.get.mockImplementation(async (url, config) => {
        if (url === "/span-details/span-1/" && attempts++ === 0)
          throw responseError;
        return get(url, config);
      });
      const ready = vi.fn();
      renderTaskMapping(ready, {
        taskMapping: true,
        initialMapping: { evaluation_result: "final_status" },
      });
      const retry = await screen.findByRole("button", {
        name: "Retry loading sample row",
      });
      expect(screen.queryByText(SPAN_REFERENCE_ERROR)).not.toBeInTheDocument();
      await userEvent.click(retry);
      await waitFor(() =>
        expect(ready).toHaveBeenLastCalledWith(true, {
          evaluation_result: "final_status",
        }),
      );
      expect(round8Reads("/spans/")).toHaveLength(1);
      expect(round8Reads("/span-details/span-1/")).toHaveLength(2);
    },
  );

  it.each(["session detail", "first trace spans", "session list"])(
    "round8 recovers failed %s without losing Session mappings",
    async (failure) => {
      const get = mocks.get.getMockImplementation();
      let fail = true,
        resolveRetry;
      const failingUrl =
        failure === "session list"
          ? "/sessions/"
          : failure === "session detail"
            ? "/sessions/session-r8/"
            : "/traces/trace-r8/";
      mocks.get.mockImplementation(async (url, config) => {
        let result;
        if (url === "/sessions/")
          result = round8List([{ session_id: "session-r8" }]);
        if (url === "/sessions/session-r8/")
          result = {
            data: {
              result: {
                session_metadata: {},
                response: [{ trace_id: "trace-r8" }],
              },
            },
          };
        if (url === "/traces/trace-r8/")
          result = {
            data: {
              status: true,
              result: { trace: {}, observation_spans: [] },
            },
          };
        if (url === failingUrl) {
          if (fail) throw new Error("offline");
          return new Promise((resolve) => {
            resolveRetry = () => resolve(result);
          });
        }
        return result || get(url, config);
      });
      const ready = vi.fn(),
        readiness = vi.fn();
      renderTaskMapping(ready, {
        initialRowType: "sessions",
        taskMapping: true,
        initialMapping: {
          evaluation_result: "traces[0].spans[0].final_status",
        },
        onReadinessChange: readiness,
      });
      const retry = await screen.findByRole("button", {
        name: "Retry loading sample row",
      });
      expect(readiness).toHaveBeenLastCalledWith(
        expect.objectContaining({ ready: false, sampleStatus: "error" }),
      );
      fail = false;
      await userEvent.click(retry);
      await waitFor(() => expect(resolveRetry).toBeTypeOf("function"));
      expect(ready.mock.lastCall[0]).toBe(false);
      expect(readiness).toHaveBeenLastCalledWith(
        expect.objectContaining({ sampleStatus: "loading", ready: false }),
      );
      await act(async () => resolveRetry());
      await waitFor(() =>
        expect(ready).toHaveBeenLastCalledWith(true, {
          evaluation_result: "traces[0].spans[0].final_status",
        }),
      );
      expect(round8Reads(failingUrl)).toHaveLength(2);
      expect(round8Reads("/sessions/")).toHaveLength(
        failure === "session list" ? 2 : 1,
      );
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it.each(["ready", "empty"])(
    "round8 distinguishes paused-empty Session lists from completed %s after Continue",
    async (outcome) => {
      const get = mocks.get.getMockImplementation();
      let pages = 0;
      mocks.get.mockImplementation(async (url, config) => {
        if (url === "/sessions/") {
          if (pages++ < 13)
            return round8List([], {
              has_more: true,
              next_cursor: `session-page-${pages}`,
              total_rows_is_lower_bound: true,
            });
          return round8List(
            outcome === "ready" ? [{ session_id: "session-r8" }] : [],
          );
        }
        if (url === "/sessions/session-r8/")
          return { data: { result: { session_metadata: {}, response: [] } } };
        return get(url, config);
      });
      const ready = vi.fn(),
        readiness = vi.fn();
      renderTaskMapping(ready, {
        initialRowType: "sessions",
        taskMapping: true,
        initialMapping: {
          evaluation_result: "traces[0].spans[0].final_status",
        },
        onReadinessChange: readiness,
      });
      const next = await screen.findByRole("button", {
        name: "Continue search",
      });
      expect(readiness).toHaveBeenLastCalledWith(
        expect.objectContaining({
          ready: false,
          sampleStatus: "pending",
          message: "Continue search to load a sample row.",
        }),
      );
      await userEvent.click(next);
      await waitFor(() =>
        expect(readiness).toHaveBeenLastCalledWith(
          expect.objectContaining({
            ready: outcome === "ready",
            sampleStatus: outcome,
          }),
        ),
      );
      expect(ready).toHaveBeenLastCalledWith(outcome === "ready", {
        evaluation_result: "traces[0].spans[0].final_status",
      });
      expect(round8Reads("/sessions/")).toHaveLength(14);
      expect(
        screen.queryByRole("button", { name: "Continue search" }),
      ).not.toBeInTheDocument();
      if (outcome === "empty")
        expect(
          screen.queryByRole("button", { name: "Retry loading sample row" }),
        ).not.toBeInTheDocument();
    },
  );

  it("retries a cold initial preview failure in place", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
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
                  ...SPAN_REFERENCE,
                  span_id: "span-after-cold-retry",
                  trace_id: "trace-after-cold-retry",
                  input: "eval preview recovered",
                },
              ],
              metadata: { has_more: false, next_cursor: null, total_rows: 1 },
            },
          },
        };
      }
      if (url === "/span-details/span-after-cold-retry/") {
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                trace_id: "trace-after-cold-retry",
                id: "span-after-cold-retry",
                input: "eval preview recovered",
              },
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    renderTaskMapping(vi.fn());

    expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Row 1 of 1")).toBeInTheDocument();
    expect(spanListCalls).toBe(2);
  });

  it("loads eval preview rows through the strict legacy cursor fallback", async () => {
    let spanListCalls = 0;
    mocks.get.mockImplementation(async (url, config) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
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
                  ...SPAN_REFERENCE,
                  span_id: "span-legacy-eval",
                  trace_id: "trace-legacy-eval",
                  input: "legacy eval preview",
                },
              ],
              metadata: { total_rows: 1 },
            },
          },
        };
      }
      if (url === "/span-details/span-legacy-eval/") {
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                trace_id: "trace-legacy-eval",
                id: "span-legacy-eval",
                input: "legacy eval preview",
              },
            },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    renderTaskMapping(vi.fn());

    expect(await screen.findByText("Row 1 of 1")).toBeInTheDocument();
    expect(spanListCalls).toBe(2);
    const spanRequests = mocks.get.mock.calls.filter(
      ([url]) => url === "/spans/",
    );
    expect(spanRequests[0][1].params).toEqual(
      expect.objectContaining({ cursor_mode: true, page_number: 0 }),
    );
    expect(spanRequests[1][1].params).not.toHaveProperty("cursor_mode");
  });

  it.each([
    [
      "degraded",
      "Attribute suggestions are temporarily unavailable. Enter an exact attribute name.",
    ],
    [
      "error",
      "Attribute suggestions are temporarily unavailable. Enter an exact attribute name.",
    ],
  ])(
    "shows a sanitized %s warning instead of treating no exact fields as authoritative",
    async (readState, message) => {
      mocks.exactFields = [];
      mocks.exactReadState = readState;
      renderTaskMapping(vi.fn());

      const input = await screen.findByPlaceholderText(
        "Search or type a path (e.g. attributes.input.value)",
      );
      await waitFor(() => expect(input).not.toBeDisabled());
      await userEvent.type(input, "final_status");

      expect(await screen.findByText(message)).toBeInTheDocument();
    },
  );

  it("keeps a verified suggestion selectable while exact-name entry remains available", async () => {
    mocks.exactFields = ["final_status"];
    mocks.exactReadState = "degraded";
    const onReadyChange = vi.fn();
    renderTaskMapping(onReadyChange);

    const input = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(input).not.toBeDisabled());
    await userEvent.click(input);
    await userEvent.type(input, "final_status");

    expect(
      await screen.findByText(
        "Attribute suggestions are temporarily unavailable. Enter an exact attribute name.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      await screen.findByRole("option", { name: "final_status" }),
    );

    expect(input).toHaveValue("final_status");
    await waitFor(() =>
      expect(onReadyChange).toHaveBeenCalledWith(true, {
        evaluation_result: "final_status",
      }),
    );
  });

  it("keeps service-colliding span selections distinct and cancels stale detail", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    const first = {
      ...SPAN_REFERENCE,
      span_id: "span-shared",
      trace_id: "trace-shared",
    };
    const second = { ...first, service_name: "second-service" };
    let firstSignal, resolveFirst;
    mocks.get.mockImplementation(async (url, options = {}) => {
      if (url === "/spans/")
        return {
          data: {
            status: true,
            result: {
              config: [],
              table: [first, second],
              metadata: { total_rows: 2, has_more: false, next_cursor: null },
            },
          },
        };
      if (url === "/span-details/span-shared/") {
        if (options.params.service_name === first.service_name) {
          firstSignal = options.signal;
          return new Promise((resolve) => {
            resolveFirst = resolve;
          });
        }
        return {
          data: {
            status: true,
            result: {
              observation_span: { ...second, input: "second physical span" },
            },
          },
        };
      }
      return defaultGet(url, options);
    });
    renderTaskMapping(vi.fn());
    await waitFor(() => expect(resolveFirst).toBeTypeOf("function"));
    await userEvent.click(screen.getByRole("button", { name: "Next row" }));
    await screen.findByText(/second physical span/);
    expect(firstSignal.aborted).toBe(true);
    await act(async () =>
      resolveFirst({
        data: {
          status: true,
          result: {
            observation_span: { ...first, input: "stale physical span" },
          },
        },
      }),
    );
    expect(screen.queryByText(/stale physical span/)).not.toBeInTheDocument();
    expect(screen.getByText(/second physical span/)).toBeVisible();
    expect(screen.getByText("Row 2 of 2")).toBeVisible();
    expect(
      mocks.get.mock.calls.some(([url]) => url.startsWith("/traces/")),
    ).toBe(false);
  });

  it("renders a stale-winner failure instead of silently choosing a different span", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (url, options) => {
      if (url === "/span-details/span-1/")
        return {
          data: {
            status: true,
            result: {
              observation_span: {
                ...SPAN_REFERENCE,
                span_id: "span-1",
                trace_id: "trace-1",
                _version: "18446744073709551615",
                input: "wrong winner must never render",
              },
            },
          },
        };
      return defaultGet(url, options);
    });
    renderTaskMapping(vi.fn());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      SPAN_REFERENCE_ERROR,
    );
    expect(
      screen.queryByText(/wrong winner must never render/),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(
      mocks.get.mock.calls.some(([url]) => url.startsWith("/traces/")),
    ).toBe(false);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("omits the model override when the Code playground supplies no model", async () => {
    mocks.post.mockResolvedValueOnce({ data: { status: true, result: { output: 1 } } });
    const ref = React.createRef();
    renderTaskMapping(vi.fn(), { ref, model: "" });
    await screen.findByPlaceholderText("Search or type a path (e.g. attributes.input.value)");
    await act(async () => { ref.current.runTest(); });
    await waitFor(() => expect(mocks.post).toHaveBeenCalled());
    expect(mocks.post.mock.calls[0][1]).not.toHaveProperty("model");
  });

  it("never renders raw infrastructure details from a failed eval test", async () => {
    const rawError =
      "Code: 159. DB::Exception: Timeout exceeded\nStack trace: SELECT secret FROM spans";
    mocks.post.mockRejectedValueOnce({
      response: { status: 500, data: { detail: rawError } },
    });
    const ref = React.createRef();
    const onTestResult = vi.fn();
    renderTaskMapping(vi.fn(), { ref, onTestResult });

    await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(ref.current).toBeTruthy());
    await act(async () => {
      ref.current.runTest();
    });

    expect(
      await screen.findByText("Failed to run evaluation. Please retry."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/DB::Exception/)).not.toBeInTheDocument();
    expect(onTestResult).toHaveBeenCalledWith(
      false,
      "Failed to run evaluation. Please retry.",
    );
    const payload = mocks.post.mock.calls[0][1];
    expect(payload.span_context).toMatchObject({
      ...SPAN_REFERENCE,
      id: "span-1",
      trace_id: "trace-1",
    });
    expect(payload).not.toHaveProperty("span_id");
    expect(payload).not.toHaveProperty("trace_id");
  });
});

// The same trace id can live in several projects (replays, re-imports, shared
// voice provider accounts). The preview lists the selected project's rows, so
// every row-detail read must pin to that project instead of the newest copy.
describe("TracingTestMode row detail project pin", () => {
  const observeList = (rows) => ({
    data: {
      status: true,
      result: {
        config: [],
        table: rows,
        metadata: {
          total_rows: rows.length,
          has_more: false,
          next_cursor: null,
        },
      },
    },
  });
  const readsOf = (url) => mocks.get.mock.calls.filter(([u]) => u === url);

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.exactFields = ["final_status"];
    mocks.exactReadState = "complete";
  });

  it("pins the trace detail read to the selected project", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
      if (url === "/traces/") return observeList([{ trace_id: "trace-pin" }]);
      if (url === "/traces/trace-pin/") {
        return {
          data: {
            status: true,
            result: { trace: { trace_id: "trace-pin" }, observation_spans: [] },
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    const view = renderTaskMapping(vi.fn(), { initialRowType: "traces" });

    await waitFor(() => expect(readsOf("/traces/trace-pin/")).toHaveLength(1));
    expect(readsOf("/traces/trace-pin/")[0][1].params).toEqual({
      project_id: PROJECT_ID,
    });
    view.unmount();
  });

  it("pins the session's first-trace read to the selected project", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "api" } } };
      }
      if (url === "/sessions/") {
        return observeList([{ session_id: "session-pin" }]);
      }
      if (url === "/sessions/session-pin/") {
        return {
          data: {
            result: {
              session_metadata: {},
              response: [{ trace_id: "trace-session-first" }],
            },
          },
        };
      }
      if (url === "/traces/trace-session-first/") {
        return {
          data: { status: true, result: { trace: {}, observation_spans: [] } },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    const view = renderTaskMapping(vi.fn(), { initialRowType: "sessions" });

    await waitFor(() =>
      expect(readsOf("/traces/trace-session-first/")).toHaveLength(1),
    );
    expect(readsOf("/traces/trace-session-first/")[0][1].params).toEqual({
      project_id: PROJECT_ID,
    });
    view.unmount();
  });

  it("pins the voice-call detail read to the selected project", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === `/projects/${PROJECT_ID}`) {
        return { data: { result: { id: PROJECT_ID, source: "simulator" } } };
      }
      if (url === "/calls/") {
        return {
          data: {
            count: 1,
            count_is_lower_bound: false,
            query_complete: true,
            query_status: "complete",
            total_pages: 1,
            current_page: 1,
            next: null,
            previous: null,
            results: [{ id: "call-pin", trace_id: "trace-voice-pin" }],
            config: [],
            has_more: false,
            next_cursor: null,
          },
        };
      }
      if (url === "/calls/detail/") {
        return { data: { result: { status: "completed" } } };
      }
      throw new Error(`Unexpected GET ${url}`);
    });

    const view = renderTaskMapping(vi.fn(), { initialRowType: "voiceCalls" });

    await waitFor(() => expect(readsOf("/calls/detail/")).toHaveLength(1));
    expect(readsOf("/calls/detail/")[0][1].params).toEqual({
      trace_id: "trace-voice-pin",
      project_id: PROJECT_ID,
    });
    view.unmount();
  });
});
