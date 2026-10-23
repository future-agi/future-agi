// Regression tests for the experiment snake_case hotfix: row detail drawer.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  EXPERIMENT_ID,
  OUTPUT_A_COLUMN_ID,
  OUTPUT_B_COLUMN_ID,
  ROW_1_ID,
  llmColumnConfig,
  rowDiffResponse,
  rowsPageResponse,
} from "./experiment-snake-case.fixtures";

const axiosPostMock = vi.hoisted(() => vi.fn());
const copyMock = vi.hoisted(() => vi.fn());

vi.mock("react-router", () => ({
  useParams: () => ({ experimentId: EXPERIMENT_ID }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: axiosPostMock },
  endpoints: {
    develop: {
      getRowsDiff: "/model-hub/experiments/v2/row-diff/",
      experiment: {
        rowDetail: (experimentId, rowId) =>
          `/model-hub/experiments/${experimentId}/${rowId}/`,
      },
    },
  },
}));
vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard: copyMock,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/sections/common/DatapointCard", () => ({ default: () => null }));
vi.mock("src/components/svg-color/svg-color", () => ({
  default: ({ alt, onClick }) => (
    <button type="button" aria-label={alt} onClick={onClick} />
  ),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/AddEvaluationFeeback/AddEvaluationFeeback",
  () => ({ default: () => null }),
);

import ExperimentDetailDrawerContent from "../ExperimentData/ExperimentDetailDrawerContent";
import ExperimentDetailDrawer from "../ExperimentData/ExperimentDetailDrawer";

// A grid-origin row exactly as AG Grid hands it to the drawer: the rows
// endpoint's table entry (views/experiments.py:1585-1603, keyed by `row_id`)
// plus the row index ExperimentDataView adds (ExperimentDataView.jsx:776-779).
const gridRow = {
  ...rowsPageResponse(llmColumnConfig).result.table[0],
  index: 0,
};

const withClient = (ui) => (
  <QueryClientProvider
    client={
      new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      })
    }
  >
    {ui}
  </QueryClientProvider>
);

describe("Row drawer reads the grid row's snake_case `row_id`", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    copyMock.mockReset();
  });

  it("D11: shows the 'Row ID' chip for a grid row and copies its row_id", () => {
    render(
      withClient(
        <ExperimentDetailDrawerContent
          onClose={vi.fn()}
          row={gridRow}
          columnConfig={llmColumnConfig}
          showDiff={false}
          setShowDiff={vi.fn()}
          handleToggleDiff={vi.fn()}
          handleFetchNextRow={vi.fn()}
          handleFetchPrevRow={vi.fn()}
          isPending={false}
          refreshGrid={vi.fn()}
          diffMode={false}
        />,
      ),
    );

    expect(screen.getByText(`Row ID: ${ROW_1_ID}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(copyMock).toHaveBeenCalledWith(ROW_1_ID);
  });

  it("D12: in diff mode, requests row-diff for the open row with a plain experiment id and the experiment output columns", async () => {
    // Request contract: DatasetRowDiffRequestSerializer
    // (serializers/contracts.py:3325-3329): experiment_id UUID, column_ids,
    // row_ids, compare_column_ids (all UUID lists). Experiment output columns
    // are origin_type "experiment" (views/experiments.py:1450-1453).
    axiosPostMock.mockResolvedValue({ data: rowDiffResponse });

    render(
      withClient(
        <ExperimentDetailDrawer
          open
          onClose={vi.fn()}
          row={gridRow}
          setExpandRow={vi.fn()}
          allRows={[{ rowIndex: 0, data: gridRow }]}
          setAllRows={vi.fn()}
          totalCount={2}
          columnConfig={llmColumnConfig}
          refreshGrid={vi.fn()}
          diffMode
          showDiffModeButton
        />,
      ),
    );

    await waitFor(() => expect(axiosPostMock).toHaveBeenCalled());
    const [url, body] = axiosPostMock.mock.calls[0];
    expect(url).toBe("/model-hub/experiments/v2/row-diff/");
    expect(body).toEqual({
      experiment_id: EXPERIMENT_ID,
      column_ids: llmColumnConfig.map((c) => c.id),
      compare_column_ids: [OUTPUT_A_COLUMN_ID, OUTPUT_B_COLUMN_ID],
      row_ids: [ROW_1_ID],
    });
  });
});
