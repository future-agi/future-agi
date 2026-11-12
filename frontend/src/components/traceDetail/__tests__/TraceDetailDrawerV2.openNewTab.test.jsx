import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  headerProps: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isEE: false }),
}));

// The header's "Open in new tab" button calls onOpenNewTab.
vi.mock("../DrawerHeader", () => ({
  default: (props) => {
    mocks.headerProps(props);
    return null;
  },
}));
vi.mock("../SpanDetailPane", () => ({ default: () => null }));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

// A root span with one nested LLM span.
const TRACE = {
  isLoading: false,
  data: {
    trace: { id: "trace-1", tags: [] },
    observation_spans: [
      {
        observation_span: {
          id: "span-root",
          trace: "trace-1",
          name: "bookstore_agent",
          observation_type: "agent",
          start_time: "2026-10-14T11:08:13Z",
        },
        children: [
          {
            observation_span: {
              id: "span-llm",
              trace: "trace-1",
              parent_span_id: "span-root",
              name: "answer_question",
              observation_type: "llm",
              start_time: "2026-10-14T11:08:14Z",
            },
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

const openInNewTab = () => {
  const { onOpenNewTab } = mocks.headerProps.mock.lastCall[0];
  act(() => {
    onOpenNewTab();
  });
};

describe("TraceDetailDrawerV2 Open in new tab", () => {
  let openSpy;

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
    mocks.headerProps.mockClear();
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    openSpy.mockRestore();
  });

  it("opens the full page on the selected span", () => {
    renderDrawer({ initialSpanId: "span-llm" });

    openInNewTab();

    expect(openSpy).toHaveBeenCalledExactlyOnceWith(
      "/dashboard/observe/project-1/trace/trace-1?span_id=span-llm",
      "_blank",
    );
  });

  it("opens the plain trace link when the root span is selected", () => {
    renderDrawer();

    openInNewTab();

    expect(openSpy).toHaveBeenCalledExactlyOnceWith(
      "/dashboard/observe/project-1/trace/trace-1",
      "_blank",
    );
  });
});
