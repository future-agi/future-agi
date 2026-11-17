import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  project: "actual-project",
  placeholder: false,
  preferenceProject: null,
  history: null,
  route: {},
}));
vi.mock("react-router", () => ({ useParams: () => state.route }));
vi.mock("@tanstack/react-query", () => ({
  useInfiniteQuery: () => ({
    data: {
      pages: [
        {
          data: {
            result: {
              session_metadata: {},
              response: state.project
                ? [
                    {
                      trace_id: "t1",
                      system_metrics: { project_id: state.project },
                    },
                  ]
                : [],
            },
          },
        },
      ],
    },
    isPlaceholderData: state.placeholder,
  }),
}));
vi.mock("../SessionsView/useSessionTurnFields", () => ({
  default: (projectId) => {
    state.preferenceProject = projectId;
    return {
      projectId,
      fields: [],
      ready: Boolean(projectId),
      scopeKey: projectId,
    };
  },
}));
vi.mock("../SessionsView/SessionTurnFieldsDialog", () => ({
  default: () => null,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "w1" }),
}));
vi.mock("src/hooks/use-drawer-stack", () => ({
  coveredDrawerSx: () => ({}),
  useDrawerCovered: () => false,
}));
vi.mock("src/hooks/use-scroll-end", () => ({ useScrollEnd: () => null }));
vi.mock("./Header", () => ({ default: () => null }));
vi.mock("./Filters", () => ({ default: () => null }));
vi.mock("./SessionHistory", () => ({
  default: (props) => {
    state.history = props;
    return null;
  },
}));
vi.mock("./SessionEvalsList", () => ({ default: () => null }));
vi.mock("src/components/InlineAnnotator", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailPanel", () => ({
  CaptureCoverageNotice: () => null,
}));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));
import TracesDrawer from "./TracesDrawer";

const context = {
  workspace_id: "w1",
  project_id: null,
  filters: [],
  sort_params: [],
};
afterEach(cleanup);
beforeEach(() => {
  state.project = "actual-project";
  state.placeholder = false;
  state.route = {};
});

describe("cross-project session turn preferences", () => {
  it("uses the loaded turn's real project and stops using it during navigation", () => {
    const props = {
      open: true,
      onClose: vi.fn(),
      rowData: { session_id: "s1" },
      userIdForUserMode: "visitor",
      navigationContext: context,
    };
    const { rerender } = render(<TracesDrawer {...props} />);
    expect(state.preferenceProject).toBe("actual-project");
    expect(state.history.turnFields).toEqual([]);
    state.placeholder = true;
    rerender(<TracesDrawer {...props} />);
    expect(state.preferenceProject).toBeUndefined();
    state.placeholder = false;
    state.project = "another-project";
    rerender(<TracesDrawer {...props} />);
    expect(state.preferenceProject).toBe("another-project");
  });

  it("inherits a project view preference without a competing drawer read", () => {
    state.route = { observeId: "actual-project" };
    const fields = [{ kind: "metric", metric: "total_cost" }];
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "s1" }}
        turnFieldPreferences={{
          projectId: "actual-project",
          fields,
          ready: true,
          scopeKey: "owned-view",
        }}
      />,
    );
    expect(state.preferenceProject).toBeNull();
    expect(state.history.turnFields).toEqual(fields);
  });
});
