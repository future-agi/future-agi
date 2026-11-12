/* eslint-disable react/prop-types */
// The New experiment drawer passes the dataset version list it reads to the
// Data step in a launch build: the real form, the real Data step and its real
// form context; only the network and unrelated widgets are stubbed. Module
// mocks follow runExperiment.snakeCase.test.jsx.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useWatch } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import versionList from "src/api/contracts/__tests__/fixtures/dataset-version-list.json";
import { EXPERIMENT_STEP } from "../constants";
import {
  DATASET_ID,
  suggestNameResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());
// The show-unreleased switch as the product modules read it. Shipped: the
// override leaves it to the build and the export reads true. Off: both false.
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
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows", dataset: null, isLoading: false }),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/useUnsavedSelection",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useUnsavedSelection: () => ({ sentence: null }),
  }),
);
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
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/BasicStepExperimentCreation",
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
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/Experiment/VariableMappingTable", () => ({
  default: () => null,
}));
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

const VERSIONS_URL = endpoints.develop.versions.list(DATASET_ID);
const SUGGEST_URL = `/model-hub/experiments/v2/suggest-name/${DATASET_ID}/`;
const [SERVER_VERSION] = versionList.result.versions;
// Two copies the server kept for earlier experiments (native_run_binding.py).
const COPIES = [
  "0b5b7b9e-7777-4c8f-9a8e-0e2f8d1d2c3a",
  "0b5b7b9e-8888-4c8f-9a8e-0e2f8d1d2c3a",
].map((id) => ({
  ...SERVER_VERSION,
  id,
  dataset: DATASET_ID,
  metadata: { capture: "native-snapshot-v1", columns: {} },
}));
const LIST_RESPONSE = {
  data: {
    ...versionList,
    result: { ...versionList.result, dataset_id: DATASET_ID, versions: COPIES },
  },
};
const EARLIER_LINE =
  "2 earlier versions, copied when experiments were created. Running on an earlier version isn't available in this release.";
const FAILED =
  "Couldn't load this dataset's earlier versions. The experiment uses the current rows either way.";
const RUN_BLOCK =
  "Running on an earlier dataset version isn't available in this release. Under Dataset version, choose “Current rows” to run.";
const SWITCH_OFF_BLOCK =
  "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.";
const UNLISTED = "0b5b7b9e-9999-4c8f-9a8e-0e2f8d1d2c3a";

let client;
let versionsAnswer;

const versionCalls = () =>
  axiosGetMock.mock.calls.filter(([url]) => url === VERSIONS_URL);

function openOnDataStep(prefill = null) {
  useRunExperimentStore.setState({
    openRunExperiment: true,
    selectedExperiment: null,
    duplicateFromExperimentId: null,
    currentStep: 0,
    prefill,
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
}

async function goToDataStep() {
  await waitFor(() =>
    expect(screen.getByLabelText("experiment name")).toHaveTextContent(
      "support-tickets-2026-10-22-v2",
    ),
  );
  act(() =>
    useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA),
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  versionsAnswer = async () => LIST_RESPONSE;
  axiosGetMock.mockReset();
  axiosGetMock.mockImplementation(async (url) => {
    if (url === SUGGEST_URL) return { data: suggestNameResponse };
    if (url === VERSIONS_URL) return versionsAnswer();
    return { data: { status: true, result: {} } };
  });
});
afterEach(() => {
  client.clear();
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
});

describe("New experiment drawer › Data step version picker, as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
  });

  it("reads one page of versions and shows Current rows with one line for the earlier versions", async () => {
    expect(isSurfaceShown("dataset.versions")).toBe(true);
    openOnDataStep();
    await goToDataStep();
    const picker = await screen.findByTestId("experiment-version-select");
    expect(within(picker).getByRole("combobox").textContent).toBe(
      "Current rows",
    );
    await waitFor(() =>
      expect(screen.getByTestId("earlier-versions").textContent).toBe(
        EARLIER_LINE,
      ),
    );
    expect(screen.queryByText(/Loading versions/)).toBeNull();
    expect(axiosGetMock).toHaveBeenCalledWith(VERSIONS_URL, {
      params: { limit: 100 },
    });
    // Current rows sends no version, so nothing about a version blocks Run.
    expect(
      screen.queryByTestId("server-blocked")?.textContent ?? "",
    ).not.toMatch(/version/i);
  });

  it("when the list can't be read, the Data step says so, and Retry reads it again without the app's error toast", async () => {
    let calls = 0;
    versionsAnswer = async () => {
      calls += 1;
      if (calls === 1) throw new Error("Network Error");
      return LIST_RESPONSE;
    };
    openOnDataStep();
    await goToDataStep();
    const earlier = await screen.findByTestId("earlier-versions");
    await waitFor(() => expect(earlier.textContent).toContain(FAILED));
    const [query] = client
      .getQueryCache()
      .findAll({ queryKey: ["develop", "dataset-versions", DATASET_ID] });
    expect(query.options.meta).toEqual({ errorHandled: true });
    fireEvent.click(within(earlier).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("earlier-versions").textContent).toBe(
        EARLIER_LINE,
      ),
    );
    expect(versionCalls()).toHaveLength(2);
  });

  it("an earlier version from a link blocks Run with a line that names the Current rows choice", async () => {
    openOnDataStep({ version_id: UNLISTED });
    await goToDataStep();
    const picker = await screen.findByTestId("experiment-version-select");
    expect(within(picker).getByRole("combobox").textContent).toBe(
      "Earlier versionNot available in this release",
    );
    expect(screen.getByTestId("server-blocked").textContent).toBe(
      `Not ready: ${RUN_BLOCK}`,
    );
  });
});

describe("New experiment drawer › Data step version picker with the show-unreleased switch off", () => {
  beforeEach(() => {
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
  });

  it("sends no version read and offers no version choice; a version from a link keeps the existing Run line", async () => {
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("dataset.versions")).toBe(false);
    openOnDataStep({ version_id: UNLISTED });
    await goToDataStep();
    await screen.findByRole("button", { name: "Clear selection" });
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    expect(screen.queryByTestId("earlier-versions")).toBeNull();
    expect(screen.getByTestId("server-blocked").textContent).toBe(
      `Not ready: ${SWITCH_OFF_BLOCK}`,
    );
    expect(versionCalls()).toHaveLength(0);
  });
});
