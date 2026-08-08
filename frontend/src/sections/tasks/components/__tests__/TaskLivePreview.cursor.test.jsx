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

function PreviewHarness({ rowType = "spans", projectId = PROJECT_ID }) {
  const { control } = useForm({
    defaultValues: {
      filters: [],
      startDate: null,
      endDate: null,
      evalsDetails: [],
      rowType,
    },
  });
  return <TaskLivePreview control={control} projectId={projectId} />;
}

PreviewHarness.propTypes = {
  rowType: PropTypes.string,
  projectId: PropTypes.string,
};

describe("TaskLivePreview sparse cursor continuation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
              result: {
                config: [],
                table: [],
                metadata: {
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

  it("resumes a sparse voice-call preview beyond the first hop budget", async () => {
    let listCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url === "/calls/") {
        const callIndex = listCalls;
        listCalls += 1;
        if (callIndex < 24) {
          return {
            data: {
              result: {
                results: [],
                has_more: true,
                next_cursor: `voice-checkpoint-${callIndex}`,
              },
            },
          };
        }
        return {
          data: {
            result: {
              results: [{ id: "call-rare", trace_id: "trace-voice-rare" }],
              has_more: false,
              next_cursor: null,
              count: 1,
            },
          },
        };
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
              result: {
                config: [],
                table: [],
                metadata: {
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
            result: {
              config: [],
              table: [],
              metadata: {
                has_more: true,
                next_cursor: `terminal-checkpoint-${callIndex}`,
              },
            },
          },
        };
      }
      return {
        data: {
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

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    await act(async () => continueSearch.click());

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

  it("fails closed without looping when the API repeats a signed cursor", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          config: [],
          table: [],
          metadata: {
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
          result: {
            config: [],
            table: [],
            metadata: {
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
              result: {
                config: [],
                table: [],
                metadata: {
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
              result: {
                config: [],
                table: [],
                metadata: {
                  has_more: true,
                  next_cursor: `old-scope-checkpoint-${callIndex}`,
                },
              },
            },
          };
        }
        return {
          data: {
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
