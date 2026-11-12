/* eslint-env node */
/* eslint-disable react/prop-types */
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const applyGroup = vi.hoisted(() => vi.fn());
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: () => ({
    data: { id: "e1", dataset_id: "d1" },
    isLoading: false,
  }),
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("../ExperimentData/ExperimentDetailDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/PdfPreviewDrawer", () => ({ default: () => null }));
vi.mock("src/hooks/use-wavesurfer-cache", () => ({
  default: () => ({ clearWaveSurferCache: () => {} }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        status: true,
        result: { status: "Completed", column_config: [], table: [] },
      },
    }),
    post: vi.fn(),
  },
}));
// Keep the actual page and context. Replace only the heavy drawer body;
// exercise its real experiment-group handler below rather than a fake API.
vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: ({ open, module, id }) =>
    open ? (
      <div role="dialog" aria-label="Evaluations">
        <button onClick={() => applyGroup({ module, datasetId: id })}>
          Apply evaluation group
        </button>
      </div>
    ) : null,
}));

import ExperimentDataView from "../ExperimentData/ExperimentDataView";
import ExperimentDetailContextProvider from "../ExperimentContextProvider";

const clients = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  applyGroup.mockClear();
});

it("opens the experiment-scoped group drawer from the classic Data page with the flag OFF", () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/experiment/e1/data"]}>
          <Routes>
            <Route
              path="/experiment/:experimentId/data"
              element={
                <ExperimentDetailContextProvider>
                  <ExperimentDataView />
                </ExperimentDetailContextProvider>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Add evaluations" }));
  expect(screen.getByRole("dialog", { name: "Evaluations" })).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Apply evaluation group" }),
  );
  expect(applyGroup).toHaveBeenCalledWith({
    module: "experiment",
    datasetId: "d1",
  });
});

it("the real shared drawer group handler applies the experiment id, never the dataset id", async () => {
  const source = readFileSync(
    resolve(
      process.cwd(),
      "src/sections/common/EvaluationDrawer/EvaluationDrawer.jsx",
    ),
    "utf8",
  );
  const body = source.match(
    /const handleRun = (async \(data, handleSuccess, \{ isGrouped = false \} = \{\}\) => \{[\s\S]*?\n {2}\});/,
  );
  expect(body).not.toBeNull();
  const handleApplyGroup = vi.fn();
  const handleSuccess = vi.fn();
  const handleRun = runInNewContext(`(${body[1]})`, {
    module: "experiment",
    experimentId: "e1",
    id: "d1",
    handleApplyGroup,
  });
  const payload = { eval_group_id: "g1", filters: {} };
  await handleRun(payload, handleSuccess, { isGrouped: true });
  expect(handleApplyGroup).toHaveBeenCalledWith(
    { eval_group_id: "g1", filters: { experiment_id: "e1" } },
    expect.any(Object),
  );
  handleApplyGroup.mock.calls[0][1].onSuccess();
  expect(handleSuccess).toHaveBeenCalledOnce();
});
