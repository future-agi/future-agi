import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

// TH-8209: Error Feed page state precedence with a real QueryClient and the
// real hooks; only the transport (axios + the Observe catalog loader) is
// replaced. The page must tell "no Observe projects" apart from "no errors"
// and from a failed read.
const { axiosGet, fetchAllObserveProjects } = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  fetchAllObserveProjects: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGet },
  endpoints: {
    errorFeed: {
      list: "/tracer/feed/issues/",
      stats: "/tracer/feed/issues/stats/",
    },
  },
}));
vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects,
}));

import ErrorFeedView from "../ErrorFeedView";
import { useErrorFeedStore } from "../store";

const NO_PROJECTS_TITLE = "No Observe projects in this workspace.";
const NO_ERRORS_TITLE = "No errors — everything looks good!";
const FEED_FAILED_TITLE = "Couldn't load the Error Feed";

const emptyPage = {
  data: { status: true, result: { data: [], total: 0, limit: 25, offset: 0 } },
};

const rowPage = {
  data: {
    status: true,
    result: {
      total: 1,
      limit: 25,
      offset: 0,
      data: [
        {
          cluster_id: "c-1",
          error: { name: "Tool call failed", type: "ToolError" },
          source: "scanner",
          severity: "high",
          status: "escalating",
          trace_count: 3,
          users_affected: 1,
          fix_layer: "tools",
          trends: [],
          last_seen: new Date().toISOString(),
        },
      ],
    },
  },
};

const feedError = () =>
  Object.assign(new Error("forbidden"), {
    statusCode: 403,
    result: "User not associated with an organization",
  });

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ErrorFeedView />
    </QueryClientProvider>,
  );
};

describe("ErrorFeedView empty and failure states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useErrorFeedStore.setState({
      searchQuery: "",
      selectedProject: "",
      selectedStatus: "",
      selectedSeverity: "",
      selectedFixLayer: "",
      selectedSource: "",
      page: 0,
    });
  });

  it("shows the no-projects state when the Observe catalog and the feed are both empty", async () => {
    fetchAllObserveProjects.mockResolvedValue([]);
    axiosGet.mockResolvedValue(emptyPage);

    renderPage();

    expect(await screen.findByText(NO_PROJECTS_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(NO_ERRORS_TITLE)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open observe/i })).toHaveAttribute(
      "href",
      "/dashboard/observe",
    );
    // The feed request is still made; the page never infers emptiness from
    // the picker alone.
    expect(axiosGet).toHaveBeenCalledWith(
      "/tracer/feed/issues/",
      expect.objectContaining({ params: expect.any(Object) }),
    );
  });

  it("keeps the regular no-errors copy when projects exist and the feed is empty", async () => {
    fetchAllObserveProjects.mockResolvedValue([
      { id: "p-1", name: "Checkout bot" },
    ]);
    axiosGet.mockResolvedValue(emptyPage);

    renderPage();

    expect(await screen.findByText(NO_ERRORS_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(NO_PROJECTS_TITLE)).not.toBeInTheDocument();
  });

  it("lets feed rows win over an empty project picker", async () => {
    fetchAllObserveProjects.mockResolvedValue([]);
    axiosGet.mockResolvedValue(rowPage);

    renderPage();

    expect(await screen.findByText("Tool call failed")).toBeInTheDocument();
    expect(screen.queryByText(NO_PROJECTS_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_ERRORS_TITLE)).not.toBeInTheDocument();
  });

  it("shows a visible failure with retry instead of success copy when the feed read fails", async () => {
    fetchAllObserveProjects.mockResolvedValue([]);
    axiosGet.mockRejectedValueOnce(feedError()).mockResolvedValue(emptyPage);

    renderPage();

    expect(await screen.findByText(FEED_FAILED_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(NO_ERRORS_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_PROJECTS_TITLE)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText(NO_PROJECTS_TITLE)).toBeInTheDocument();
    await waitFor(() => expect(axiosGet).toHaveBeenCalledTimes(2));
  });

  it("does not call an empty feed 'no projects' while the catalog read failed", async () => {
    fetchAllObserveProjects.mockRejectedValue(new Error("catalog down"));
    axiosGet.mockResolvedValue(emptyPage);

    renderPage();

    expect(await screen.findByText(FEED_FAILED_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(NO_PROJECTS_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_ERRORS_TITLE)).not.toBeInTheDocument();
  });
});
