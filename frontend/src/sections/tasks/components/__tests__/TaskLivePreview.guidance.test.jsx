import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { act, render, screen } from "src/utils/test-utils";

// When the preview stops before it has searched everything, it must say what
// happened and what the user can do next, without paging internals.
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      getCallLogs: "/calls/",
      getTracesForObserveProject: () => "/traces/",
      getSpansForObserveProject: () => "/spans/",
      projectSessionList: () => "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
      getObservationSpan: (id) => `/span-details/${id}/`,
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

const PROJECT_ID = "00000000-0000-4000-8000-000000000903";
// The preview follows this many empty chunks before it hands the search back.
const EMPTY_CHUNKS_PER_ATTEMPT = 13;
const JARGON = /bounded|saved position|exact preview/i;

function PreviewHarness({ rowType }) {
  const { control } = useForm({
    defaultValues: {
      filters: [],
      startDate: null,
      endDate: null,
      evalsDetails: [],
      rowType,
    },
  });
  return <TaskLivePreview control={control} projectId={PROJECT_ID} />;
}

PreviewHarness.propTypes = {
  rowType: PropTypes.string.isRequired,
};

const emptyChunk = (callIndex) => ({
  data: {
    status: true,
    result: {
      config: [],
      table: [],
      metadata: {
        total_rows: 0,
        has_more: true,
        next_cursor: `guidance-checkpoint-${callIndex}`,
      },
    },
  },
});

const renderPreview = (rowType) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PreviewHarness rowType={rowType} />
    </QueryClientProvider>,
  );
};

describe("TaskLivePreview guidance copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ["spans", "/spans/"],
    ["traces", "/traces/"],
  ])(
    "says no %s matched yet and offers to continue the search",
    async (rowType, listUrl) => {
      let listCalls = 0;
      mocks.get.mockImplementation(async (url) => {
        if (url !== listUrl) throw new Error(`Unexpected GET ${url}`);
        const callIndex = listCalls;
        listCalls += 1;
        return emptyChunk(callIndex);
      });
      renderPreview(rowType);

      const continueSearch = await screen.findByRole("button", {
        name: "Continue search",
      });
      expect(listCalls).toBe(EMPTY_CHUNKS_PER_ATTEMPT);
      expect(continueSearch).toBeEnabled();
      expect(
        screen.getByText(`No matching ${rowType} found so far.`),
      ).toBeVisible();
      expect(
        screen.getByText(
          `Only part of this project has been searched. Continue the search to check more ${rowType}, or adjust the filters.`,
        ),
      ).toBeVisible();
      expect(screen.queryByText(JARGON)).not.toBeInTheDocument();
    },
  );

  it("says the search did not finish and how to pick it back up", async () => {
    let listCalls = 0;
    mocks.get.mockImplementation(async (url) => {
      if (url !== "/spans/") throw new Error(`Unexpected GET ${url}`);
      const callIndex = listCalls;
      listCalls += 1;
      if (callIndex < EMPTY_CHUNKS_PER_ATTEMPT) return emptyChunk(callIndex);
      throw new Error("transport failure");
    });
    renderPreview("spans");

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    await act(async () => continueSearch.click());

    const retrySearch = await screen.findByRole("button", {
      name: "Retry search",
    });
    expect(retrySearch).toBeEnabled();
    expect(screen.getByText("We couldn't finish the search.")).toBeVisible();
    expect(
      screen.getByText("Retry the search to continue from where it stopped."),
    ).toBeVisible();
    expect(screen.queryByText(JARGON)).not.toBeInTheDocument();
    expect(screen.queryByText("transport failure")).not.toBeInTheDocument();
  });
});
