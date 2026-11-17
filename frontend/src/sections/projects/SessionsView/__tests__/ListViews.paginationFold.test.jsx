/* eslint-disable react/prop-types */
// C3: on a project's Sessions and Users tabs the list sat under the graph in
// a box sized to the whole tab (height: 100%), so the list ran one graph
// height past the window and its pagination ("Results per page / Back / 1 /
// Next") fell below a 900 px fold. The Trace tab and the user page keep it
// on screen. The fix stacks chips, graph and list in one column the height of
// the tab and gives the list what is left.
// Layout is not measured here: every Box stamps the sx it was given on its
// element (data-sx), and the tests read the sx the views set.
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";
import UsersView from "../../UsersView/UsersView";
import useUsersStore from "../../UsersView/Store/usersStore";

const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  replay: vi.fn(),
  save: vi.fn(),
  selection: { toggledNodes: [], selectAll: false, totalRowCount: 2 },
  header: {
    activeViewConfig: null,
    setHeaderConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    registerGetViewConfig: vi.fn(),
  },
}));

vi.mock("@mui/material", async (importOriginal) => {
  const actual = await importOriginal();
  const { createElement, forwardRef } = await import("react");
  const Box = forwardRef(function SxStampedBox(props, ref) {
    return createElement(actual.Box, {
      ...props,
      ref,
      "data-sx": JSON.stringify(props.sx ?? null),
    });
  });
  return { ...actual, Box };
});
vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  Helmet: () => null,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ observeId: "project-1" }),
  useLocation: () => ({ pathname: "/dashboard/observe/project-1/users" }),
  useNavigate: () => h.navigate,
}));
vi.mock(
  "src/hooks/useRestrictSimulateToEnvironments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRestrictSimulateToEnvironments: () => false,
  }),
);
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => {
  const { useState } = await import("react");
  return {
    ...(await importOriginal()),
    useUrlState: (_key, initial) => useState(initial),
  };
});
vi.mock(
  "src/sections/project/context/ObserveHeaderContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useObserveHeader: () => h.header,
  }),
);
vi.mock("../../LLMTracing/useLLMTracingFilters", async (importOriginal) => {
  const { useMemo, useState } = await import("react");
  return {
    ...(await importOriginal()),
    useLLMTracingFilters: (initialFilters, initialDate) => {
      const [filters, setFilters] = useState(initialFilters);
      const [dateFilter, setDateFilter] = useState(initialDate);
      const validatedFilters = useMemo(
        () => [
          {
            column_id: "created_at",
            filter_config: {
              filter_type: "datetime",
              filter_op: "between",
              filter_value: ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
            },
          },
        ],
        [],
      );
      return {
        filters,
        setFilters,
        dateFilter,
        setDateFilter,
        validatedFilters,
      };
    },
  };
});
vi.mock("../../LLMTracing/ObserveToolbar", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../Session-grid", async (importOriginal) => {
  const { createElement, forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    default: forwardRef(function SessionGridDouble(_props, _ref) {
      return createElement("div", { "data-testid": "session-grid" });
    }),
  };
});
vi.mock("../../UsersView/UsersGrid", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="users-grid" />,
}));
vi.mock("../../UsersView/UsersEmptyScreen", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../UsersView/useProjectFilterField", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../LLMTracing/FilterChips", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../LLMTracing/SelectAllBanner", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../LLMTracing/CustomColumnDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
// PrimaryGraph has only a default export, and loading the real module would
// load ApexCharts, which every suite here stubs; so this one is a full mock.
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: () => <div data-testid="observe-graph" />,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock(
  "../../LLMTracing/useCursorAttributeInventory",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useCursorAttributeInventory: () => ({
      attributes: [],
      inventoryControlProps: {},
    }),
  }),
);
vi.mock("../ReplaySessions/store", async (importOriginal) => ({
  ...(await importOriginal()),
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    }),
  useSessionsGridStore: {
    setState: (state) => Object.assign(h.selection, state),
  },
  useSessionsGridStoreShallow: (selector) => selector(h.selection),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: {
      ...original.default,
      post: () => Promise.resolve({ data: {} }),
    },
  };
});
vi.mock("src/api/project/replay-sessions", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateReplaySessions: () => ({ mutate: h.replay }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useUpdateSavedView: () => ({ mutate: h.save }),
  useUpdateWorkspaceSavedView: () => ({ mutate: h.save }),
}));

const mount = (Component) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <Component />
      </React.Suspense>
    </QueryClientProvider>,
  );

const sxOf = (element) =>
  JSON.parse(element?.getAttribute("data-sx") ?? "null");

// The graph is lazy-loaded in both views; give its first import time.
const findGraph = () =>
  screen.findByTestId("observe-graph", {}, { timeout: 10000 });

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/?tab=sessions");
  h.header.activeViewConfig = null;
  h.selection = { toggledNodes: [], selectAll: false, totalRowCount: 2 };
  useUsersStore.setState({ columns: [], gridApi: null, searchQuery: "" });
});
afterEach(cleanup);

describe("Sessions tab keeps its pagination on screen (C3)", () => {
  it("gives the sessions list the height the graph leaves, not the whole tab", async () => {
    mount(SessionsView);
    await findGraph();

    const list = screen.getByTestId("session-grid").parentElement;
    expect(sxOf(list)).toMatchObject({
      display: "flex",
      flexDirection: "column",
      flex: 1,
      minHeight: 0,
    });
    expect(sxOf(list)).not.toHaveProperty("height");
  });

  it("stacks the graph and the sessions list in one column the height of the tab", async () => {
    mount(SessionsView);
    const graph = await findGraph();

    const list = screen.getByTestId("session-grid").parentElement;
    const column = list.parentElement;
    expect(sxOf(column)).toMatchObject({
      display: "flex",
      flexDirection: "column",
      height: "100%",
    });
    expect(column).toContainElement(graph);
    // The graph keeps its height; the list takes the difference.
    expect(sxOf(graph.parentElement)).toMatchObject({ flexShrink: 0 });
  });
});

describe("Users tab keeps its pagination on screen (C3)", () => {
  it("gives the users list the height the graph leaves, not the whole tab", async () => {
    mount(UsersView);
    await findGraph();

    const gridBox = screen.getByTestId("users-grid").parentElement;
    const list = gridBox.parentElement;
    expect(sxOf(list)).toMatchObject({
      display: "flex",
      flexDirection: "column",
      flex: 1,
      minHeight: 0,
    });
    expect(sxOf(list)).not.toHaveProperty("height");
    // The grid's own box hands that height down instead of growing to fit
    // its rows, so AG Grid's pagination panel ends inside the window.
    expect(sxOf(gridBox)).toMatchObject({ flex: 1, minHeight: 0 });
  });

  it("stacks the graph and the users list in one column the height of the tab", async () => {
    mount(UsersView);
    const graph = await findGraph();

    const gridBox = screen.getByTestId("users-grid").parentElement;
    const column = gridBox.parentElement.parentElement;
    expect(sxOf(column)).toMatchObject({
      display: "flex",
      flexDirection: "column",
      height: "100%",
    });
    expect(column).toContainElement(graph);
    expect(sxOf(graph.parentElement)).toMatchObject({ flexShrink: 0 });
  });
});
