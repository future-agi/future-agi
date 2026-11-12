/* eslint-disable react/prop-types */
// The lineage ribbon above every tab (the experiment page and Compare runs)
// read the results matrix over its whole path for its hop numbers. An
// evaluation re-run sits on its source run's path but uses other
// evaluators, so the results endpoint refused that mix (409 "Runs require
// the same immutable version, trial plan and evaluator bindings; mixed
// comparison is not supported.") and the app toasted it on every tab. The
// ribbon now reads only the runs of its path that line up, a refused read
// shows no numbers instead of a toast, and the ribbon itself stays.
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
  records: [],
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: h.get },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...a) => h.enqueueSnackbar(...a),
}));
// R1 with lineage: the runs of the path, their parents and their groups.
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: { results: h.records }, held: false }),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import LineageRibbon from "../LineageRibbon";
import { handleError } from "src/utils/queryErrorHandler";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";
const MIXED = {
  statusCode: 409,
  result:
    "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.",
};
const run = (runId, label, parentId, group, day) => ({
  run_id: runId,
  label,
  parent_run_id: parentId,
  comparison_group: group,
  created_at: `2026-01-0${day}T00:00:00Z`,
});

let client;
const renderRibbon = (runIds, baselineId) =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={["/dashboard/develop/experiment/exp-1/data"]}
      >
        <LineageRibbon
          datasetId="ds-1"
          runIds={runIds}
          baselineId={baselineId}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
// The `runs` of every results read sent.
const reads = () => h.get.mock.calls.map(([, config]) => config?.params?.runs);
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

beforeEach(() => {
  h.get.mockReset();
  h.enqueueSnackbar.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    queryCache: new QueryCache({ onError: handleError }),
  });
});

describe("the lineage ribbon's hop numbers", () => {
  it("reads no runs that can't be lined up, and never toasts", async () => {
    // #2 re-ran #1's evaluation with other evaluators.
    h.records = [
      run(B, "#2", A, "plan-rerun", 2),
      run(A, "#1", null, "plan-source", 1),
    ];
    h.get.mockRejectedValue(MIXED);
    renderRibbon([B, A], B);
    await settle();
    expect(
      reads().filter((runs) => runs?.includes(A) && runs?.includes(B)),
    ).toEqual([]);
    expect(h.enqueueSnackbar).not.toHaveBeenCalled();
    // The ribbon, its runs and its links stay.
    expect(screen.getByTestId("lineage-ribbon")).toBeTruthy();
    expect(screen.getByText("#1")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Compare #2 with #1" }),
    ).toBeTruthy();
    expect(screen.getByText("Compare chain")).toBeTruthy();
  });

  it("reads the runs of the path that line up, without the re-run", async () => {
    // #2 iterates on #1 (same plan); #3 re-ran #2's evaluation.
    h.records = [
      run(C, "#3", B, "plan-rerun", 3),
      run(B, "#2", A, "plan-source", 2),
      run(A, "#1", null, "plan-source", 1),
    ];
    h.get.mockReturnValue(new Promise(() => {}));
    renderRibbon([C, B, A], C);
    await waitFor(() => expect(h.get).toHaveBeenCalled());
    await settle();
    expect(reads()).toEqual([`${A},${B}`]);
    expect(h.get.mock.calls[0][1].params.baseline).toBe(A);
  });

  it("shows no numbers and no toast when the server still refuses", async () => {
    h.records = [
      run(B, "#2", A, "plan-source", 2),
      run(A, "#1", null, "plan-source", 1),
    ];
    h.get.mockRejectedValue(MIXED);
    renderRibbon([B, A], B);
    await waitFor(() => expect(h.get).toHaveBeenCalled());
    await settle();
    expect(reads()).toEqual([`${A},${B}`]);
    expect(h.enqueueSnackbar).not.toHaveBeenCalled();
    expect(screen.getByTestId("lineage-ribbon")).toBeTruthy();
  });
});
