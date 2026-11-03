// Regression test for the experiment snake_case hotfix: the row drawer's
// Difference view. Uses the real DatapointCard / GenerateDiffText.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  AGENT_NODE_ANSWER_COLUMN_ID,
  AGENT_NODE_PLAN_COLUMN_ID,
  BASE_COLUMN_ID,
  OUTPUT_A_COLUMN_ID,
  ROW_1_ID,
  agentColumnConfig,
  llmColumnConfig,
  rowDiffResponse,
  rowsPageResponse,
} from "./experiment-snake-case.fixtures";

vi.mock("react-router", () => ({
  useParams: () => ({ experimentId: "6f1c2a5e-8d3b-4c71-9a2e-1b5d7c9e0a11" }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock(
  "src/sections/develop-detail/DataTab/AddEvaluationFeeback/AddEvaluationFeeback",
  () => ({ default: () => null }),
);

import ExperimentDetailDrawerContent from "../ExperimentData/ExperimentDetailDrawerContent";

const renderContent = (row, columnConfig) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ExperimentDetailDrawerContent
        onClose={vi.fn()}
        row={row}
        columnConfig={columnConfig}
        showDiff
        setShowDiff={vi.fn()}
        handleToggleDiff={vi.fn()}
        handleFetchNextRow={vi.fn()}
        handleFetchPrevRow={vi.fn()}
        isPending={false}
        refreshGrid={vi.fn()}
        diffMode={false}
        showDiffModeButton
      />
    </QueryClientProvider>,
  );

describe("Row drawer Difference view reads the row-diff API's snake_case diff", () => {
  it("D13: renders the word diff from `cell_diff_value` once the drawer's Show Diff is on", async () => {
    // What ExperimentDetailDrawer holds after a successful row-diff call:
    // the grid row merged with the row-diff result for that row
    // (ExperimentDetailDrawer.jsx updateDiffData onSuccess). The row-diff
    // response carries cell_value plus cell_diff_value, a list of
    // {text, status} words (views/experiments.py:1886-1898,
    // model_hub/utils/utils.py:566-606).
    const gridRow = rowsPageResponse(llmColumnConfig).result.table[0];
    const row = {
      ...gridRow,
      ...rowDiffResponse.result[ROW_1_ID],
      rowId: ROW_1_ID,
      index: 0,
    };
    expect(row[OUTPUT_A_COLUMN_ID].cell_diff_value).toBeDefined();

    renderContent(row, llmColumnConfig);

    // The Difference tab of variant A shows the removed and the added word.
    await waitFor(() =>
      expect(screen.getByText("October")).toBeInTheDocument(),
    );
    expect(screen.getByText("Oct")).toBeInTheDocument();
  });

  it("D13 (agent variant): renders the final node's word diff from `cell_diff_value`", async () => {
    const diffCell = (value, words) => ({
      cell_value: value,
      cell_diff_value: words,
      status: "pass",
      value_infos: {},
    });
    const row = {
      row_id: ROW_1_ID,
      rowId: ROW_1_ID,
      index: 0,
      [BASE_COLUMN_ID]: {
        cell_value: "Your refund was issued on Oct 2.",
        cell_diff_value: null,
        status: "pass",
        value_infos: {},
      },
      [AGENT_NODE_PLAN_COLUMN_ID]: diffCell("Look up the refund.", [
        { text: "Look", status: "added" },
      ]),
      [AGENT_NODE_ANSWER_COLUMN_ID]: diffCell(
        "Your refund was issued on October 2.",
        [
          { text: "Your", status: "default" },
          { text: "Oct", status: "removed" },
          { text: "October", status: "added" },
        ],
      ),
    };

    renderContent(row, agentColumnConfig);

    await waitFor(() =>
      expect(screen.getByText("October")).toBeInTheDocument(),
    );
  });
});
