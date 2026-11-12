import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  createView: vi.fn(),
  updateView: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useCreateSavedView: () => ({ mutate: mocks.createView }),
  useUpdateSavedView: () => ({ mutate: mocks.updateView }),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isEE: false }),
}));

// The real toolbar and the real Display panel render, so the test opens the
// panel through the Display button a user clicks.
vi.mock("../DrawerHeader", () => ({ default: () => null }));
vi.mock("../SpanDetailPane", () => ({ default: () => null }));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const TRACE = {
  isLoading: false,
  data: {
    trace: { id: "trace-1", tags: [] },
    observation_spans: [
      {
        observation_span: {
          id: "span-1",
          trace: "trace-1",
          name: "bookstore_agent",
          observation_type: "agent",
          start_time: "2026-10-14T11:08:13Z",
        },
        children: [],
      },
    ],
  },
};

// One LLM span with a cost and a token count, so the timeline row can show
// either metric.
const TRACE_WITH_METRICS = {
  isLoading: false,
  data: {
    trace: { id: "trace-1", tags: [] },
    observation_spans: [
      {
        observation_span: {
          id: "span-1",
          trace: "trace-1",
          name: "answer_question",
          observation_type: "llm",
          start_time: "2026-10-14T11:08:13Z",
          latency_ms: 1200,
          total_tokens: 321,
          cost: 1.25,
        },
        children: [],
      },
    ],
  },
};

// The drawer opens with the display last chosen in this project.
const STORAGE_KEY = "trace-view-default-project-1";
const openInTimelineView = () =>
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      viewMode: "timeline",
      spanTypeFilter: null,
      visibleMetrics: { latency: true, tokens: true, cost: false },
      showAgentGraph: true,
      filters: [],
    }),
  );

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

// Clicks the toolbar's "Display" label; the click reaches its button, which
// opens the panel. Matching the text avoids depending on the icon's
// accessible name.
const openDisplayPanel = () => {
  fireEvent.click(screen.getByText("Display"));
  // The panel is open: its own section heading is on screen.
  expect(screen.getByText("Span metrics")).toBeInTheDocument();
  return screen.getByText("Span metrics").closest(".MuiPaper-root");
};

// The switch on the panel row with this label.
const metricSwitch = (panel, label) =>
  within(within(panel).getByText(label).closest("div")).getByRole("checkbox");

describe("TraceDetailDrawerV2 Display panel", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
    mocks.createView.mockClear();
    mocks.updateView.mockClear();
  });

  // The drawer cannot share a default, so the action stays visible but
  // disabled, says why, and never reaches the saved-views API.
  it("shows 'Set default for everyone' disabled with the reason, which the drawer cannot share", () => {
    renderDrawer();

    openDisplayPanel();

    const setDefault = screen.getByRole("button", {
      name: "Set default for everyone",
    });
    expect(setDefault).toHaveAttribute("aria-disabled", "true");
    expect(setDefault).toHaveAttribute(
      "title",
      "Not available in this release",
    );

    fireEvent.click(setDefault);
    fireEvent.keyDown(setDefault, { key: "Enter" });

    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
    // The panel stays open: the click did nothing.
    expect(screen.getByText("Span metrics")).toBeInTheDocument();
  });

  it("shows no 'Set default for everyone' when read-only", () => {
    renderDrawer({ readOnly: true });

    openDisplayPanel();

    expect(screen.queryByText(/set default/i)).not.toBeInTheDocument();
  });

  it("keeps Evals, Annotations and Events as disabled, off switches with the reason", () => {
    renderDrawer();

    const panel = openDisplayPanel();

    for (const label of ["Latency", "Tokens", "Cost"]) {
      expect(within(panel).getByText(label)).toBeInTheDocument();
      expect(metricSwitch(panel, label)).not.toHaveAttribute("aria-disabled");
    }
    for (const label of ["Evals", "Annotations", "Events"]) {
      const row = within(panel).getByText(label).closest("div");
      expect(row).toHaveAttribute("title", "Not available in this release");
      const toggle = metricSwitch(panel, label);
      expect(toggle).toHaveAttribute("aria-disabled", "true");
      expect(toggle).not.toBeChecked();

      fireEvent.click(toggle);

      expect(metricSwitch(panel, label)).not.toBeChecked();
    }
    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
    // Trace View draws the agent graph, so its switch stays.
    expect(within(panel).getByText("Show agent graph")).toBeInTheDocument();
  });

  it("has no agent graph switch in Timeline View, which draws no graph", () => {
    openInTimelineView();
    renderDrawer();

    const panel = openDisplayPanel();

    expect(within(panel).queryByText("Agent graph")).not.toBeInTheDocument();
    expect(
      within(panel).queryByText("Show agent graph"),
    ).not.toBeInTheDocument();
  });

  it("draws the Cost and Tokens choices on the timeline in Timeline View", () => {
    mocks.getTraceDetail.mockReturnValue(TRACE_WITH_METRICS);
    openInTimelineView();
    renderDrawer();

    // Tokens on and Cost off, as chosen.
    expect(screen.getByText("321")).toBeInTheDocument();
    expect(screen.queryByText("$1.25")).not.toBeInTheDocument();

    const panel = openDisplayPanel();
    fireEvent.click(metricSwitch(panel, "Cost"));
    fireEvent.click(metricSwitch(panel, "Tokens"));

    expect(screen.getByText("$1.25")).toBeInTheDocument();
    expect(screen.queryByText("321")).not.toBeInTheDocument();
  });
});
