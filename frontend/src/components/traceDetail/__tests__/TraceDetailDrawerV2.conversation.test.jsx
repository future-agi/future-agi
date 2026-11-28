import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  projectId,
  spanImage,
} from "src/components/attachments/__tests__/fixtures";

const recording = spanImage(0, {
  reference: {
    modality: "audio",
    mime_type: "audio/ogg",
    purpose: "conversation_recording",
  },
});
const tree = (attachments) => [
  {
    observation_span: {
      id: "root-1",
      trace: "trace-1",
      name: "job_entrypoint",
      observation_type: "conversation",
      start_time: "2026-11-22T10:00:00Z",
    },
    children: [
      {
        observation_span: {
          id: "session-1",
          trace: "trace-1",
          parent_span_id: "root-1",
          name: "agent_session",
          observation_type: "unknown",
          start_time: "2026-11-22T10:00:00Z",
          attachments,
        },
        children: [],
      },
    ],
  },
];

const mocks = vi.hoisted(() => ({
  spans: [],
  toolbarProps: vi.fn(),
  spanPaneProps: vi.fn(),
  callPanelProps: vi.fn(),
  headerProps: vi.fn(),
  createView: vi.fn(),
  updateView: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
  useCreateSavedView: () => ({ mutate: mocks.createView }),
  useUpdateSavedView: () => ({ mutate: mocks.updateView }),
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({
    isLoading: false,
    data: {
      trace: { id: "trace-1", tags: [] },
      observation_spans: mocks.spans,
    },
  }),
}));
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useSharedIssueTrace: () => ({ isLoading: false, data: undefined }),
}));
vi.mock("../TraceDisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../DrawerToolbar", () => ({
  default: (props) => {
    mocks.toolbarProps(props);
    return null;
  },
}));
vi.mock("../DrawerHeader", () => ({
  default: (props) => {
    mocks.headerProps(props);
    return null;
  },
}));
vi.mock("../SpanDetailPane", () => ({
  default: (props) => {
    mocks.spanPaneProps(props);
    return <div data-testid="span-detail" />;
  },
}));
vi.mock("../TraceLeftPanel", () => ({
  default: () => <div data-testid="trace-tree" />,
}));
vi.mock("../TraceCallPanel", () => ({
  default: (props) => {
    mocks.callPanelProps(props);
    return <div data-testid="voice-left-panel" />;
  },
}));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const renderDrawer = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TraceDetailDrawerV2
        traceId="trace-1"
        open
        onClose={vi.fn()}
        projectId={projectId}
        {...props}
      />
    </QueryClientProvider>,
  );
const toolbar = () => mocks.toolbarProps.mock.lastCall[0];
const tabIds = () => toolbar().tabs.map((tab) => tab.id);

describe("TraceDetailDrawerV2 Conversation view", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    mocks.spans = tree([recording]);
    Object.values(mocks).forEach((m) => m?.mockClear?.());
  });

  it("pins a Conversation tab next to Trace for a call recorded as an attachment", () => {
    renderDrawer();

    expect(tabIds()).toEqual(["trace", "conversation"]);
    expect(toolbar().activeTabId).toBe("trace");
    expect(screen.getByTestId("trace-tree")).toBeInTheDocument();
    expect(screen.queryByTestId("voice-left-panel")).toBeNull();
    // The span pane leaves the call recording to the Conversation view.
    expect(mocks.spanPaneProps.mock.lastCall[0].callRecording).toBe(true);
  });

  it("swaps the trace tree for the Voice Observability panel and keeps span detail on the right", () => {
    renderDrawer();

    act(() => toolbar().onTabChange("conversation"));

    expect(toolbar().activeTabId).toBe("conversation");
    expect(toolbar().hideFilter).toBe(true);
    expect(toolbar().hideDisplay).toBe(true);
    expect(
      screen.getByRole("region", { name: "Conversation" }),
    ).toContainElement(screen.getByTestId("voice-left-panel"));
    expect(mocks.callPanelProps.mock.lastCall[0]).toEqual({
      traceId: "trace-1",
      projectId,
      spans: mocks.spans,
      onSelectSpan: expect.any(Function),
    });
    expect(screen.queryByTestId("trace-tree")).toBeNull();
    expect(screen.getByTestId("span-detail")).toBeInTheDocument();

    act(() => toolbar().onTabChange("trace"));

    expect(toolbar().activeTabId).toBe("trace");
    expect(screen.getByTestId("trace-tree")).toBeInTheDocument();
    expect(screen.queryByTestId("voice-left-panel")).toBeNull();
    // The Conversation view is not a saved view and writes none.
    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("hands the Conversation panel the whole tree and opens a selected span on the right", () => {
    renderDrawer();
    expect(mocks.headerProps.mock.lastCall[0].navigationShortcuts).toBe(true);

    act(() => toolbar().onTabChange("conversation"));

    const panel = mocks.callPanelProps.mock.lastCall[0];
    expect(panel.spans).toBe(mocks.spans);
    // J/K belong to the transcript while it is shown; Esc still closes.
    expect(mocks.headerProps.mock.lastCall[0].navigationShortcuts).toBe(false);

    act(() => panel.onSelectSpan("session-1"));
    expect(mocks.spanPaneProps.mock.lastCall[0].entry.observation_span.id).toBe(
      "session-1",
    );
    // Selecting the same span again keeps its detail open (no toggle).
    act(() => mocks.callPanelProps.mock.lastCall[0].onSelectSpan("session-1"));
    expect(screen.getByTestId("span-detail")).toBeInTheDocument();
    expect(mocks.spanPaneProps.mock.lastCall[0].entry.observation_span.id).toBe(
      "session-1",
    );

    act(() => toolbar().onTabChange("trace"));
    expect(mocks.headerProps.mock.lastCall[0].navigationShortcuts).toBe(true);
  });

  it("opens a span the saved type filter hides and times it from the whole call", () => {
    // A saved default that matches no span of this call.
    const key = `trace-view-default-${projectId}`;
    localStorage.setItem(key, JSON.stringify({ spanTypeFilter: ["llm"] }));
    try {
      renderDrawer();
      act(() => toolbar().onTabChange("conversation"));

      const panel = mocks.callPanelProps.mock.lastCall[0];
      expect(panel.spans).toBe(mocks.spans);
      act(() => panel.onSelectSpan("session-1"));

      expect(screen.getByTestId("span-detail")).toBeInTheDocument();
      const pane = mocks.spanPaneProps.mock.lastCall[0];
      expect(pane.entry.observation_span.id).toBe("session-1");
      expect(pane.allSpans).toBe(mocks.spans);
      expect(pane.traceStartTime).toBe("2026-11-22T10:00:00Z");
    } finally {
      localStorage.removeItem(key);
    }
  });

  it("adds nothing for a call without an attached recording (Vapi, Retell)", () => {
    mocks.spans = tree([]);
    renderDrawer();

    expect(tabIds()).toEqual(["trace"]);
    expect(mocks.spanPaneProps.mock.lastCall[0].callRecording).toBe(false);
  });

  it.each([
    ["read-only", { readOnly: true }],
    ["shared", { sharedToken: "share-token" }],
    ["unscoped", { projectId: "project-1" }],
  ])("adds nothing to a %s drawer", (_, props) => {
    renderDrawer(props);

    expect(tabIds()).toEqual(["trace"]);
  });
});
