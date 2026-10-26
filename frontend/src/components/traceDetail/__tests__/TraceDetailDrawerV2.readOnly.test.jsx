import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  getSavedViews: vi.fn(() => ({ data: undefined })),
  getTraceDetail: vi.fn(() => ({
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
  })),
  toolbarProps: vi.fn(),
  headerProps: vi.fn(),
  spanPaneProps: vi.fn(),
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

vi.mock("../DrawerHeader", () => ({
  default: (props) => {
    mocks.headerProps(props);
    return null;
  },
}));

vi.mock("../SpanDetailPane", () => ({
  default: (props) => {
    mocks.spanPaneProps(props);
    return null;
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
        projectId="project-1"
        {...props}
      />
    </QueryClientProvider>,
  );

describe("TraceDetailDrawerV2 read-only", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    mocks.getSavedViews.mockClear();
    mocks.getTraceDetail.mockClear();
    mocks.toolbarProps.mockClear();
    mocks.headerProps.mockClear();
    mocks.spanPaneProps.mockClear();
    mocks.displayProps.mockClear();
    mocks.createView.mockClear();
    mocks.updateView.mockClear();
  });

  it("reads saved views and offers every action when it can act", () => {
    renderDrawer();

    expect(mocks.getSavedViews).toHaveBeenLastCalledWith("project-1");
    expect(mocks.toolbarProps.mock.lastCall[0].readOnly).toBe(false);
    const header = mocks.headerProps.mock.lastCall[0];
    expect(header.onShare).toEqual(expect.any(Function));
    expect(header.onOpenNewTab).toEqual(expect.any(Function));
    const pane = mocks.spanPaneProps.mock.lastCall[0];
    expect(pane.onAction).toEqual(expect.any(Function));
    expect(pane.readOnly).toBe(false);
  });

  it("asks for no saved views and offers no action when read-only", () => {
    renderDrawer({ readOnly: true });

    expect(mocks.getSavedViews).toHaveBeenLastCalledWith(null);
    expect(mocks.toolbarProps.mock.lastCall[0].readOnly).toBe(true);
    const header = mocks.headerProps.mock.lastCall[0];
    expect(header.onShare).toBeUndefined();
    expect(header.onOpenNewTab).toBeUndefined();
    const pane = mocks.spanPaneProps.mock.lastCall[0];
    expect(pane.onAction).toBeUndefined();
    expect(pane.readOnly).toBe(true);
  });

  it("leaves out the filter and the shared default when read-only", () => {
    renderDrawer({ readOnly: true });

    expect(mocks.toolbarProps.mock.lastCall[0].hideFilter).toBe(true);
    const display = mocks.displayProps.mock.lastCall[0];
    expect(display.hideSetDefault).toBe(true);

    display.onSetDefaultView();
    expect(mocks.createView).not.toHaveBeenCalled();
    expect(mocks.updateView).not.toHaveBeenCalled();
  });

  it("offers the filter and the shared default when it can act", () => {
    renderDrawer();

    expect(mocks.toolbarProps.mock.lastCall[0].hideFilter).toBe(false);
    expect(mocks.displayProps.mock.lastCall[0].hideSetDefault).toBe(false);
  });
});
