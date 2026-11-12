/* eslint-disable react/prop-types */
// L3: what dev's Data page shows in the launch build. Real and kept:
// Display. Hidden (post-launch items): Summary tab, Edit (wizard), Add
// Evaluations, Configure Eval, Edit Column Name, the column Re run dialog, the
// eval detail's Add Feedback, and Download (dev's CSV has none of a V2
// experiment's outputs or scores). Show Diff is dev's code, unchanged, and is
// not a launch option: it reads camelCase fields the V2 detail sends in
// snake_case, so on the real stack it never shows (as on dev).
import React from "react";
import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: http };
});
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

import ExperimentBarDataRightSection from "../ExperimentBarRightSection/ExperimentBarDataRightSection";
import ExperimentBar from "../ExperimentBar";
import ExperimentDetailContextProvider from "../ExperimentContextProvider";
import { CustomExperimentColumnHeader } from "../ExperimentData/CustomExperimentColumnHeader";

const EXP = "44444444-4444-4444-8444-444444444444";

const wrap = (ui, path = `/dashboard/develop/experiment/${EXP}/data`) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={
              <ExperimentDetailContextProvider>
                {ui}
              </ExperimentDetailContextProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const source = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

describe("L3 classic Data page actions", () => {
  afterEach(() => setLaunchOverrideForTest(null));
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    http.get.mockReset();
    http.post.mockReset();
  });

  // J09 REVISED / 767 restores pre-existing evaluation-group application.
  it("shows Display and Add Evaluations; no Download or Edit", () => {
    const onAddEvaluations = vi.fn();
    wrap(
      <ExperimentBarDataRightSection
        outputFormat="string"
        gridApiRef={{ current: null }}
        experimentData={{}}
        onAddEvaluations={onAddEvaluations}
      />,
    );
    expect(screen.getByText("Display")).toBeTruthy();
    expect(screen.queryByTestId("classic-experiment-download")).toBe(null);
    expect(screen.queryByLabelText(/download/i)).toBe(null);
    expect(screen.queryByText("Edit")).toBe(null);
    fireEvent.click(screen.getByRole("button", { name: "Add evaluations" }));
    expect(onAddEvaluations).toHaveBeenCalledOnce();
    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("keeps the pre-existing Data and Summary tabs visible", () => {
    wrap(<ExperimentBar />);
    expect(screen.getByRole("tab", { name: /Data/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Summary/ })).toBeVisible();
  });

  it("an experiment column header has no Re run button", () => {
    render(
      <CustomExperimentColumnHeader
        displayName="gpt-4o"
        showColumnMenu={vi.fn()}
        col={{ originType: "experiment", sourceId: "s1", status: "Completed" }}
        hideMenu
      />,
    );
    expect(screen.queryByText("Re run")).toBe(null);
  });

  it("the Data view mounts no edit / add-eval / column-rerun surface", () => {
    const view = source("../ExperimentData/ExperimentDataView.jsx");
    for (const gone of [
      "<EditEvaluation",
      "<EditColumnName",
      "<RenderCellRunningOptions",
      "Evaluation/EditEvaluation",
      "DataTab/EditColumnName",
      '"Configure Eval"',
      '"Edit Column Name"',
    ]) {
      expect(view.includes(gone), gone).toBe(false);
    }
    // J09: the shared drawer keeps its existing experiment-scoped group handler.
    expect(view).toContain("<EvaluationDrawer");
    expect(view).toContain('module="experiment"');
    expect(view).toContain("setEvaluateOpen");
    // Kept: the reasoning toggle and the failed-cell re-run.
    expect(view.includes('"Show Reasoning"')).toBe(true);
    expect(view.includes("reRunExperimentCell")).toBe(true);
    const drawer = source(
      "../ExperimentData/ExperimentDetailDrawerContent.jsx",
    );
    expect(drawer.includes("AddEvaluationFeeback")).toBe(false);
    expect(drawer.includes("onAddFeedbackClick")).toBe(true);
    expect(drawer.includes("<ExperimentFeedbackDialog")).toBe(true);
    const feedback = source("../ExperimentFeedbackDialog.jsx");
    expect(feedback.includes("routes.create(experimentId)")).toBe(true);
    expect(feedback.includes("routes.submit")).toBe(false);
    const bar = source(
      "../ExperimentBarRightSection/ExperimentBarDataRightSection.jsx",
    );
    expect(bar.includes("RunExperiment")).toBe(false);
    expect(bar.includes("initiateEditMode")).toBe(false);
    expect(bar.includes("downloadExperiment")).toBe(false);
  });

  it("the failed-cell re-run body matches the strict rerun-cells contract", () => {
    const view = source("../ExperimentData/ExperimentDataView.jsx");
    expect(view).toMatch(
      /cells: \[\{ column_id: columnId, row_id: rowId \}\],\s*failed_only: true,/,
    );
    const ok = validateContractedRequestConfig({
      url: `/model-hub/experiments/v2/${EXP}/rerun-cells/`,
      method: "post",
      data: {
        cells: [
          {
            column_id: "55555555-5555-4555-8555-555555555555",
            row_id: "66666666-6666-4666-8666-666666666666",
          },
        ],
        failed_only: true,
      },
    });
    expect(ok.skipped).toBeFalsy();
    expect(ok.ok).toBe(true);
  });

  it("the rows read sends only the rows view's query keys", () => {
    for (const params of [
      { current_page_index: 0, page_size: 30, get_diff: false, search: "" },
      { current_page_index: 1, page_size: 30, get_diff: true },
    ]) {
      const r = validateContractedRequestConfig({
        url: `/model-hub/experiments/v2/${EXP}/rows/`,
        method: "get",
        params,
      });
      expect(r.ok, JSON.stringify(r.error?.details ?? null)).toBe(true);
    }
    const detail = validateContractedRequestConfig({
      url: `/model-hub/experiments/v2/${EXP}/rows/`,
      method: "get",
      params: { column_config_only: true },
    });
    expect(detail.ok).toBe(true);
  });
});
