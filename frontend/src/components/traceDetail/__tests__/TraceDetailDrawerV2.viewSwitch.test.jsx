import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

// G6: the trace drawer's Timeline layout was reachable only through
// Display > Timeline View. The toolbar now has a Tree | Timeline switch
// beside Display. The Display panel keeps its own choice, and both set the
// same view.

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/api/project/trace-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("src/hooks/useDeploymentMode", async (importOriginal) => ({
  ...(await importOriginal()),
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isEE: false }),
}));

// The real toolbar, Display panel, span tree and timeline render. The header
// and the span detail pane are not part of this change.
vi.mock("../DrawerHeader", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../SpanDetailPane", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

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
          latency_ms: 1200,
        },
        children: [],
      },
    ],
  },
};

// The drawer reads and saves its display under this key.
const STORAGE_KEY = "trace-view-default-project-1";
const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY));

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

const layoutSwitch = () => screen.getByRole("group", { name: "Trace layout" });
const layoutButton = (name) =>
  within(layoutSwitch()).getByRole("button", { name });

// The toolbar's Display button. Matching its text avoids depending on the
// icon's accessible name.
const displayButton = () => screen.getByText("Display").closest("button");

// While the Display panel (a popover) is open, the drawer behind it is
// aria-hidden, so the panel's own tabs are found by their text.
const openDisplayPanel = () => {
  fireEvent.click(displayButton());
  expect(screen.getByText("Span metrics")).toBeInTheDocument();
};
const panelTab = (label) => screen.getByText(label).closest('[role="tab"]');

const expectTreeLayout = () => {
  expect(
    screen.getByRole("region", { name: "Trace Tree" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Trace Timeline")).not.toBeInTheDocument();
};

const expectTimelineLayout = () => {
  expect(
    screen.getByRole("tree", { name: "Span timeline" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Trace Timeline")).toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "Trace Tree" }),
  ).not.toBeInTheDocument();
};

describe("TraceDetailDrawerV2 Tree and Timeline switch", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    // An earlier visit's display with no view chosen, and the agent graph
    // off, so only the span tree or the timeline draws.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ showAgentGraph: false, filters: [] }),
    );
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
  });

  it("shows Tree and Timeline beside Display, with Tree pressed", () => {
    renderDrawer();

    const group = layoutSwitch();
    const buttons = within(group).getAllByRole("button");
    const labels = buttons.map((button) => button.textContent);
    expect(labels).toEqual(["Tree", "Timeline"]);
    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "false");

    // In the toolbar's row of view controls, right after Display.
    expect(group.previousElementSibling).toBe(displayButton());

    expectTreeLayout();
  });

  it("opens the timeline from the toolbar and goes back to the tree", () => {
    renderDrawer();

    fireEvent.click(layoutButton("Timeline"));

    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "false");
    expectTimelineLayout();
    // Saved like a Display choice, so the next trace opens the same way.
    expect(stored()).toMatchObject({ viewMode: "timeline" });

    // Clicking the pressed option keeps it pressed.
    fireEvent.click(layoutButton("Timeline"));

    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "true");
    expectTimelineLayout();

    fireEvent.click(layoutButton("Tree"));

    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "false");
    expectTreeLayout();
    expect(stored()).toMatchObject({ viewMode: "tree" });
  });

  it("keeps the Display panel's Trace View and Timeline View, in step with the switch", () => {
    renderDrawer();
    const tree = layoutButton("Tree");
    const timeline = layoutButton("Timeline");

    fireEvent.click(timeline);
    openDisplayPanel();

    expect(panelTab("Timeline View")).toHaveAttribute("aria-selected", "true");
    expect(panelTab("Trace View")).toHaveAttribute("aria-selected", "false");

    fireEvent.click(panelTab("Trace View"));

    expect(tree).toHaveAttribute("aria-pressed", "true");
    expect(timeline).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Trace Tree")).toBeInTheDocument();
    expect(screen.queryByText("Trace Timeline")).not.toBeInTheDocument();

    fireEvent.click(panelTab("Timeline View"));

    expect(timeline).toHaveAttribute("aria-pressed", "true");
    expect(tree).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Trace Timeline")).toBeInTheDocument();
  });

  it("offers the switch in a read-only drawer too", () => {
    renderDrawer({ readOnly: true });

    fireEvent.click(layoutButton("Timeline"));

    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "true");
    expectTimelineLayout();
  });

  // At a 1024 px window the drawer is about 613 px wide. The switch keeps
  // its full width and one line; the custom-tab strip, which scrolls, gives
  // way instead.
  it("never shrinks or wraps the switch in a narrow toolbar", () => {
    renderDrawer();

    const style = window.getComputedStyle(layoutSwitch());

    expect(style.flexShrink).toBe("0");
    expect(style.whiteSpace).toBe("nowrap");
  });
});
