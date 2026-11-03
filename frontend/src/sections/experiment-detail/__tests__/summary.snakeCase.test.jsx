// Regression tests for the experiment snake_case hotfix: Summary tab.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import {
  EDT_A_ID,
  EXPERIMENT_ID,
  statsResponse,
} from "./experiment-snake-case.fixtures";

const captured = vi.hoisted(() => ({ columns: null, rows: null }));
const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("react-router", () => ({
  useParams: () => ({
    experimentId: "6f1c2a5e-8d3b-4c71-9a2e-1b5d7c9e0a11",
    individualExperimentId: "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a91",
  }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    develop: {
      individualExperimentDataset: (id) =>
        `/model-hub/develops/${id}/get-experiment-dataset-table/`,
      experiment: {
        getSummary: (id) => `/model-hub/experiments/v2/${id}/stats/`,
      },
    },
  },
}));
// The table component is replaced by a probe that keeps the columns and rows
// the Summary view hands to AG Grid, so the test reads exactly what AG Grid
// would read (colDef.field on each row, then colDef.valueFormatter).
vi.mock("../ExperimentSummary/ExperimentSummaryTable", () => ({
  default: ({ columns, rows }) => {
    captured.columns = columns;
    captured.rows = rows;
    return null;
  },
}));
vi.mock("../ExperimentSummary/ExperimentEvaluationChart", () => ({
  default: () => null,
}));
vi.mock(
  "../ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment",
  () => ({
    default: () => null,
  }),
);

import ExperimentSummaryView from "../ExperimentSummary/ExperimentSummaryView";
import IndividualExperimentSummaryView from "../ExperimentSummary/IndividualExperimentSummaryView";
import ExperimentEvalCellRenderer from "../ExperimentSummary/CustomRenderers/ExperimentEvalCellRenderer";

const displayed = (colDef, row) => {
  const value = row[colDef.field];
  return colDef.valueFormatter ? colDef.valueFormatter({ value }) : value;
};

const renderWithClient = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );

describe("Summary tab reads the stats endpoint's snake_case keys", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    captured.columns = null;
    captured.rows = null;
  });

  it("D8: shows each variant's name, latency (ms) and token totals", async () => {
    // views/experiments.py:2505-2513 builds table_data rows with
    // experiment_dataset_name / average_response_time / prompt_token /
    // completion_token / total_token. average_response_time is the mean of the
    // cells' metadata.response_time, which the run-prompt handler records in
    // milliseconds (agentic_eval/.../runprompt_handlers/utils/response_formatter.py:145).
    axiosGetMock.mockResolvedValue({ data: statsResponse() });

    renderWithClient(<ExperimentSummaryView />);
    await waitFor(() => expect(captured.rows).toHaveLength(2));

    const byHeader = Object.fromEntries(
      captured.columns.map((c) => [c.headerName, c]),
    );
    const variantA = captured.rows.find((r) => r.dataset_id === EDT_A_ID);

    expect(displayed(byHeader["Experiment Name"], variantA)).toBe(
      "support-reply-gpt-4o",
    );
    expect(byHeader["Average Response time (ms)"]).toBeDefined();
    expect(displayed(byHeader["Average Response time (ms)"], variantA)).toBe(
      "1234.6 ms",
    );
    expect(displayed(byHeader["Prompt Tokens"], variantA)).toBe(18420);
    expect(displayed(byHeader["Completion Tokens"], variantA)).toBe(6210);
    expect(displayed(byHeader["Total Tokens"], variantA)).toBe(24630);
  });

  it("D9: tints a reverse_output eval by its inverted score (90% on a lower-is-better eval is bad)", () => {
    // views/experiments.py:2636-2644: stats column_config carries reverse_output
    const eGridCell = { style: { backgroundColor: "" } };
    const colDef = {
      col: { name: "toxicity", reverse_output: true, output_type: "score" },
    };

    render(
      <ExperimentEvalCellRenderer
        value={90}
        eGridCell={eGridCell}
        colDef={colDef}
      />,
    );

    expect(eGridCell.style.backgroundColor).toBe(
      interpolateColorBasedOnScore(90, 100, true),
    );
    expect(eGridCell.style.backgroundColor).toBe("var(--score-red-light)");
  });

  it("D10: individual-experiment Summary shows the Ranking column when `is_winner_chosen`", async () => {
    // views/experiments.py:2684: metadata: {"is_winner_chosen": ...}
    axiosGetMock.mockImplementation((url) =>
      Promise.resolve(
        url.includes("/stats/")
          ? { data: statsResponse({ winnerChosen: true }) }
          : {
              data: {
                status: true,
                result: { metadata: { experiment_id: EXPERIMENT_ID } },
              },
            },
      ),
    );

    renderWithClient(<IndividualExperimentSummaryView />);
    await waitFor(() => expect(captured.rows).toHaveLength(2));

    expect(captured.columns.map((c) => c.headerName)).toContain("Ranking");
  });
});
