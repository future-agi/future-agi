/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SharedView from "../SharedView";

// A shared link to a trace too large for the full read resolves with the
// trace's spans only and `detail_limited: true`. The page says so in one line
// and renders the spans as usual, with no evaluations, rollups or graph.

const LIMITED_LINE =
  "This trace is large, so evaluations and annotations are not included on this link.";
const TRACE_ID = "4b1f6c2e-9d3a-4e57-8a10-2c6d9e8f7a31";
const STARTED_MS = Date.UTC(2026, 0, 15, 9, 0, 0);

const mocks = vi.hoisted(() => ({ useResolveSharedLink: vi.fn() }));

function countSpans(entries) {
  return (entries || []).reduce(
    (total, entry) => total + 1 + countSpans(entry.children),
    0,
  );
}

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ token: "share-token" }),
}));
vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  Helmet: ({ children }) => <>{children}</>,
}));
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useResolveSharedLink: mocks.useResolveSharedLink,
}));
vi.mock("src/components/traceDetail/TraceTreeV2", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ spans }) => (
    <div data-testid="trace-tree" data-span-count={countSpans(spans)}>
      {(spans || []).map((entry) => entry.observation_span.name).join("|")}
    </div>
  ),
}));
vi.mock(
  "src/components/traceDetail/SpanDetailPane",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
// A trace page never renders the issue, voice or simulation views; full stubs
// keep their import trees out of this file.
vi.mock("../SharedErrorIssueView", () => ({ default: () => null }));
vi.mock("../SharedVoiceView", () => ({ default: () => null }));
vi.mock("../SharedSimulationCallView", () => ({ default: () => null }));

function span(id, parentId, offsetMs) {
  return {
    id,
    trace: TRACE_ID,
    parent_span_id: parentId,
    name: parentId ? `step ${id}` : "agent run",
    observation_type: parentId ? "tool" : "agent",
    start_time: new Date(STARTED_MS + offsetMs).toISOString(),
    end_time: new Date(STARTED_MS + offsetMs + 1000).toISOString(),
    latency_ms: 1000,
    status: "OK",
  };
}

// One root with 1,000 children, as the spans-only read builds the tree.
function largeTraceEntries() {
  const children = Array.from({ length: 1000 }, (_, index) => ({
    observation_span: span(`child-${index}`, "root", index + 1),
    children: [],
  }));
  return [{ observation_span: span("root", null, 0), children }];
}

function resolved(data) {
  return {
    data: {
      resource_type: "trace",
      resource_id: TRACE_ID,
      access_type: "public",
      data,
    },
    isLoading: false,
    isError: false,
    error: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SharedView trace too large for the full read", () => {
  it("shows one line saying evaluations and annotations are left out, and renders every span", () => {
    mocks.useResolveSharedLink.mockReturnValue(
      resolved({
        trace: {
          id: TRACE_ID,
          name: "agent run",
          project_id: "project-1",
          created_at: "2026-01-15T09:00:00+00:00",
        },
        observation_spans: largeTraceEntries(),
        summary: { total_spans: 1001 },
        detail_limited: true,
        detail_limited_reason: "span_limit_exceeded",
      }),
    );

    render(<SharedView />);

    expect(screen.getAllByText(LIMITED_LINE)).toHaveLength(1);
    const tree = screen.getByTestId("trace-tree");
    expect(tree).toHaveAttribute("data-span-count", "1001");
    expect(tree).toHaveTextContent("agent run");
    // A span count is the whole summary; the empty pane still renders it.
    expect(screen.getByText("1001 spans")).toBeInTheDocument();
    expect(
      screen.getByText("Select a span to view details"),
    ).toBeInTheDocument();
  });

  it("shows no such line for a trace that came through in full", () => {
    mocks.useResolveSharedLink.mockReturnValue(
      resolved({
        observation_spans: [
          {
            observation_span: span("root", null, 0),
            children: [],
            eval_rollup: { scope: "trace", evals: [] },
          },
        ],
        summary: {
          total_spans: 1,
          total_duration_ms: 1000,
          total_tokens: 12,
          total_cost: 0.002,
        },
        graph: { nodes: [], edges: [] },
      }),
    );

    render(<SharedView />);

    expect(screen.getByTestId("trace-tree")).toHaveAttribute(
      "data-span-count",
      "1",
    );
    expect(screen.queryByText(LIMITED_LINE)).not.toBeInTheDocument();
  });
});
