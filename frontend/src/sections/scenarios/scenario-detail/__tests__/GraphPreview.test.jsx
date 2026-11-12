import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GraphPreview from "../GraphPreview";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/axios", () => ({
  default: { put: vi.fn() },
  endpoints: { scenarios: { edit: () => "/simulate/scenarios/scn-1/edit/" } },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// The graph canvas and its editor are not under test here.
vi.mock("@xyflow/react", () => ({
  // eslint-disable-next-line react/prop-types
  ReactFlowProvider: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/GraphBuilder/common", () => ({
  dagreTransformAndLayout: (nodes, edges) => ({ nodes, edges }),
}));
vi.mock("src/components/GraphBuilder/GraphView", () => ({
  default: () => <div data-testid="graph-view" />,
}));
vi.mock("src/components/GraphBuilder/GraphBuilderDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/GraphBuilder/validation", () => ({
  ValidateAndTransformGraphSchema: () => ({ safeParse: () => ({}) }),
}));
vi.mock("src/components/GraphBuilder/connectivity", () => ({
  validateGraphConnectivity: () => ({}),
}));
vi.mock("src/components/GraphBuilder/DisconnectedNodesToast", () => ({
  default: () => null,
}));
vi.mock("src/components/GraphBuilder/store/graphStore", () => ({
  useGraphStore: { getState: () => ({ nodes: [], edges: [] }) },
}));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));

const renderPreview = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GraphPreview {...props} />
    </QueryClientProvider>,
  );

describe("GraphPreview: a scenario whose generation failed", () => {
  // The Replay sessions drawer shows this panel on its own (viewOnly), so the
  // line has to make sense without the scenario page around it.
  it.each([
    ["on the scenario page", {}],
    ["in a view-only panel", { viewOnly: true }],
  ])("says plainly why there is no graph %s", (_where, extra) => {
    renderPreview({
      scenario: { id: "scn-1", status: "Failed", graph: {} },
      ...extra,
    });

    expect(
      screen.getByText(
        "Scenario generation failed, so there is no graph to show.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("There was an error generating the scenario graph"),
    ).toBeNull();
    expect(screen.queryByTestId("graph-view")).toBeNull();
  });
});
