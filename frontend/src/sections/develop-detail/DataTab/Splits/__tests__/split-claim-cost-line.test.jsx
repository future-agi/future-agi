// The over-budget estimate offers the smallest split first. That offer runs
// the experiment on a split, so it follows the run scope claim
// (exp.new.splitScope), never the dataset page's split claim.
import React from "react";
import { isRealBackend } from "src/sections/develop/realBackendCaps";
import { isLaunchBuild } from "src/launch/launchSwitch";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { ExperimentFormContext } from "src/sections/develop-detail/Experiment/ExperimentFormContext";
import { emptyExperimentValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import { wireExperimentBody } from "src/sections/develop-detail/Experiment/serverBody";
import CostEstimateLine from "src/sections/develop-detail/Experiment/CostEstimateLine";

const mocks = vi.hoisted(() => ({
  smoke: vi.fn(({ splits }) => ({
    split: splits[0],
    text: `Try the ${splits[0].name} split first`,
  })),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: undefined }),
}));
vi.mock("src/sections/develop-detail/Experiment/useEngineCostInputs", () => ({
  useEngineCostInputs: (promptConfig) => ({
    promptConfig: promptConfig ?? [],
    customPrices: null,
  }),
}));
vi.mock(
  "src/sections/develop-detail/Experiment/costEstimateModel",
  async (importOriginal) => ({
    ...(await importOriginal()),
    costLine: () => ({
      text: "1,000 calls · about $12.00",
      overBudget: true,
      total: 12,
      note: null,
      rough: false,
    }),
    smokeSuggestion: mocks.smoke,
  }),
);

const SPLIT_ID = "4f9d2c1a-7b3e-4c5d-8e6f-0a1b2c3d4e5f";
const SPLITS = [{ id: SPLIT_ID, name: "smoke", row_count: 2 }];
const committed = () => [...madeRealSurfaceIds()];
const withoutRunScope = () => [
  ...committed().filter((id) => id !== "exp.new.splitScope"),
  "dataset.splits",
];
const state = (madeReal) => ({
  launch: true,
  experimentsBasic: true,
  madeReal,
});

afterEach(() => {
  setLaunchOverrideForTest(null);
  mocks.smoke.mockClear();
});

let formValues = null;
function Host() {
  const form = useForm({ defaultValues: emptyExperimentValues() });
  formValues = form.watch();
  return <CostEstimateLine control={form.control} setValue={form.setValue} />;
}

const costLineOnDataset = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ExperimentFormContext.Provider
        value={{
          datasetId: "d1",
          versions: [],
          exampleCount: 100,
          rows: [],
          columns: [],
          splits: SPLITS,
        }}
      >
        <Host />
      </ExperimentFormContext.Provider>
    </QueryClientProvider>,
  );

describe("over-budget estimate: the smaller split offer", () => {
  it("dataset.splits claimed without run scope: the estimate shows, no split offer", () => {
    expect(withoutRunScope()).toContain("dataset.splits");
    setLaunchOverrideForTest(state(withoutRunScope()));
    costLineOnDataset();
    expect(screen.getByTestId("cost-estimate")).toBeTruthy();
    expect(screen.getByText("1,000 calls · about $12.00")).toBeTruthy();
    expect(screen.queryByText("Try the smoke split first")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use it" })).toBeNull();
    expect(mocks.smoke).not.toHaveBeenCalled();
  });

  it("committed claims: Use it picks the split and the create body carries it", () => {
    // All-on F50: the default launch build makes the offer; no census opt-in.
    expect(committed()).toContain("exp.new.splitScope");
    setLaunchOverrideForTest(state(committed()));
    costLineOnDataset();
    expect(screen.getByText("Try the smoke split first")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use it" }));
    expect(formValues.splitIds).toEqual([SPLIT_ID]);
    const wire = wireExperimentBody(
      { split_ids: formValues.splitIds },
      {},
      false,
    );
    expect(wire.blocks).toEqual([]);
    expect(wire.body.split_ids).toEqual([SPLIT_ID]);
    expect(screen.queryByText("Try the smoke split first")).toBeNull();
  });

  it("default launch makes the smaller-split offer without changing backend mode", () => {
    setLaunchOverrideForTest(state(committed()));
    expect(committed()).toContain("exp.new.splitScope");
    expect(isLaunchBuild()).toBe(true);
    expect(isRealBackend()).toBe(true);
    costLineOnDataset();
    expect(screen.getByTestId("cost-estimate")).toBeTruthy();
    expect(screen.getByText("1,000 calls · about $12.00")).toBeTruthy();
    expect(screen.getByText("Try the smoke split first")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Use it" })).toBeTruthy();
    expect(formValues.splitIds).toEqual([]);
  });
});

beforeEach(() => vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false"));
afterEach(() => vi.unstubAllEnvs());
