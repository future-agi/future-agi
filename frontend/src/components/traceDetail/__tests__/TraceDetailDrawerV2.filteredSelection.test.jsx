import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

// The drawer restores its span-type filter and span filters from this key.
const STORAGE_KEY = "trace-view-default-project-1";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  spanPaneProps: vi.fn(),
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

vi.mock("../SpanDetailPane", () => ({
  default: (props) => {
    mocks.spanPaneProps(props);
    return null;
  },
}));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const span = (id, type, parent = null) => ({
  id,
  trace: "trace-1",
  name: `${type}_span`,
  observation_type: type,
  parent_span_id: parent,
  start_time: "2026-10-14T11:08:13Z",
});

// agent -> [llm, tool]: an LLM-only filter keeps the agent (it has a matching
// child) and the LLM span, and drops the tool span.
const TRACE = {
  isLoading: false,
  data: {
    trace: { id: "trace-1", tags: [] },
    observation_spans: [
      {
        observation_span: span("span-agent", "agent"),
        children: [
          {
            observation_span: span("span-llm", "llm", "span-agent"),
            children: [],
          },
          {
            observation_span: span("span-tool", "tool", "span-agent"),
            children: [],
          },
        ],
      },
    ],
  },
};

const renderDrawer = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TraceDetailDrawerV2
        traceId="trace-1"
        open
        onClose={vi.fn()}
        projectId="project-1"
        {...props}
      />
    </QueryClientProvider>,
  );

const shownSpanId = () =>
  mocks.spanPaneProps.mock.lastCall?.[0]?.entry?.observation_span?.id;

// The drawer default a user left behind; the agent graph is off so only the
// span tree and the detail pane render.
const saveDrawerDefault = (config) =>
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ showAgentGraph: false, filters: [], ...config }),
  );

describe("TraceDetailDrawerV2 opens the clicked span through saved filters", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
    mocks.spanPaneProps.mockClear();
  });

  it("shows a span the saved span-type filter hides", () => {
    saveDrawerDefault({ spanTypeFilter: ["llm"] });

    renderDrawer({ initialSpanId: "span-tool" });

    expect(shownSpanId()).toBe("span-tool");
    expect(
      screen.queryByText("Select a span to view details"),
    ).not.toBeInTheDocument();
  });

  it("shows a span the saved span filter hides", () => {
    saveDrawerDefault({
      spanTypeFilter: null,
      filters: [{ field: "nodeType", operator: "is", value: ["llm"] }],
    });

    renderDrawer({ initialSpanId: "span-tool" });

    expect(shownSpanId()).toBe("span-tool");
    expect(
      screen.queryByText("Select a span to view details"),
    ).not.toBeInTheDocument();
  });

  it("still shows a span the filter keeps", () => {
    saveDrawerDefault({ spanTypeFilter: ["llm"] });

    renderDrawer({ initialSpanId: "span-llm" });

    expect(shownSpanId()).toBe("span-llm");
  });
});
