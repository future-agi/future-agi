import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { renderWithRouter, screen } from "src/utils/test-utils";

const { mockUseGetProjectDetails } = vi.hoisted(() => ({
  mockUseGetProjectDetails: vi.fn(),
}));

vi.mock("../../project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => ({
    headerConfig: {},
    setActiveViewConfig: vi.fn(),
  }),
}));

vi.mock("../ObserveHeader", () => ({
  default: () => <div>observe header</div>,
}));

vi.mock("src/components/observe-tabs", () => ({
  ObserveTabBar: () => <div>observe tab bar</div>,
  ViewConfigModal: () => null,
  TabContextMenu: () => null,
}));

vi.mock("../LLMTracing/tabStore", () => ({
  useTabStoreShallow: () => ({
    createModalOpen: false,
    editModalView: null,
    contextMenuAnchor: null,
    closeCreateModal: vi.fn(),
    closeContextMenu: vi.fn(),
    startRenaming: vi.fn(),
  }),
  resetTabStore: vi.fn(),
}));

vi.mock("../LLMTracing/states", () => ({ resetTraceGridStore: vi.fn() }));

vi.mock("../SessionsView/ReplaySessions/store", () => ({
  resetReplaySessionsStore: vi.fn(),
  resetSessionsGridStore: vi.fn(),
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: mockUseGetProjectDetails,
}));

vi.mock("src/api/project/saved-views", () => ({
  SAVED_VIEWS_KEY: "saved-views",
  useGetSavedViews: () => ({ data: undefined }),
}));

vi.mock("../ReplayDrawer/ReplayDrawer", () => ({
  default: () => <div>replay drawer</div>,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));

import ObservePage from "../ObservePage";

// The app's axios rejects with the server's envelope plus statusCode.
const refusal = (statusCode, message, code) => ({
  status: false,
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
});

const renderObservePage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route path="/dashboard/observe/:observeId" element={<ObservePage />}>
          <Route path="llm-tracing" element={<div>traces tab</div>} />
        </Route>
      </Routes>
    </QueryClientProvider>,
    { route: "/dashboard/observe/proj-1/llm-tracing" },
  );
};

const NOT_FOUND_TITLE = "This project doesn't exist or you don't have access";

describe("ObservePage not-found gate", () => {
  beforeEach(() => {
    mockUseGetProjectDetails.mockReset();
  });

  it("shows not-found for the current 400 on a deleted project", () => {
    mockUseGetProjectDetails.mockReturnValue({
      data: undefined,
      error: refusal(400, "Project Not Found", "invalid"),
    });

    renderObservePage();

    expect(screen.getByText(NOT_FOUND_TITLE)).toBeInTheDocument();
    expect(screen.queryByText("traces tab")).not.toBeInTheDocument();
    expect(screen.queryByText("observe tab bar")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to observe/i }),
    ).toHaveAttribute("href", "/dashboard/observe");
  });

  it("shows not-found for a 404", () => {
    mockUseGetProjectDetails.mockReturnValue({
      data: undefined,
      error: refusal(404, "Project Not Found", "not_found"),
    });

    renderObservePage();

    expect(screen.getByText(NOT_FOUND_TITLE)).toBeInTheDocument();
    expect(screen.queryByText("traces tab")).not.toBeInTheDocument();
  });

  it("keeps a loaded project's page when a later refetch fails", () => {
    mockUseGetProjectDetails.mockReturnValue({
      data: { id: "proj-1", source: "traces" },
      error: refusal(400, "Project Not Found", "invalid"),
    });

    renderObservePage();

    expect(screen.getByText("traces tab")).toBeInTheDocument();
    expect(screen.queryByText(NOT_FOUND_TITLE)).not.toBeInTheDocument();
  });

  it("keeps the page for any other failure", () => {
    mockUseGetProjectDetails.mockReturnValue({
      data: undefined,
      error: refusal(500, "Something went wrong", "error"),
    });

    renderObservePage();

    expect(screen.getByText("traces tab")).toBeInTheDocument();
    expect(screen.getByText("observe tab bar")).toBeInTheDocument();
    expect(screen.queryByText(NOT_FOUND_TITLE)).not.toBeInTheDocument();
  });

  it("keeps the page while the project loads", () => {
    mockUseGetProjectDetails.mockReturnValue({ data: undefined, error: null });

    renderObservePage();

    expect(screen.getByText("traces tab")).toBeInTheDocument();
    expect(screen.queryByText(NOT_FOUND_TITLE)).not.toBeInTheDocument();
  });
});
