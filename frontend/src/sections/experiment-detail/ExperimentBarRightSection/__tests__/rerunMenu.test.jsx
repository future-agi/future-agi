// IT-R3 G6 P2: the Rerun menu anchors to the visible button at 1280 px (it
// used to open at the window's top-left corner: the ref sat on a button the
// tooltip remounted), disabled items carry a reason and no strikethrough,
// `Failed tasks only` counts check failures apart from errors, and Back goes
// to the parent page.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  failedItemText,
  planLine,
  rerunPlan,
  runsWhereText,
} from "../rerunPlan";

const RUNS = [
  {
    run_id: "ra",
    label: "#2A",
    name: "Prompt",
    concurrency: 2,
    progress: { total: 3, done: 3, errors: 0 },
  },
  {
    run_id: "rb",
    label: "#2B",
    name: "Agent",
    concurrency: 1,
    progress: { total: 3, done: 3, errors: 0 },
  },
];
const MATRIX = {
  rows: [
    {
      row_id: "t1",
      cells: {
        ra: { trials: [1, 0], cost_usd: [0.1, 0.1], wall_ms: [60000, 60000] },
        rb: { trials: [1, 1], cost_usd: [0.2, 0.2], wall_ms: [120000, 120000] },
      },
    },
    {
      row_id: "t2",
      cells: {
        ra: { trials: [1, 1], cost_usd: [0.1, 0.1], wall_ms: [60000, 60000] },
        rb: { trials: [0, 0], cost_usd: [0.2, 0.2], wall_ms: [120000, 120000] },
      },
    },
    {
      row_id: "t3",
      cells: {
        ra: { trials: ["error", "error"], cost_usd: [0, 0], wall_ms: [0, 0] },
        rb: { trials: [1, 1], cost_usd: [0.2, 0.2], wall_ms: [120000, 120000] },
      },
    },
  ],
};

vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({
    data: {
      id: "e2",
      ordinal: 2,
      name: "Five ways",
      dataset_id: "ds",
      runs: RUNS,
    },
  }),
  useDatasetRuns: () => ({ data: { pinned_run_id: "rb", results: RUNS } }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Support ops tasks" }],
  }),
}));
vi.mock("src/api/develop/r2", () => ({
  useMatrix: () => ({ data: MATRIX }),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "task_suite" }),
}));
vi.mock("../../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../../DevelopExperimentSelect", () => ({
  default: () => <span>#2 Five ways</span>,
  experimentStatusOf: () => null,
}));

import ExperimentRow from "../../ExperimentRow";

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{`${loc.pathname}${loc.search}`}</span>;
}

function renderRow(entry) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={<ExperimentRow />}
          />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("rerunPlan (pure)", () => {
  it("counts check failures per run apart from errors, with cost / time", () => {
    const plan = rerunPlan(MATRIX, RUNS);
    expect(plan.failed.rows).toBe(2);
    expect(plan.failed.errored).toBe(1);
    expect(plan.all.cost).toBeCloseTo(1.6, 5);
    // #2B: 6 tries × 2 min, one at a time = 12 min (the slowest run).
    expect(Math.round(plan.all.ms / 60000)).toBe(12);
    const item = failedItemText(plan, "task");
    // IT-R5 G6 P5: one vocabulary with the dialog: `Failed tasks (N)` and
    // the one estimate (rows · tries · cost · time), then per run.
    expect(item.primary).toBe("Failed tasks (3)");
    expect(item.secondary).toBe(
      "3 tasks · 6 tries · ~$0.6666666666666667 · ~4 min · #2A 2 · #2B 1 · 1 errored",
    );
    expect(
      planLine({
        cost: 0.57,
        ms: 6 * 60000,
        places: "3 on alice@mbp-14, 1 in CI",
      }),
    ).toBe("≈ $0.57 · about 6 min · 3 on alice@mbp-14, 1 in CI");
  });

  it("disables with a reason when nothing failed", () => {
    const item = failedItemText(rerunPlan({ rows: [] }, RUNS), "task");
    expect(item.enabled).toBe(false);
    expect(item.secondary).toBe(
      "Not available: no task failed a check · errored tasks: 0",
    );
  });

  it("names where the runs execute", () => {
    const local = (label) => ({
      run_id: label,
      label,
      config: { execution: { mode: "local", label: "This machine" } },
    });
    expect(runsWhereText([local("#4A"), local("#4B"), local("#4C")])).toBe(
      "3 runs · this machine",
    );
    expect(runsWhereText([local("#4A")])).toBeNull();
  });
});

describe("ExperimentRow header at 1280 px", () => {
  it("anchors the Rerun menu to the visible button and keeps reasons readable", async () => {
    window.innerWidth = 1280;
    renderRow(
      "/dashboard/develop/experiment/e2/data?datasetId=ds&from=%2Fdashboard%2Fdevelop%2Fds%3Ftab%3Dexperiments%26sort%3Dbest",
    );
    const anchor = screen.getByTestId("exp-rerun-anchor");
    const button = screen.getByTestId("exp-rerun");
    expect(anchor.contains(button)).toBe(true);
    fireEvent.click(button);
    const menu = await screen.findByTestId("exp-rerun-menu");
    // The anchor the menu reads is still in the page after the tooltip
    // swapped its child (the bug: a detached node → menu at (16, 20)).
    expect(document.body.contains(screen.getByTestId("exp-rerun-anchor"))).toBe(
      true,
    );
    expect(
      screen
        .getByTestId("exp-rerun-anchor")
        .contains(screen.getByTestId("exp-rerun")),
    ).toBe(true);
    expect(menu.textContent).toContain("Failed tasks (3)");
    expect(menu.textContent).toContain("1 errored");
    expect(menu.textContent).toContain("Rerun with changes…");
    const struck = [...menu.querySelectorAll("*")].filter((el) =>
      window.getComputedStyle(el).textDecoration.includes("line-through"),
    );
    expect(struck).toHaveLength(0);
  });

  it("Back goes to the page that opened it (url.from), never history -1", async () => {
    renderRow(
      "/dashboard/develop/experiment/e2/data?datasetId=ds&from=%2Fdashboard%2Fdevelop%2Fds%3Ftab%3Dexperiments%26sort%3Dbest",
    );
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() =>
      expect(screen.getByTestId("where").textContent).toBe(
        "/dashboard/develop/ds?tab=experiments&sort=best",
      ),
    );
  });
});
