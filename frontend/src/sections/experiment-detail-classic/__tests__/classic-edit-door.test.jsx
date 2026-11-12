/* eslint-env node */
/* eslint-disable react/prop-types */
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("../DevelopExperimentSelect", () => ({ default: () => null }));
vi.mock("../experiment-context", () => ({
  useExperimentDetailContext: () => ({ experimentGridRef: null }),
}));
vi.mock("../ClassicRerunAction", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Experiment/common", () => ({
  useReRunExperiment: () => ({
    reRunExperiment: vi.fn(),
    isReRunningExperiment: false,
  }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  DevelopDetailContext: { Provider: ({ children }) => children },
}));
vi.mock("src/sections/develop-detail/Experiment/RunExperiment", () => ({
  default: function EditFormProbe() {
    const selected = useRunExperimentStore((s) => s.selectedExperiment);
    return selected ? (
      <div data-testid="classic-edit-form">{selected}</div>
    ) : null;
  },
}));
import ExperimentRow from "../ExperimentRow";
beforeEach(() => {
  useRunExperimentStore.getState().reset();
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
});
afterEach(() => {
  useRunExperimentStore.getState().reset();
  setLaunchOverrideForTest(null);
});
it("opens the legacy editor for the current experiment without enabling append-variant", () => {
  render(
    <MemoryRouter
      initialEntries={["/dashboard/develop/experiment/e1/data?datasetId=d1"]}
    >
      <Routes>
        <Route
          path="/dashboard/develop/experiment/:experimentId/data"
          element={<ExperimentRow />}
        />
      </Routes>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit and rerun" }));
  expect(screen.getByTestId("classic-edit-form")).toHaveTextContent("e1");
  expect(useRunExperimentStore.getState().mode).toBe("edit");
  const form = readFileSync(
    resolve(
      process.cwd(),
      "src/sections/develop-detail/Experiment/RunExperiment.jsx",
    ),
    "utf8",
  );
  // HTTP readback also uses the classic hydration path when enhancements are
  // on; the expanded condition must still admit every classic edit.
  expect(form).toContain(
    "if (!enhancementsShown || sourceDetail?.http_configs?.length)",
  );
  expect(form).toContain(
    "getExperimentDefaultValue(sourceDetail, experimentColumns ?? [])",
  );
  expect(form).toContain("update.mutate(body");
  const append = readFileSync(
    resolve(
      process.cwd(),
      "src/sections/experiment-detail-classic/ClassicRerunAction.jsx",
    ),
    "utf8",
  );
  expect(append).toContain('!isSurfaceShown("exp.rerun")');
});
