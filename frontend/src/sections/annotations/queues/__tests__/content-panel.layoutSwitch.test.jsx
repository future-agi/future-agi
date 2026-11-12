import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// The annotation queue shows a trace inline, with its own toolbar, Display
// panel and layout. The Timeline layout was reachable only through
// Display > Timeline View. The toolbar now has the trace drawer's
// Tree | Timeline switch right after Display. The Display panel keeps its own
// choice, and both set the same view.

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
}));

vi.mock("src/api/project/trace-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/hooks/useDeploymentMode", async (importOriginal) => ({
  ...(await importOriginal()),
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isEE: false }),
}));

// The real toolbar and Display panel render. The two layouts are stubs that
// say which one is on; the span detail pane is not part of this change.
vi.mock(
  "src/components/traceDetail/TraceLeftPanel",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => <div data-testid="tree-layout" />,
  }),
);
vi.mock(
  "src/components/traceDetail/SpanTreeTimeline",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => <div data-testid="timeline-layout" />,
  }),
);
vi.mock(
  "src/components/traceDetail/SpanDetailPane",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

import ContentPanel from "../annotate/content-panel";

// The Trace tab, Display and View session glyphs, and the Display panel's
// Trace View / Timeline View and Span Type glyphs.
for (const name of [
  "solar:link-linear",
  "solar:tuning-2-linear",
  "solar:chat-round-line-linear",
  "solar:graph-linear",
  "solar:clock-circle-linear",
  "solar:alt-arrow-down-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const traceDetail = ({ session = null } = {}) => ({
  isLoading: false,
  data: {
    trace: { id: "trace-1", project: "project-1", session, tags: [] },
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
});

const renderPanel = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ContentPanel
        item={{
          source_type: "trace",
          source_content: { trace_id: "trace-1", project_id: "project-1" },
        }}
      />
    </QueryClientProvider>,
  );

const layoutSwitch = () => screen.getByRole("group", { name: "Trace layout" });
const layoutButton = (name) =>
  within(layoutSwitch()).getByRole("button", { name });

// The toolbar's Display and View session buttons, found by their text.
const displayButton = () => screen.getByText("Display").closest("button");
const viewSessionButton = () =>
  screen.getByText("View session").closest("button");

// While the Display panel (a popover) is open, the panel behind it is
// aria-hidden, so the panel's own tabs are found by their text.
const openDisplayPanel = () => {
  fireEvent.click(displayButton());
  expect(screen.getByText("Span metrics")).toBeInTheDocument();
};
const panelTab = (label) => screen.getByText(label).closest('[role="tab"]');

const expectTreeLayout = () => {
  expect(screen.getByTestId("tree-layout")).toBeInTheDocument();
  expect(screen.queryByTestId("timeline-layout")).not.toBeInTheDocument();
};

const expectTimelineLayout = () => {
  expect(screen.getByTestId("timeline-layout")).toBeInTheDocument();
  expect(screen.queryByTestId("tree-layout")).not.toBeInTheDocument();
};

describe("Annotation queue inline trace: Tree and Timeline switch", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(traceDetail());
  });

  it("shows Tree and Timeline right after Display, with Tree pressed", () => {
    renderPanel();

    const group = layoutSwitch();
    const buttons = within(group).getAllByRole("button");
    const labels = buttons.map((button) => button.textContent);
    expect(labels).toEqual(["Tree", "Timeline"]);
    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "false");

    // In the toolbar's row of view controls, first after Display.
    expect(displayButton().nextElementSibling).toBe(group.parentElement);
    expect(group.parentElement.firstElementChild).toBe(group);

    expectTreeLayout();
  });

  it("opens the timeline from the toolbar and goes back to the tree", () => {
    renderPanel();

    fireEvent.click(layoutButton("Timeline"));

    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "false");
    expectTimelineLayout();

    // Clicking the pressed option keeps it pressed.
    fireEvent.click(layoutButton("Timeline"));

    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "true");
    expectTimelineLayout();

    fireEvent.click(layoutButton("Tree"));

    expect(layoutButton("Tree")).toHaveAttribute("aria-pressed", "true");
    expect(layoutButton("Timeline")).toHaveAttribute("aria-pressed", "false");
    expectTreeLayout();
  });

  it("keeps the Display panel's Trace View and Timeline View, in step with the switch", () => {
    renderPanel();
    const tree = layoutButton("Tree");
    const timeline = layoutButton("Timeline");

    fireEvent.click(timeline);
    openDisplayPanel();

    expect(panelTab("Timeline View")).toHaveAttribute("aria-selected", "true");
    expect(panelTab("Trace View")).toHaveAttribute("aria-selected", "false");

    fireEvent.click(panelTab("Trace View"));

    expect(tree).toHaveAttribute("aria-pressed", "true");
    expect(timeline).toHaveAttribute("aria-pressed", "false");
    expectTreeLayout();

    fireEvent.click(panelTab("Timeline View"));

    expect(timeline).toHaveAttribute("aria-pressed", "true");
    expect(tree).toHaveAttribute("aria-pressed", "false");
    expectTimelineLayout();
  });

  it("keeps View session in the toolbar, right after the switch", () => {
    mocks.getTraceDetail.mockReturnValue(
      traceDetail({ session: "session-abc" }),
    );
    renderPanel();

    const group = layoutSwitch();
    expect(displayButton().nextElementSibling).toBe(group.parentElement);
    expect(group.nextElementSibling).toBe(viewSessionButton());
  });

  // The switch keeps its full width and one line in a narrow panel; the
  // custom-tab strip, which scrolls, gives way instead.
  it("never shrinks or wraps the switch in a narrow toolbar", () => {
    renderPanel();

    const style = window.getComputedStyle(layoutSwitch());

    expect(style.flexShrink).toBe("0");
    expect(style.whiteSpace).toBe("nowrap");
  });
});
