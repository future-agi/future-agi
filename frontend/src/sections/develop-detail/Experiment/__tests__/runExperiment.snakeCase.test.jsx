// Regression tests for the experiment snake_case hotfix: Run Experiment drawer.
// The payloads are the backend's own snake_case shapes; see
// src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures.js.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useWatch } from "react-hook-form";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import NewExperimentButton from "../NewExperimentButton";
import { EXPERIMENT_STEP, experimentCreationSteps } from "../constants";
import {
  DATASET_ID,
  suggestNameResponse,
  validateNameTakenResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());
// The show-unreleased switch as the product modules read it. On by default
// (the release's state); one test turns it off with the override.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
vi.mock("src/auth/hooks", () => ({
  // Match the app's case-sensitive RolePermission key, not an unknown role.
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: true }, reason: () => null }),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: axiosGetMock,
      post: vi.fn(),
      put: vi.fn(),
    },
  };
});

vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
  useDatasetColumnConfig: () => [],
  useDatasetDerivedVariables: () => ({ data: {} }),
  useGetJsonColumnSchema: () => ({ data: {} }),
  useColumnInfo: () => ({ data: [], isLoading: false }),
}));
vi.mock("src/api/experiment/use-experiment-derived-variables", () => ({
  useExperimentDerivedVariables: () => ({ data: {} }),
}));
vi.mock("src/api/experiment/use-get-exp-jsonschema", () => ({
  useGetExperimentJSONSchema: () => ({ data: {} }),
}));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/sections/develop-detail/Experiment/StepperForExperiment", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepExperimentCreation",
  () => ({
    default: function NameProbe({ control }) {
      const name = useWatch({ control, name: "name" });
      return <output aria-label="experiment name">{name}</output>;
    },
  }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/EvaluationStepExperimentCreation",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/BasicStepExperimentCreation",
  () => ({
    default: function NameProbe({ control }) {
      const name = useWatch({ control, name: "name" });
      return <output aria-label="experiment name">{name}</output>;
    },
  }),
);

vi.mock("../StepperComponentExperiment/ReviewStep", () => ({
  default: () => <div data-testid="new-review-step" />,
}));
vi.mock("../CostEstimateLine", () => ({
  SimpleEstimate: () => <div data-testid="new-cost-estimate" />,
}));
vi.mock("../EstimateDetails", () => ({
  useEstimateDetails: () => ({}),
  EstimateSummary: () => <div data-testid="new-estimate-summary" />,
  EstimateBreakdown: () => <div data-testid="new-estimate-breakdown" />,
}));
vi.mock("../RunTray", () => ({
  RunTrayDock: () => <div data-testid="new-run-tray-dock" />,
}));

import RunExperiment from "src/sections/develop-detail/Experiment/RunExperiment";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { useExperimentNameCheck } from "src/api/develop/experiment-create";
import { configurationIssues } from "src/sections/develop-detail/Experiment/stepIssues";

afterEach(() => setLaunchOverrideForTest(null));
afterEach(() => {
  unreleased.shown = true;
});

describe("Run Experiment drawer reads the backend's snake_case keys", () => {
  it("J09: the launch-default New experiment button opens the real prompt drawer", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    // The launch default shows every registered surface, so with
    // experimentsBasic off the New experiment enhancements, the kinds menu
    // and the run tray still show.
    expect(isSurfaceShown("exp.classic")).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(isSurfaceShown("dataset.tab.experiments")).toBe(true);
    axiosGetMock.mockResolvedValue({ data: suggestNameResponse });
    useRunExperimentStore.setState({
      openRunExperiment: false,
      selectedExperiment: null,
      duplicateFromExperimentId: null,
      currentStep: 0,
      prefill: null,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET_ID}`]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={
                <>
                  <NewExperimentButton />
                  <RunExperiment />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const button = screen.getByRole("button", { name: "New experiment" });
    expect(button).toBeEnabled();
    // The split button's menu toggle sits next to the main button.
    expect(screen.getByTestId("new-experiment-kinds")).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByLabelText("experiment name")).toHaveTextContent(
        "support-tickets-2026-10-22-v2",
      ),
    );
    expect(useRunExperimentStore.getState().openRunExperiment).toBe(true);
    // The run tray docks in the drawer header while the drawer is open.
    expect(screen.getByTestId("new-run-tray-dock")).toBeInTheDocument();

    // Review & run is the last step. Each step before it moves on with
    // Continue, and Run experiment, the review and the estimate wait for the
    // last step. The Data step's body is not stubbed in this file, so the
    // walk checks the steps around it.
    const lastStep = experimentCreationSteps.length - 1;
    expect(lastStep).toBe(EXPERIMENT_STEP.REVIEW);
    const reviewParts = [
      "new-review-step",
      "new-estimate-summary",
      "new-estimate-breakdown",
    ];
    for (const step of [
      EXPERIMENT_STEP.CONFIGURATION,
      EXPERIMENT_STEP.EVALUATIONS,
    ]) {
      act(() => useRunExperimentStore.getState().setCurrentStep(step));
      expect(screen.getByTestId("wizard-continue")).toHaveTextContent(
        "Continue",
      );
      expect(
        screen.queryByRole("button", { name: /Run experiment/ }),
      ).toBeNull();
      for (const id of reviewParts) {
        expect(screen.queryByTestId(id)).toBeNull();
      }
    }
    act(() => useRunExperimentStore.getState().setCurrentStep(lastStep));
    expect(
      screen.getByRole("button", { name: /Run experiment/ }),
    ).toBeVisible();
    expect(screen.queryByTestId("wizard-continue")).toBeNull();
    for (const id of reviewParts) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    client.clear();
  });
  it("with the show-unreleased switch off, New experiment opens the classic prompt drawer", async () => {
    // Switch off in the override and in the export the product modules read.
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    // The gates decide again: with experimentsBasic off the classic door
    // stays, and the enhancements, the kinds menu and the run tray are hidden.
    expect(isSurfaceShown("exp.classic")).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(false);
    expect(isSurfaceShown("dataset.tab.experiments")).toBe(false);
    axiosGetMock.mockResolvedValue({ data: suggestNameResponse });
    useRunExperimentStore.setState({
      openRunExperiment: false,
      selectedExperiment: null,
      duplicateFromExperimentId: null,
      currentStep: 0,
      prefill: null,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET_ID}`]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={
                <>
                  <NewExperimentButton />
                  <RunExperiment />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const button = screen.getByRole("button", { name: "New experiment" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByLabelText("experiment name")).toHaveTextContent(
        "support-tickets-2026-10-22-v2",
      ),
    );
    expect(useRunExperimentStore.getState().openRunExperiment).toBe(true);
    expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
    expect(screen.queryByTestId("new-run-tray-dock")).toBeNull();
    // Without the Review & run step, Evaluations is the last step: it runs.
    const lastStep = EXPERIMENT_STEP.EVALUATIONS;
    act(() => useRunExperimentStore.getState().setCurrentStep(lastStep));
    expect(
      screen.getByRole("button", { name: /Run experiment/ }),
    ).toBeVisible();
    expect(screen.queryByTestId("wizard-continue")).toBeNull();
    for (const id of [
      "new-review-step",
      "new-estimate-summary",
      "new-estimate-breakdown",
    ]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    client.clear();
  });
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  it("D1: prefills the name from suggest-name's `suggested_name`", async () => {
    // views/experiments.py:6324 returns {"suggested_name": ...}
    axiosGetMock.mockResolvedValue({ data: suggestNameResponse });
    useRunExperimentStore.setState({
      openRunExperiment: true,
      selectedExperiment: null,
      currentStep: 0,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET_ID}`]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={<RunExperiment />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(axiosGetMock).toHaveBeenCalledWith(
        `/model-hub/experiments/v2/suggest-name/${DATASET_ID}/`,
      ),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("experiment name")).toHaveTextContent(
        "support-tickets-2026-10-22-v2",
      ),
    );
  });

  it("D2: rejects a taken name when validate-name answers `is_valid: false`", async () => {
    // views/experiments.py:6360-6363 returns {"is_valid": False, "message": ...}
    axiosGetMock.mockResolvedValue({ data: validateNameTakenResponse });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () => useExperimentNameCheck(DATASET_ID, "support-replies-oct"),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.data).toBe(false));
    const values = {
      columnId: null,
      name: "support-replies-oct",
      experimentType: "llm",
      outputFormat: "string",
      promptConfig: [],
      userEvalMetrics: [],
    };

    expect(axiosGetMock).toHaveBeenCalledWith(
      "/model-hub/experiments/v2/validate-name/",
      { params: { dataset_id: DATASET_ID, name: "support-replies-oct" } },
    );
    expect(
      configurationIssues(values, { nameTaken: result.current.data === false }),
    ).toContain("An experiment with this name already exists on this dataset");
    expect(configurationIssues(values, { nameTaken: false })).not.toContain(
      "An experiment with this name already exists on this dataset",
    );
  });
});
