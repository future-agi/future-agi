// Regression test for the experiment snake_case hotfix: Data grid agent columns.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  AGENT_NODE_ANSWER_COLUMN_ID,
  AGENT_NODE_PLAN_COLUMN_ID,
  EXPERIMENT_ID,
  agentColumnConfig,
  columnConfigOnlyResponse,
  experimentDetailAgent,
} from "./experiment-snake-case.fixtures";

const grid = vi.hoisted(() => ({ columnDefs: null }));
const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  return {
    AgGridReact: ReactModule.forwardRef(function MockGrid(props) {
      grid.columnDefs = props.columnDefs;
      return null;
    }),
  };
});
vi.mock("react-router", () => ({
  useParams: () => ({ experimentId: EXPERIMENT_ID }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, post: vi.fn() },
  endpoints: {
    develop: {
      experiment: {
        experimentDetail: (id) => `/model-hub/experiments/v2/${id}/rows/`,
      },
    },
  },
}));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: () => ({
    data: experimentDetailAgent,
    isLoading: false,
  }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-wavesurfer-cache", () => ({
  default: () => ({
    getWaveSurferInstance: vi.fn(),
    storeWaveSurferInstance: vi.fn(),
    removeWaveSurferInstance: vi.fn(),
    updateWaveSurferInstance: vi.fn(),
    clearWaveSurferCache: vi.fn(),
  }),
}));
vi.mock("../ExperimentBarRightSection/ExperimentBarDataRightSection", () => ({
  default: () => null,
}));
vi.mock("../ExperimentData/ExperimentDetailDrawer", () => ({
  default: () => null,
}));
vi.mock("../ExperimentData/ExperimentStatusBar", () => ({
  default: () => null,
}));
vi.mock("../ExperimentData/RenderCellRunningOptions", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Evaluation/EditEvaluation", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/DataTab/EditColumnName", () => ({
  default: () => null,
}));
vi.mock("src/components/PdfPreviewDrawer", () => ({ default: () => null }));
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: () => null }),
);
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));

import ExperimentDataView from "../ExperimentData/ExperimentDataView";

describe("Data grid reads the rows column_config's snake_case agent keys", () => {
  it("D6: hides intermediate agent node columns (is_agent && !is_final) by default", async () => {
    // views/experiments.py:1472-1482 marks agent columns with is_agent / is_final
    axiosGetMock.mockResolvedValue({
      data: columnConfigOnlyResponse(agentColumnConfig, "string"),
    });

    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ExperimentDataView />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      const groups = (grid.columnDefs ?? []).filter((c) => c.children);
      expect(groups.length).toBe(1);
    });
    const agentGroup = grid.columnDefs.find((c) => c.children);
    const fields = agentGroup.children.map((child) => child.field);
    expect(fields).toContain(AGENT_NODE_ANSWER_COLUMN_ID);
    expect(fields).not.toContain(AGENT_NODE_PLAN_COLUMN_ID);
  });
});
