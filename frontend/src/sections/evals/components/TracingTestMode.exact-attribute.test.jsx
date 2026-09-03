import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      getProjectById: (id) => `/projects/${id}`,
      getSpansForObserveProject: () => "/spans/",
      getTracesForObserveProject: () => "/traces/",
      projectSessionList: () => "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
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

vi.mock("src/sections/tasks/components/TaskLivePreview", () => ({
  buildApiFilterArray: () => [],
}));
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => null,
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

const PROJECT_ID = "00000000-0000-4000-8000-000000000901";

function renderTaskMapping(onReadyChange, extraProps = {}) {
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
  );
}

describe("TracingTestMode exact task attribute mapping", () => {
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
      if (url === "/traces/trace-1/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-1" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-1",
                    input: "generic preview value",
                    span_attributes: { input: "generic preview value" },
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
      if (url === "/traces/trace-buffered/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-buffered" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-buffered",
                    input: "proven preview value",
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
                    input: "eval preview recovered",
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
      if (url === "/traces/trace-legacy-eval/") {
        return {
          data: {
            status: true,
            result: {
              trace: { trace_id: "trace-legacy-eval" },
              observation_spans: [
                {
                  observation_span: {
                    id: "span-legacy-eval",
                    input: "legacy eval preview",
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
  });
});
