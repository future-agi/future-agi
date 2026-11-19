import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import TraceDetailPanel from "../TraceDetailPanel";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const state = vi.hoisted(() => ({ data: undefined }));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: state.data, isLoading: false }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
}));
vi.mock("../DrawerHeader", () => ({ default: () => null }));
vi.mock("../DrawerToolbar", () => ({ default: () => null }));
vi.mock("../SpanDetailPane", () => ({ default: () => null }));
vi.mock("../SpanTreeTimeline", () => ({ default: () => null }));

const coverage = (requestCount) => ({
  scope: "observed_requests",
  source_summary_present: true,
  request_count: requestCount,
  coverage_unknown: true,
});
const snapshot = (requestCount = 0) => ({
  trace: { id: "trace-1", tags: [] },
  capture_coverage: coverage(requestCount),
  observation_spans: [{
    observation_span: {
      id: "0000000000000001", name: "invoke_agent", observation_type: "agent",
      start_time: "2026-10-20T12:00:00Z", total_tokens: 0, cost: 0,
      span_attributes: {
        "fi.usage.contribution": "none",
        "fi.source.summary.gen_ai.usage.input_tokens": 42925,
        "fi.source.summary.gen_ai.usage.output_tokens": 146,
        "fi.source.summary.gen_ai.cost.total": 17,
      },
    },
    children: [],
  }],
  summary: {
    total_spans: requestCount ? 5 : 1, total_duration_ms: 1000,
    total_tokens: requestCount ? 43071 : 0, total_cost: requestCount ? null : 0,
  },
});

function drawer(Component) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <Component open traceId="trace-1" projectId="project-1" onClose={vi.fn()} />
    </QueryClientProvider>
  );
}

describe("Native source-summary capture scope", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    state.data = snapshot();
  });

  it.each([TraceDetailPanel, TraceDetailDrawerV2])(
    "keeps the scope visible for parent-only, late-child and replay snapshots",
    (Component) => {
      const view = render(drawer(Component));
      const note = () => screen.getByRole("note", { name: "Capture coverage" });
      expect(note()).toHaveTextContent("Observed requests only · 0 observed requests");
      expect(note()).toHaveTextContent("Capture coverage unknown");
      expect(note()).toHaveTextContent("Source summaries are not added to request usage");
      expect(screen.queryByText("$17.00")).not.toBeInTheDocument();
      state.data = snapshot(3);
      view.rerender(drawer(Component));
      expect(note()).toHaveTextContent("3 observed requests");
      expect(note()).toHaveTextContent("matching totals do not prove complete capture");
      state.data = snapshot(3);
      view.rerender(drawer(Component));
      expect(note()).toHaveTextContent("Capture coverage unknown");
      expect(note()).toHaveTextContent("3 observed requests");
      view.unmount();
    },
  );

  it("labels the zero as observed rather than complete request usage", () => {
    render(drawer(TraceDetailPanel));
    expect(screen.getByText("0 observed tokens")).toBeVisible();
    expect(screen.getByText("Observed cost: $0.00")).toBeVisible();
  });

  it.each([TraceDetailPanel, TraceDetailDrawerV2])("leaves legacy no-flag presentation unchanged", (Component) => {
    delete state.data.capture_coverage;
    render(drawer(Component));
    expect(screen.queryByRole("note", { name: "Capture coverage" })).not.toBeInTheDocument();
    expect(screen.queryByText(/observed tokens|Observed cost:/)).not.toBeInTheDocument();
  });
});
