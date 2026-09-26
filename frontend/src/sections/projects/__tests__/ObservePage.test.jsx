import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { ErrorBoundary } from "react-error-boundary";

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
  useGetProjectDetails: () => ({ data: undefined }),
}));

vi.mock("src/api/project/saved-views", () => ({
  SAVED_VIEWS_KEY: "saved-views",
  useGetSavedViews: () => ({ data: undefined }),
}));

vi.mock("../ReplayDrawer/ReplayDrawer", () => ({
  default: () => <div>replay drawer</div>,
}));

import ObservePage from "../ObservePage";

const CrashingTab = () => {
  throw new Error("Invalid time value");
};

const CrashingChunkTab = () => {
  throw new Error("Loading chunk 42 failed after several retries");
};

const NavigateTo = ({ to }) => {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      leave tab
    </button>
  );
};

NavigateTo.propTypes = { to: PropTypes.string.isRequired };

// Stands in for the app-level boundary in app.jsx that used to swallow the
// whole page when a tab threw.
const renderObservePage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary fallback={<div>whole app crashed</div>}>
        <MemoryRouter
          initialEntries={["/dashboard/observe/proj-1/llm-tracing"]}
        >
          <NavigateTo to="/dashboard/observe/proj-1/sessions" />
          <Routes>
            <Route
              path="/dashboard/observe/:observeId"
              element={<ObservePage />}
            >
              <Route path="llm-tracing" element={<CrashingTab />} />
              <Route path="sessions" element={<div>sessions tab</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ErrorBoundary>
    </QueryClientProvider>,
  );
};

const renderObservePageWithTab = (TabComponent) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary fallback={<div>whole app crashed</div>}>
        <MemoryRouter
          initialEntries={["/dashboard/observe/proj-1/llm-tracing"]}
        >
          <Routes>
            <Route
              path="/dashboard/observe/:observeId"
              element={<ObservePage />}
            >
              <Route path="llm-tracing" element={<TabComponent />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ErrorBoundary>
    </QueryClientProvider>,
  );
};

describe("ObservePage tab containment", () => {
  let consoleError;

  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  it("keeps a throwing tab's error inside the tab", () => {
    renderObservePage();

    expect(screen.getByText("Could not load this tab")).toBeInTheDocument();
    expect(screen.queryByText("whole app crashed")).not.toBeInTheDocument();
    expect(screen.getByText("observe header")).toBeInTheDocument();
    expect(screen.getByText("observe tab bar")).toBeInTheDocument();
  });

  it("recovers the tab once the route segment changes", async () => {
    renderObservePage();

    expect(screen.getByText("Could not load this tab")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /leave tab/i }));

    await waitFor(() =>
      expect(screen.getByText("sessions tab")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("Could not load this tab"),
    ).not.toBeInTheDocument();
  });

  it("lets a chunk-load error bubble past the tab boundary to the app-level one", () => {
    renderObservePageWithTab(CrashingChunkTab);

    expect(screen.getByText("whole app crashed")).toBeInTheDocument();
    expect(
      screen.queryByText("Could not load this tab"),
    ).not.toBeInTheDocument();
  });
});
