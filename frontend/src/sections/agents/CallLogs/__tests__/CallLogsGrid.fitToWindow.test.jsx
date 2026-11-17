/* eslint-disable react/prop-types */
// C3: a voice project's call list (Observe > the project's Trace tab) was a
// fixed 78vh tall under the graph, so its pagination ("Results per page /
// Back / 1 / Next") sat below a 900 px fold. The trace and span grids on the
// same page fill the window from where they start (useFitHeight) and keep
// that footer on screen; the call list now does the same when its page asks
// for it (fitToWindow). Grids in other hosts keep the fixed height.
// Layout is not measured here: useFitHeight is a stub that returns a height,
// and every Box stamps the sx it was given on its element (data-sx).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

const {
  fit,
  queryClientMock,
  simulationAgentsState,
  useCallLogsMock,
  useQueryMock,
} = vi.hoisted(() => ({
  fit: { refs: [], height: 612 },
  queryClientMock: { prefetchQuery: vi.fn() },
  simulationAgentsState: { data: [] },
  useCallLogsMock: vi.fn(),
  useQueryMock: vi.fn(),
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
vi.mock("src/hooks/use-fit-height", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (ref) => {
    fit.refs.push(ref);
    return fit.height;
  },
}));
vi.mock("ag-grid-react", async (importOriginal) => {
  const { createElement, forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    AgGridReact: forwardRef(function AgGridReactDouble(_props, _ref) {
      return createElement("div", { "data-testid": "call-logs-grid" });
    }),
  };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: (...args) => useQueryMock(...args),
  useQueryClient: () => queryClientMock,
}));
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/sections/agents/helper", async (importOriginal) => ({
  ...(await importOriginal()),
  getCallLogsColumnDefs: () => [],
  prefetchCallLogs: vi.fn(),
  useCallLogs: (...args) => useCallLogsMock(...args),
}));
vi.mock(
  "src/sections/agents/store/agentDetailsStore",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAgentDetailsStore: () => ({ selectedVersion: "version-1" }),
  }),
);
vi.mock("src/sections/agents/store", async (importOriginal) => ({
  ...(await importOriginal()),
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: false, reset: vi.fn() }),
}));
vi.mock("src/sections/test-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  resetState: vi.fn(),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector({ testDetailDrawerOpen: null }),
}));
// The call drawer module has only a default export and loads the whole voice
// drawer; like the sibling CallLogsGrid suites, stub it outright.
vi.mock(
  "src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/components/show", async (importOriginal) => ({
  ...(await importOriginal()),
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useSimulationAgents: () => simulationAgentsState,
}));

import CallLogsGrid from "../CallLogsGrid";

const projectRead = {
  data: {
    count: 0,
    count_is_lower_bound: false,
    total_pages: 1,
    current_page: 1,
    results: [],
    config: [],
    has_more: false,
    next_cursor: null,
    query_complete: true,
    query_status: "complete",
    query_error_code: null,
  },
  isLoading: false,
  error: null,
  queryKey: ["callLogs", "project", "project-1", 25, {}, 1, 0],
};

const sxOf = (element) =>
  JSON.parse(element?.getAttribute("data-sx") ?? "null");

// The outermost box: the one whose height decides where the footer lands.
const gridRoot = () => {
  const grid = screen.getByTestId("call-logs-grid");
  return grid.closest(".ag-theme-alpine").parentElement;
};

describe("CallLogsGrid height (C3)", () => {
  beforeEach(() => {
    fit.refs = [];
    useCallLogsMock.mockReset().mockReturnValue(projectRead);
    useQueryMock.mockReset().mockReturnValue({ data: undefined });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fills the window from where it starts on the project's call list", () => {
    render(
      <CallLogsGrid id="project-1" module="project" fitToWindow hideDrawer />,
    );

    const root = gridRoot();
    expect(sxOf(root)).toMatchObject({
      height: `${fit.height}px`,
      display: "flex",
    });
    // The height is measured from the grid's own outer box.
    expect(fit.refs.length).toBeGreaterThan(0);
    expect(fit.refs.at(-1).current).toBe(root);
  });

  it("keeps the fixed height where the page does not ask for it", () => {
    // The annotation queue's add-items dialog lists a voice project's calls
    // inside its own bounded panel.
    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(sxOf(gridRoot())).toMatchObject({ height: "78vh" });
    expect(fit.refs.every((ref) => ref.current === null)).toBe(true);
  });
});
