import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "src/utils/test-utils";

// The trace drawer keeps its display in this browser under this key; it is
// the only default the drawer reads back on mount.
const STORAGE_KEY = "trace-view-default-project-1";

const mocks = vi.hoisted(() => ({
  getSavedViews: vi.fn(),
  getTraceDetail: vi.fn(),
  toolbarProps: vi.fn(),
  displayProps: vi.fn(),
  createView: vi.fn(),
  updateView: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: (...args) => mocks.getSavedViews(...args),
  useCreateSavedView: () => ({ mutate: mocks.createView }),
  useUpdateSavedView: () => ({ mutate: mocks.updateView }),
}));

vi.mock("../TraceDisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    mocks.displayProps(props);
    return null;
  },
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("../DrawerToolbar", () => ({
  default: (props) => {
    mocks.toolbarProps(props);
    return null;
  },
}));

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

// The Observe traces list's own "Default View" (list-shaped config).
const LIST_DEFAULT_VIEW = {
  id: "view-list-default",
  name: "Default View",
  tab_type: "traces",
  visibility: "personal",
  created_by: { id: "user-1" },
  config: { columns: [{ id: "latency", hide: false }], filters: [] },
};

const NO_VIEWS = { data: { custom_views: [] } };
const WITH_LIST_DEFAULT = { data: { custom_views: [LIST_DEFAULT_VIEW] } };

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

const lastDisplay = () => mocks.displayProps.mock.lastCall[0];

describe("TraceDetailDrawerV2 views stay in the drawer", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    mocks.getSavedViews.mockReset();
    mocks.getSavedViews.mockReturnValue(NO_VIEWS);
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
    mocks.toolbarProps.mockClear();
    mocks.displayProps.mockClear();
    mocks.createView.mockClear();
    mocks.updateView.mockClear();
  });

  it("shows the shared default only disabled, so it cannot overwrite the list's own Default View", () => {
    mocks.getSavedViews.mockReturnValue(WITH_LIST_DEFAULT);
    renderDrawer();

    const display = lastDisplay();
    // Shown (disabled, "Not available in this release"), never wired.
    expect(display.hideSetDefault).toBe(false);
    expect(display.onSetDefaultView).toBeUndefined();
    expect(mocks.updateView).not.toHaveBeenCalled();
    expect(mocks.createView).not.toHaveBeenCalled();
  });

  it("shows the shared default only disabled, so it cannot create a project-visible traces view", () => {
    renderDrawer();

    const display = lastDisplay();
    // Shown (disabled, "Not available in this release"), never wired.
    expect(display.hideSetDefault).toBe(false);
    expect(display.onSetDefaultView).toBeUndefined();
    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("keeps display changes as the default the drawer reads back", () => {
    renderDrawer();

    act(() => lastDisplay().onToggleAgentGraph());

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY))).toMatchObject({
      viewMode: "tree",
      showAgentGraph: false,
      filters: [],
    });
    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("offers no '+' create view, which would make a hidden list view", () => {
    renderDrawer();

    const toolbar = mocks.toolbarProps.mock.lastCall[0];
    expect(toolbar.onCreateTab).toBeUndefined();
    expect(toolbar.onCreateImagineTab).toEqual(expect.any(Function));
    expect(toolbar.readOnly).toBe(false);
  });
});
