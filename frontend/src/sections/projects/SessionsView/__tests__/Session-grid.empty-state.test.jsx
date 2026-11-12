import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";

const { gridApi, gridState, sessionStoreState } = vi.hoisted(() => ({
  // Catch-all grid API: any method the grid calls is a spy, so this test
  // fails only on the empty-state contract.
  gridApi: new Proxy(
    {},
    {
      get(target, key) {
        if (key in target) return target[key];
        if (key === "then" || typeof key === "symbol") return undefined;
        target[key] = vi.fn();
        return target[key];
      },
    },
  ),
  gridState: { props: null },
  sessionStoreState: {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: null,
  },
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      gridState.props = props;
      ReactModule.useImperativeHandle(ref, () => ({ api: gridApi }), []);
      // An empty grid body: the grid's own no-rows overlay when it passes
      // one, otherwise AG Grid's default text.
      // eslint-disable-next-line react/prop-types
      const NoRowsOverlay = props.noRowsOverlayComponent;
      return (
        <div data-testid="session-grid">
          {NoRowsOverlay ? <NoRowsOverlay /> : "No Rows To Show"}
        </div>
      );
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/utils/utils", () => ({ getRandomId: () => "column" }));
vi.mock("src/sections/develop-detail/Common/TotalRowsStatusBar", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  readQuery: vi.fn(),
  default: { get: vi.fn() },
  endpoints: {
    project: { projectSessionList: () => "/sessions/list/" },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../../TracesDrawer/TracesDrawer", () => ({ default: () => null }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("../common", () => ({
  getSessionListColumnDef: (column) => ({ field: column.id }),
  initialVisibility: { session_id: true },
  mergeNonCustomColumns: (_current, incoming) => incoming,
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { observeSessionidClicked: "session" },
  trackEvent: vi.fn(),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => ["day", vi.fn()],
}));
vi.mock("../../UsersView/common", () => ({
  userTraceRowHeightMapping: { Short: { height: 40 } },
}));
vi.mock("src/sections/projects/LLMTracing/common", () => ({
  DOC_LINKS: {
    sessions: "https://docs.futureagi.com/docs/observe/features/session",
  },
  normalizeConfigKeys: (config) => config || [],
  toBackendFilters: (filters) => filters,
}));
vi.mock("../ReplaySessions/store", () => ({
  useSessionsGridStore: { setState: vi.fn() },
  useSessionsGridStoreShallow: (selector) => selector(sessionStoreState),
}));

import SessionGrid from "../Session-grid";

const renderGrid = () =>
  render(
    <SessionGrid
      ref={React.createRef()}
      updateObj={{ session_id: true }}
      columns={[{ id: "session_id", isVisible: true }]}
      setColumns={vi.fn()}
      filters={[]}
      projectId="project-1"
      cellHeight="Short"
      onSelectionChanged={vi.fn()}
      className=""
      onGridReady={vi.fn()}
    />,
  );

describe("SessionGrid empty state", () => {
  beforeEach(() => {
    gridState.props = null;
  });

  it("explains how sessions are grouped and links the setup docs when no sessions are found", async () => {
    renderGrid();
    await waitFor(() => expect(gridState.props).not.toBeNull());

    expect(screen.queryByText("No Rows To Show")).not.toBeInTheDocument();
    expect(
      screen.getByText("No sessions in this time range"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Sessions group traces that share a session.id attribute. Add it to your spans to see sessions here, or try a wider time range.",
      ),
    ).toBeInTheDocument();
    const docsLink = screen.getByRole("link", { name: "How to add sessions" });
    expect(docsLink).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/observe/features/session",
    );
    expect(docsLink).toHaveAttribute("target", "_blank");
    expect(docsLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
