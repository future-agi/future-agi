// W28 (walk flow #28): on a Completed experiment the Matrix named its runs by
// their ids, showed `—` in every cell and read `Running · 0 of 8 tries`. The
// runs' names come from the matrix read itself when R1 is held, a try that
// finished without a pass / fail rule is done (not running) and shows the
// row's score, and the status line reads Completed.
import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, renderHook, screen } from "src/utils/test-utils";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import MatrixCell from "../MatrixCell";
import useMatrixData, { matrixRunsById } from "../useMatrixData";
import {
  buildMatrixModel,
  cellScoreList,
  cellTrialKind,
  matrixStatusText,
  matrixTries,
  unscoredResultText,
} from "../matrixModel";

vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: vi.fn(),
  useRunConfigs: vi.fn(),
}));

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const METRIC = "33333333-3333-4333-8333-333333333333";

// R3 shape=matrix cell of a try that completed with a score but no pass /
// fail rule (results_matrix.trial_value -> null).
const doneCell = (status = "completed", value = 1) => ({
  trials: [null],
  trial_numbers: [1],
  result_ids: ["result-1"],
  cost_usd: [null],
  wall_ms: [12],
  trace_ids: [null],
  trace_summary: [{ status, trial_number: 1, carried: false }],
  failed_evals: [[]],
  carried: [false],
  scores: {
    [METRIC]:
      value == null
        ? { value: null, status: "not_assessed", n: 0 }
        : { value, status: "assessed", n: 1 },
  },
});

const completedMatrix = (cell = doneCell) => ({
  total: 4,
  rows: [0, 1, 2, 3].map((i) => ({
    row_id: `row-${i}`,
    label: String(i + 1),
    values: {},
    source: null,
    cells: { [B]: cell(), [A]: cell() },
  })),
  columns: {
    [B]: { trials: 1, status: "completed", trial_count_source: "immutable_plan" },
    [A]: { trials: 1, status: "completed", trial_count_source: "immutable_plan" },
  },
  summary: {
    runs: [
      {
        run_id: B,
        label: "#2",
        name: "Run B-gpt-4o-mini",
        status: "completed",
        progress: { status: "Completed", total: 4, done: 4 },
      },
      {
        run_id: A,
        label: "#1",
        name: "Run A-gpt-4o-mini",
        status: "completed",
        progress: { status: "Completed", total: 4, done: 4 },
      },
    ],
    metrics: [{ key: METRIC, name: "output-check", kind: "eval" }],
  },
});

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.clearAllMocks();
});

describe("W28 the Matrix of a completed experiment", () => {
  it("names the runs from the matrix read and is not running when R1 is held", () => {
    const matrix = completedMatrix();
    // A launch build holds R1 locally (launchHoldRoutes): the read fails.
    useRunConfigs.mockReturnValue({ data: undefined, isError: true });
    useMatrix.mockImplementation((params, options) =>
      options?.enabled === false
        ? { data: undefined, isLoading: false, isError: false }
        : { data: matrix, isLoading: false, isError: false, refetch: vi.fn() },
    );
    const { result } = renderHook(
      () => useMatrixData({ datasetId: "ds-1", runIds: [B, A], baselineId: null }),
      { wrapper: Wrapper },
    );
    expect(result.current.runsById[B]).toMatchObject({
      label: "#2",
      name: "Run B-gpt-4o-mini",
    });
    expect(result.current.runsById[A]).toMatchObject({
      label: "#1",
      name: "Run A-gpt-4o-mini",
    });
    expect(result.current.running).toBe(false);
  });

  it("keeps R1's richer run record over the matrix read's summary", () => {
    const matrix = completedMatrix();
    const byId = matrixRunsById(
      [{ run_id: B, label: "#2", name: "Run B-gpt-4o-mini", config: { model: { name: "m" } } }],
      matrix,
    );
    expect(byId[B]).toMatchObject({ label: "#2", config: { model: { name: "m" } } });
    expect(byId[A]).toMatchObject({ label: "#1", name: "Run A-gpt-4o-mini" });
    expect(matrixRunsById(undefined, null)).toEqual({});
  });

  it("counts finished tries as done and reads Completed, n of n", () => {
    const matrix = completedMatrix();
    expect(cellTrialKind(doneCell(), 0)).toBe("done");
    expect(cellTrialKind(doneCell("queued"), 0)).toBe("pending");
    expect(cellTrialKind(doneCell("not_reached"), 0)).toBe("skipped");
    expect(cellTrialKind({ trials: [null] }, 0)).toBe("pending");
    expect(cellTrialKind({ trials: [1] }, 0)).toBe("pass");
    const tries = matrixTries(matrix, [B, A]);
    expect(tries).toEqual({ done: 8, total: 8 });
    expect(
      matrixStatusText({
        running: false,
        ...tries,
        statuses: [B, A].map((id) => matrix.columns[id].status),
      }),
    ).toEqual({ running: false, text: "Completed · 8 of 8 tries" });
    expect(
      matrixStatusText({ running: true, done: 7, total: 8, statuses: ["running"] }),
    ).toEqual({ running: true, text: "Running · 7 of 8 tries" });
    expect(
      matrixStatusText({ running: false, done: 8, total: 8, statuses: ["failed"] }),
    ).toBeNull();
  });

  it("names each row's score by its eval", () => {
    const model = buildMatrixModel(completedMatrix(), { runIds: [B, A] });
    expect(model.metricNames).toEqual({ [METRIC]: "output-check" });
    expect(cellScoreList(doneCell(), model.metricNames)).toEqual([
      { key: METRIC, name: "output-check", value: 1, text: "1" },
    ]);
    expect(cellScoreList(doneCell("completed", 0.5), {})[0]).toMatchObject({
      name: "Score",
      text: "0.50",
    });
    expect(unscoredResultText(doneCell(), model.metricNames)).toBe(
      "output-check 1",
    );
    expect(unscoredResultText(doneCell("completed", null), {})).toBe(
      "No pass/fail result",
    );
    expect(unscoredResultText(doneCell("queued", null), {})).toBeNull();
  });

  it("shows the row's score in a finished cell, not a running dot and —", () => {
    const { container } = render(
      <MatrixCell
        cell={doneCell()}
        runLabel="#2"
        rowLabel="1"
        metricNames={{ [METRIC]: "output-check" }}
        dense
      />,
    );
    const cell = screen.getByRole("gridcell", { name: "#2 1: output-check 1" });
    expect(cell).toHaveTextContent("1");
    expect(cell).not.toHaveTextContent("—");
    expect(container.querySelector('[data-trial="done"]')).not.toBeNull();
    expect(container.querySelector('[data-trial="pending"]')).toBeNull();
  });
});
