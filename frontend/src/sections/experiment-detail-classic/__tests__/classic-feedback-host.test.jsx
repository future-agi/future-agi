import React from "react";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { launchRedirect } from "src/launch/launchRoutes";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", async () => {
  const { default: PropTypes } = await import("prop-types");
  const Grid = ({ onCellClicked, rowData }) => (
    <button
      onClick={() =>
        onCellClicked({
          colDef: { headerName: "Description" },
          data: rowData[0],
        })
      }
    >
      Inspect evaluation
    </button>
  );
  Grid.propTypes = { onCellClicked: PropTypes.func, rowData: PropTypes.array };
  return { AgGridReact: Grid };
});
vi.mock("../ExperimentData/ViewDetailsModal", () => ({
  default: ({ onAddFeedbackClick, data }) => (
    <div>
      <span>Evaluation detail</span>
      {onAddFeedbackClick && (
        <button onClick={() => onAddFeedbackClick(data)}>Add Feedback</button>
      )}
    </div>
  ),
}));
vi.mock("src/sections/common/DatapointCard", () => ({ default: () => null }));
vi.mock("src/components/custom-audio/AudioDatapointCard", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ImageDatapointCard", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ImagesDatapointCard", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/DocumentDatapointCard", () => ({
  default: () => null,
}));
vi.mock("../ExperimentData/AgentFlowRenderer", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: { result: { feedback: [] } } }),
  },
  endpoints: {
    develop: {
      experiment: {
        feedback: {
          getDetails: (id) =>
            `/experiments/${id}/feedback/get-feedback-details/`,
          create: (id) => `/experiments/${id}/feedback/`,
        },
      },
    },
  },
}));

import ExperimentDetailDrawerContent from "../ExperimentData/ExperimentDetailDrawerContent";

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});
it("opens scoped feedback from the classic evaluation detail", async () => {
  // J09 REVISED / 767: this working action must be reachable with the flag OFF.
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  expect(
    launchRedirect({
      pathname: "/dashboard/develop/experiment/experiment-a/data",
    }),
  ).toBeNull();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/experiment/experiment-a"]}>
        <Routes>
          <Route
            path="/experiment/:experimentId"
            element={
              <ExperimentDetailDrawerContent
                row={{
                  row_id: "snapshot-row",
                  "eval-column": { cell_value: "Pass" },
                }}
                columnConfig={[
                  {
                    id: "output",
                    name: "Output",
                    dataset_id: "snapshot",
                    group: {
                      id: "variant",
                      origin: "Experiment",
                      name: "Prompt",
                    },
                  },
                  {
                    id: "eval-column",
                    name: "Judge",
                    dataset_id: "snapshot",
                    origin_type: "evaluation",
                    group: { id: "metric", name: "Judge" },
                  },
                ]}
                setShowDiff={() => {}}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Inspect evaluation" }));
  expect(screen.getByText("Evaluation detail")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Add Feedback" }));
  expect(
    await screen.findByRole("dialog", { name: "Experiment feedback" }),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText("Feedback value", { exact: false }), {
    target: { value: "Pass" },
  });
  fireEvent.change(screen.getByLabelText("Explanation", { exact: false }), {
    target: { value: "Expected result" },
  });
  expect(screen.getByRole("button", { name: "Save feedback" })).toBeEnabled();
});

it("keeps the classic row Show Diff action enabled with NEW compare routes off", () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  const toggle = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ExperimentDetailDrawerContent
          row={{ row_id: "row-1" }}
          columnConfig={[]}
          showDiff={false}
          setShowDiff={vi.fn()}
          handleToggleDiff={toggle}
          showDiffModeButton
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const diff = screen.getByRole("button", { name: "Show Diff" });
  expect(diff).toBeEnabled();
  fireEvent.click(diff);
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(
    launchRedirect({ pathname: "/dashboard/develop/d1/compare/r1/r2" }),
  ).not.toBeNull();
});
