import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";

const mocks = vi.hoisted(() => ({
  spanPaneProps: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("../TraceDisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({
    isLoading: false,
    data: {
      trace: { id: "trace-1", tags: [] },
      observation_spans: [
        {
          observation_span: {
            id: "span-1",
            trace: "trace-1",
            name: "chat_completion",
            observation_type: "llm",
            start_time: "2026-10-14T11:08:13Z",
          },
          children: [],
        },
      ],
    },
  }),
}));

vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useSharedIssueTrace: () => ({ isLoading: false, data: undefined }),
}));

vi.mock("../DrawerToolbar", () => ({ default: () => null }));

vi.mock("../DrawerHeader", () => ({ default: () => null }));

vi.mock("../SpanDetailPane", () => ({
  default: (props) => {
    mocks.spanPaneProps(props);
    return null;
  },
}));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

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

// The span detail pane's "Actions" button opens the menu through onAction.
const openActionsMenu = () => {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  act(() => {
    mocks.spanPaneProps.mock.lastCall[0].onAction("_open", anchor);
  });
  return screen.getByRole("menu");
};

describe("TraceDetailDrawerV2 'Iterate in agent playground' action", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    window.history.pushState({}, "", "/");
    mocks.spanPaneProps.mockClear();
    mocks.enqueueSnackbar.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the action between the prompt workbench and tags", () => {
    renderDrawer();

    const menu = openActionsMenu();
    const labels = within(menu)
      .getAllByRole("menuitem")
      .map((item) => item.textContent.trim());

    expect(labels).toEqual([
      "Move to dataset",
      "Iterate in prompt workbench",
      "Iterate in agent playground",
      "Add Tags",
      "Annotate",
      "Add to annotation queue",
    ]);
  });

  it("creates a graph from the trace and opens it in the agent playground", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({
      data: { result: { graph_id: "graph-1", version_id: "version-1" } },
    });
    renderDrawer();

    const menu = openActionsMenu();
    fireEvent.click(
      within(menu).getByRole("menuitem", {
        name: "Iterate in agent playground",
      }),
    );

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        endpoints.agentPlayground.createGraphFromTrace,
        { trace_id: "trace-1" },
      ),
    );
    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe(
        "/dashboard/agents/playground/graph-1/build?version=version-1",
      ),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("shows the existing error when the trace cannot be converted", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("400"));
    renderDrawer();

    const menu = openActionsMenu();
    fireEvent.click(
      within(menu).getByRole("menuitem", {
        name: "Iterate in agent playground",
      }),
    );

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Failed to create agent graph from trace.",
        { variant: "error" },
      ),
    );
    expect(window.location.pathname).toBe("/");
  });
});
