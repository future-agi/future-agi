import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import axios from "src/utils/axios";
import { SAVED_VIEWS_KEY, useGetSavedViews } from "src/api/project/saved-views";
import ObserveHeaderProvider from "../../project/context/ObserveHeaderContextProvider";
import { useObserveHeader } from "../../project/context/ObserveHeaderContext";
import ObservePage from "../ObservePage";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { savedViews: { list: "/tracer/saved-views/" } },
}));

vi.mock("../ObserveHeader", () => ({ default: () => null }));
vi.mock("../ReplayDrawer/ReplayDrawer", () => ({ default: () => null }));
vi.mock("src/components/observe-tabs", () => ({
  ObserveTabBar: () => null,
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

const makeView = (sessionId) => ({
  id: "saved-sessions",
  tab_type: "sessions",
  config: {
    extra_filters: [
      {
        column_id: "session_id",
        display_name: "Session ID",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "text",
          filter_op: "equals",
          filter_value: sessionId,
        },
      },
    ],
  },
});

const projectAView = makeView("session-a-only");
const projectBView = makeView("session-b-only");
const savedRoute = (projectId) =>
  `/dashboard/observe/${projectId}/sessions?tab=view-saved-sessions`;

// Read the actual shared context consumed by SessionsView, not a setter spy.
function SavedConfigProbe() {
  const { activeViewConfig } = useObserveHeader();
  const { observeId } = useParams();
  const { data } = useGetSavedViews(observeId);
  return (
    <>
      <output data-testid="project">{observeId}</output>
      <output data-testid="active-config">
        {JSON.stringify(activeViewConfig)}
      </output>
      <output data-testid="saved-views">{JSON.stringify(data ?? null)}</output>
    </>
  );
}

// eslint-disable-next-line react/prop-types
function NavigateTo({ to }) {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(to)}>
        Open destination
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        Go back
      </button>
    </>
  );
}

let queryClient;

function renderPage({
  destination = "/dashboard/observe/project-b/sessions",
  projectBViews = [],
} = {}) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  queryClient.setQueryData([SAVED_VIEWS_KEY, "project-a"], {
    custom_views: [projectAView],
  });
  // null models an uncached project whose saved-view request is still pending.
  if (projectBViews !== null) {
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-b"], {
      custom_views: projectBViews,
    });
  }
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[savedRoute("project-a")]}>
        <ObserveHeaderProvider>
          <NavigateTo to={destination} />
          <Routes>
            <Route
              path="/dashboard/observe/:observeId"
              element={<ObservePage />}
            >
              <Route path="sessions" element={<SavedConfigProbe />} />
            </Route>
          </Routes>
        </ObserveHeaderProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const readConfig = () =>
  JSON.parse(screen.getByTestId("active-config").textContent);
const openDestination = () =>
  fireEvent.click(screen.getByRole("button", { name: "Open destination" }));

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue({ data: { result: { custom_views: [] } } });
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
});

describe("OBS-SW-55 saved-view project scope", () => {
  it.each([
    ["another project without a tab", "/dashboard/observe/project-b/sessions"],
    [
      "another project with the fixed tab",
      "/dashboard/observe/project-b/sessions?tab=sessions",
    ],
    ["the same project without a tab", "/dashboard/observe/project-a/sessions"],
    [
      "the same project with the fixed tab",
      "/dashboard/observe/project-a/sessions?tab=sessions",
    ],
  ])(
    "clears saved filters on navigation to %s",
    async (_label, destination) => {
      renderPage({ destination });
      await waitFor(() => expect(readConfig()).toEqual(projectAView.config));

      openDestination();

      await waitFor(() => expect(readConfig()).toBeNull());
      expect(screen.getByTestId("project").textContent).toBe(
        destination.includes("project-b") ? "project-b" : "project-a",
      );
    },
  );

  it("clears the old config while the next project loads, then hydrates its view", async () => {
    let resolveViews;
    const pendingViews = new Promise((resolve) => {
      resolveViews = resolve;
    });
    axios.get.mockReturnValue(pendingViews);
    renderPage({
      destination: savedRoute("project-b"),
      projectBViews: null,
    });
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));

    openDestination();

    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith("/tracer/saved-views/", {
        params: { project_id: "project-b" },
      }),
    );
    await waitFor(() => expect(readConfig()).toBeNull());
    await act(async () => {
      resolveViews({ data: { result: { custom_views: [projectBView] } } });
      await pendingViews;
    });
    await waitFor(() => expect(readConfig()).toEqual(projectBView.config));
  });

  it("hydrates the current project even when the saved tab key is unchanged", async () => {
    renderPage({
      destination: savedRoute("project-b"),
      projectBViews: [projectBView],
    });
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));

    openDestination();

    await waitFor(() => expect(readConfig()).toEqual(projectBView.config));
    expect(screen.getByTestId("project").textContent).toBe("project-b");
  });

  it.each([
    ["an empty list", []],
    ["a nonmatching list", [{ ...projectBView, id: "other-view" }]],
    ["a view without config", [{ ...projectBView, config: null }]],
  ])("does not retain another project's view for %s", async (_label, views) => {
    renderPage({ destination: savedRoute("project-b"), projectBViews: views });
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));

    openDestination();

    await waitFor(() => expect(readConfig()).toBeNull());
    expect(screen.getByTestId("project").textContent).toBe("project-b");
  });

  it("clears a saved config when it disappears from the current project list", async () => {
    renderPage();
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));

    act(() => {
      queryClient.setQueryData([SAVED_VIEWS_KEY, "project-a"], {
        custom_views: [],
      });
    });

    await waitFor(() => expect(readConfig()).toBeNull());
  });

  it("hydrates a direct URL and does not reapply a present view on refetch", async () => {
    renderPage();
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));
    const refetched = { custom_views: [makeView("refetched-session")] };

    act(() => {
      queryClient.setQueryData([SAVED_VIEWS_KEY, "project-a"], refetched);
    });

    await waitFor(() =>
      expect(screen.getByTestId("saved-views").textContent).toBe(
        JSON.stringify(refetched),
      ),
    );
    expect(readConfig()).toEqual(projectAView.config);
  });

  it("rehydrates the saved view when returning from an ordinary project route", async () => {
    renderPage();
    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));
    openDestination();
    await waitFor(() => expect(readConfig()).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Go back" }));

    await waitFor(() => expect(readConfig()).toEqual(projectAView.config));
    expect(screen.getByTestId("project").textContent).toBe("project-a");
  });
});
