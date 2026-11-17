import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";

// The drawer restores its span-type filter and span filters from this key.
const STORAGE_KEY = "trace-view-default-project-1";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("../DrawerToolbar", () => ({ default: () => null }));
vi.mock("../DrawerHeader", () => ({ default: () => null }));
vi.mock("../TraceDisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../SpanDetailPane", () => ({ default: () => null }));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const chainSpan = (id, name, parent = null) => ({
  id,
  trace: "trace-1",
  name,
  observation_type: "chain",
  parent_span_id: parent,
  start_time: "2026-10-14T11:08:13Z",
  latency_ms: 1200,
});

// A first trace made only of chain spans.
const CHAIN_TRACE = {
  isLoading: false,
  data: {
    trace: { id: "trace-1", tags: [] },
    observation_spans: [
      {
        observation_span: chainSpan("span-root", "plan_trip"),
        children: [
          {
            observation_span: chainSpan("span-child", "pick_city", "span-root"),
            children: [],
          },
        ],
      },
    ],
  },
};

const EMPTY_TRACE = {
  isLoading: false,
  data: { trace: { id: "trace-1", tags: [] }, observation_spans: [] },
};

const LLM_ONLY = { field: "nodeType", operator: "is", value: ["llm"] };

const renderDrawer = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TraceDetailDrawerV2
        traceId="trace-1"
        open
        onClose={vi.fn()}
        projectId="project-1"
      />
    </QueryClientProvider>,
  );

const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY));

// The drawer default an earlier visit left behind. The agent graph is off so
// only the span tree (or timeline) and the detail pane render.
const saveDrawerDefault = (config) =>
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ showAgentGraph: false, filters: [], ...config }),
  );

const showAllSpans = () =>
  fireEvent.click(screen.getByRole("button", { name: "Show all spans" }));

describe("TraceDetailDrawerV2 says when its filters hide every span", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(CHAIN_TRACE);
  });

  it("names the saved Span Type setting in the tree and shows the spans again", () => {
    saveDrawerDefault({ viewMode: "tree", spanTypeFilter: ["llm"] });
    renderDrawer();

    expect(screen.getByText("All spans are hidden")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The Span Type setting in Display hides every span in this trace.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No spans in this trace"),
    ).not.toBeInTheDocument();

    showAllSpans();

    expect(
      screen.getByRole("region", { name: "Trace Tree" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("pick_city").length).toBeGreaterThan(0);
    expect(screen.queryByText("All spans are hidden")).not.toBeInTheDocument();
    // Saved, so the next trace in this project opens with every span shown.
    expect(stored()).toMatchObject({
      viewMode: "tree",
      spanTypeFilter: null,
      filters: [],
    });
  });

  it("names the saved span filters in the timeline and shows the spans again", () => {
    saveDrawerDefault({ viewMode: "timeline", filters: [LLM_ONLY] });
    renderDrawer();

    expect(screen.getByText("All spans are hidden")).toBeInTheDocument();
    expect(
      screen.getByText("The span filters hide every span in this trace."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No spans in this trace"),
    ).not.toBeInTheDocument();

    showAllSpans();

    expect(screen.getByText("Trace Timeline")).toBeInTheDocument();
    expect(screen.getAllByText("pick_city").length).toBeGreaterThan(0);
    expect(screen.queryByText("All spans are hidden")).not.toBeInTheDocument();
    expect(stored()).toMatchObject({
      viewMode: "timeline",
      spanTypeFilter: null,
      filters: [],
    });
  });

  it("names both when the Span Type setting and the span filters are set", () => {
    saveDrawerDefault({ spanTypeFilter: ["llm"], filters: [LLM_ONLY] });
    renderDrawer();

    expect(
      screen.getByText(
        "The Span Type setting in Display and the span filters hide every span in this trace.",
      ),
    ).toBeInTheDocument();

    showAllSpans();

    expect(
      screen.getByRole("region", { name: "Trace Tree" }),
    ).toBeInTheDocument();
    expect(stored()).toMatchObject({ spanTypeFilter: null, filters: [] });
  });

  it("still calls a trace with no spans empty", () => {
    saveDrawerDefault({ spanTypeFilter: ["llm"] });
    mocks.getTraceDetail.mockReturnValue(EMPTY_TRACE);
    renderDrawer();

    expect(screen.getByText("No spans in this trace")).toBeInTheDocument();
    expect(screen.queryByText("All spans are hidden")).not.toBeInTheDocument();
  });

  it("shows the tree when the filters keep a span", () => {
    saveDrawerDefault({ spanTypeFilter: ["chain"] });
    renderDrawer();

    expect(
      screen.getByRole("region", { name: "Trace Tree" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("All spans are hidden")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show all spans" }),
    ).not.toBeInTheDocument();
  });
});
