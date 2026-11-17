import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "src/utils/test-utils";

// The trace drawer keeps its Trace-tab display and span filters under this
// key and reads them back when it opens.
const STORAGE_KEY = "trace-view-default-project-1";

const mocks = vi.hoisted(() => ({
  getSavedViews: vi.fn(),
  getTraceDetail: vi.fn(),
  toolbarProps: vi.fn(),
  displayProps: vi.fn(),
  filterPanelProps: vi.fn(),
  imagineProps: vi.fn(),
  updateView: vi.fn(),
  deleteView: vi.fn((id, options) => options?.onSuccess?.()),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: (...args) => mocks.getSavedViews(...args),
  useUpdateSavedView: () => ({ mutate: mocks.updateView }),
  useDeleteSavedView: () => ({ mutate: mocks.deleteView }),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));

vi.mock("../TraceDisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    mocks.displayProps(props);
    return null;
  },
}));

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    mocks.filterPanelProps(props);
    return null;
  },
}));

vi.mock("src/components/imagine/ImagineTab", () => ({
  default: (props) => {
    mocks.imagineProps(props);
    return null;
  },
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

// A saved Imagine tab, shaped as ImagineTab saves it: no display settings and
// no span filters of its own.
const IMAGINE_VIEW = {
  id: "imagine-1",
  name: "Cost breakdown",
  tab_type: "imagine",
  visibility: "personal",
  config: { widgets: [], conversation_id: "conv-1" },
};

const NO_VIEWS = { data: { custom_views: [] } };
const WITH_IMAGINE = { data: { custom_views: [IMAGINE_VIEW] } };

// Both filters keep the trace's only span, so the tree never empties.
const MOUNT_FILTER = {
  field: "traceName",
  operator: "contains",
  value: ["bookstore"],
};
const LATEST_FILTER = { field: "nodeType", operator: "is", value: ["agent"] };

// What an earlier visit saved, and so what the drawer reads when it opens.
const MOUNT_DEFAULT = {
  viewMode: "tree",
  spanTypeFilter: null,
  visibleMetrics: {
    latency: true,
    tokens: true,
    cost: false,
    evals: false,
    annotations: false,
    events: false,
  },
  showAgentGraph: true,
  filters: [MOUNT_FILTER],
};

// What the user chooses after the drawer opened (see chooseLatest).
const LATEST = {
  viewMode: "tree",
  spanTypeFilter: ["agent"],
  visibleMetrics: { ...MOUNT_DEFAULT.visibleMetrics, cost: true },
  showAgentGraph: false,
  filters: [LATEST_FILTER],
};

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

const lastToolbar = () => mocks.toolbarProps.mock.lastCall[0];
const lastDisplay = () => mocks.displayProps.mock.lastCall[0];
const lastFilterPanel = () => mocks.filterPanelProps.mock.lastCall[0];
const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY));

const chooseLatest = () => {
  act(() => {
    lastDisplay().onToggleAgentGraph();
  });
  act(() => {
    lastDisplay().onSpanTypeFilterChange(["agent"]);
  });
  act(() => {
    lastDisplay().onToggleMetric("cost");
  });
  act(() => {
    lastFilterPanel().onApply([LATEST_FILTER]);
  });
};

const expectLatestOnTrace = () => {
  expect(lastToolbar().activeTabId).toBe("trace");
  const display = lastDisplay();
  expect(display.viewMode).toBe("tree");
  expect(display.showAgentGraph).toBe(false);
  expect(display.spanTypeFilter).toEqual(["agent"]);
  expect(display.visibleMetrics).toEqual(LATEST.visibleMetrics);
  expect(lastFilterPanel().currentFilters).toEqual([LATEST_FILTER]);
  expect(stored()).toEqual(LATEST);
};

describe("TraceDetailDrawerV2 Trace tab keeps this session's display", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(MOUNT_DEFAULT));
    mocks.getSavedViews.mockReset();
    mocks.getSavedViews.mockReturnValue(NO_VIEWS);
    mocks.getTraceDetail.mockReset();
    mocks.getTraceDetail.mockReturnValue(TRACE);
    mocks.toolbarProps.mockClear();
    mocks.displayProps.mockClear();
    mocks.filterPanelProps.mockClear();
    mocks.imagineProps.mockClear();
    mocks.updateView.mockClear();
    mocks.deleteView.mockClear();
  });

  it("keeps the latest display after a saved Imagine tab and back", () => {
    mocks.getSavedViews.mockReturnValue(WITH_IMAGINE);
    renderDrawer();

    // Opened with the settings saved by an earlier visit.
    expect(lastDisplay().showAgentGraph).toBe(true);
    expect(lastFilterPanel().currentFilters).toEqual([MOUNT_FILTER]);

    chooseLatest();
    act(() => {
      lastToolbar().onTabChange("imagine-1");
    });
    expect(lastToolbar().activeTabId).toBe("imagine-1");
    expect(mocks.imagineProps).toHaveBeenCalled();

    act(() => {
      lastToolbar().onTabChange("trace");
    });

    expectLatestOnTrace();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("keeps the latest display after a new Imagine tab and back", () => {
    renderDrawer();

    chooseLatest();
    act(() => {
      lastToolbar().onCreateImagineTab();
    });
    expect(lastToolbar().activeTabId).toBe("__new_imagine__");
    expect(mocks.imagineProps).toHaveBeenCalled();

    act(() => {
      lastToolbar().onTabChange("trace");
    });

    expectLatestOnTrace();
  });

  it("keeps the latest display when the open Trace tab is clicked again", () => {
    renderDrawer();

    chooseLatest();
    act(() => {
      lastToolbar().onTabChange("trace");
    });

    expectLatestOnTrace();
  });

  it("keeps the latest display after deleting the open Imagine tab", () => {
    mocks.getSavedViews.mockReturnValue(WITH_IMAGINE);
    renderDrawer();

    chooseLatest();
    act(() => {
      lastToolbar().onTabChange("imagine-1");
    });
    act(() => {
      lastToolbar().onCloseTab("imagine-1");
    });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(mocks.deleteView).toHaveBeenCalledWith(
      "imagine-1",
      expect.any(Object),
    );
    expectLatestOnTrace();
  });

  it("keeps the latest display after closing a new Imagine tab opened from a saved one", () => {
    mocks.getSavedViews.mockReturnValue(WITH_IMAGINE);
    renderDrawer();

    chooseLatest();
    // The saved Imagine tab clears the span filters while it is open.
    act(() => {
      lastToolbar().onTabChange("imagine-1");
    });
    act(() => {
      lastToolbar().onCreateImagineTab();
    });
    expect(lastToolbar().activeTabId).toBe("__new_imagine__");

    // The close button on the unsaved Imagine tab.
    act(() => {
      lastToolbar().onCloseTab("__new_imagine__");
    });

    expectLatestOnTrace();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("writes nothing over the Trace settings while Imagine is open", () => {
    mocks.getSavedViews.mockReturnValue(WITH_IMAGINE);
    renderDrawer();

    chooseLatest();
    expect(stored()).toEqual(LATEST);

    act(() => {
      lastToolbar().onTabChange("imagine-1");
    });

    expect(lastToolbar().activeTabId).toBe("imagine-1");
    expect(stored()).toEqual(LATEST);
    expect(mocks.updateView).not.toHaveBeenCalled();
  });
});
