/* eslint-disable react/prop-types */
// Keep the real drawer, Configuration, prompt card, form, validation, query,
// keyboard shortcut and Continue handler. Only network and unrelated widgets
// are stubbed; Data is a read-only probe of the values that actually advance.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useWatch } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { EXPERIMENT_STEP } from "../constants";
import { experimentBodyOfValues } from "../experimentBody";
import {
  DATASET_ID,
  suggestNameResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());
const firstConfigurationCommit = vi.hoisted(() => vi.fn());
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  SHOW_UNRELEASED_PARTS: true,
}));
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
vi.mock("../StepperForExperiment", () => ({ default: () => null }));
vi.mock(
  "../StepperComponentExperiment/ConfigureStepLLM",
  async (importOriginal) => {
    const { default: ConfigurationRuns } = await importOriginal();
    return {
      default: function FirstCommitRuns(props) {
        // Schedule a completed input after the real name field has subscribed,
        // but before the drawer's parent effects. No form fields are mocked.
        React.useEffect(() => {
          firstConfigurationCommit();
        }, []);
        return <ConfigurationRuns {...props} />;
      },
    };
  },
);
vi.mock("../StepperComponentExperiment/DataStep", () => ({
  default: function DataProbe({ control }) {
    const values = useWatch({ control });
    return <output data-testid="data-values">{JSON.stringify(values)}</output>;
  },
}));
vi.mock(
  "../StepperComponentExperiment/EvaluationStepExperimentCreation",
  () => ({ default: () => null }),
);
vi.mock("../StepperComponentExperiment/ReviewStep", () => ({
  default: () => null,
}));
vi.mock("../StepperComponentExperiment/NewModelRenderWithParamsTool", () => ({
  default: ({ selectedModel }) => (
    <output aria-label="run model">{selectedModel?.value}</output>
  ),
}));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);
vi.mock("../RunTargetPicker", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSources/SourcePicker", () => ({
  default: () => null,
}));
vi.mock("../FirstPromptComposer", () => ({ default: () => null }));
vi.mock("../ToolModesBlock", () => ({ default: () => null }));
vi.mock("../TryOnOneRow", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSettings/RowLimits", () => ({
  default: () => null,
}));
vi.mock("../CostEstimateLine", () => ({ SimpleEstimate: () => null }));
vi.mock("../EstimateDetails", () => ({
  useEstimateDetails: () => ({}),
  EstimateSummary: () => null,
  EstimateBreakdown: () => null,
}));
vi.mock("../RunTray", () => ({ RunTrayDock: () => null }));

import RunExperiment from "../RunExperiment";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { usePickerUi } from "src/sections/common/RunSources/pickerUi";

const PROMPT_ID = "b6a04ad5-5f7a-4c76-8b45-000000000001";
const VERSION_ID = "b6a04ad5-5f7a-4c76-8b45-000000000002";
const SPLIT_ID = "b6a04ad5-5f7a-4c76-8b45-000000000003";
const PROMPT_NAME = "Linked saved prompt";
const MISSING_VERSION = `Pick a saved version of ${PROMPT_NAME}`;
const PROMPT = { id: PROMPT_ID, name: PROMPT_NAME, model: "gpt-4.1" };
const SAVED = {
  id: VERSION_ID,
  template_version: "v1",
  is_draft: false,
  is_default: true,
  variable_names: {},
  prompt_config_snapshot: {
    messages: [{ role: "user", content: "Summarize this row" }],
    configuration: { temperature: 0 },
  },
};
const page = (results) => ({
  data: { results, current_page: 1, total_pages: 1 },
});
let client;
let response;
let promptResponse;
let suggestionResponse;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function renderPrefilled() {
  useRunExperimentStore.getState().initiateCreateMode({
    prefill: {
      prompt_id: PROMPT_ID,
      prompt_version: VERSION_ID,
      split_name: "smoke",
    },
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

async function openPrefilled() {
  renderPrefilled();
  await screen.findByText(PROMPT_NAME, {
    selector: '[data-testid="run-card-title"]',
  });
  await waitFor(() =>
    expect(axiosGetMock).toHaveBeenCalledWith(
      endpoints.develop.runPrompt.getPromptVersions(),
      expect.objectContaining({
        params: expect.objectContaining({ template_id: PROMPT_ID }),
      }),
    ),
  );
  const name = screen.getByRole("textbox", { name: /Experiment name/ });
  fireEvent.focus(name);
  fireEvent.change(name, { target: { value: "Prefill readiness run" } });
  expect(name).toHaveValue("Prefill readiness run");
  expect(screen.getByLabelText("run model")).toHaveTextContent("gpt-4.1");
  expect(client.getQueryState(["versions", PROMPT_ID])?.status).toBe("pending");
  expect(screen.getByRole("button", { name: "Version" })).toHaveTextContent(
    "Version",
  );
  expect(useRunExperimentStore.getState().currentStep).toBe(
    EXPERIMENT_STEP.CONFIGURATION,
  );
}

function keyboardContinue() {
  fireEvent.keyDown(document, { key: "Enter", code: "Enter", metaKey: true });
}

async function resolveVersions(results = [SAVED]) {
  await act(async () => {
    response.resolve(page(results));
  });
  await waitFor(() =>
    expect(client.getQueryState(["versions", PROMPT_ID])?.status).toBe(
      "success",
    ),
  );
  await waitFor(() =>
    expect(screen.getByTestId("wizard-continue")).toBeEnabled(),
  );
}

async function resolveSuggestion() {
  await act(async () => {
    suggestionResponse.resolve({
      data: {
        ...suggestNameResponse,
        result: { ...suggestNameResponse.result, next_ordinal: 7 },
      },
    });
  });
  // A rendered ordinal proves the query observer delivered the response;
  // an unchanged name alone could pass before the suggestion effect ran.
  await waitFor(() =>
    expect(screen.getByTestId("experiment-ordinal-chip")).toHaveTextContent(
      /^#7$/,
    ),
  );
}

async function continueWithSavedVersion() {
  await resolveVersions();
  fireEvent.click(screen.getByTestId("wizard-continue"));
  const probe = await screen.findByTestId("data-values");
  const values = JSON.parse(probe.textContent);
  expect(useRunExperimentStore.getState().currentStep).toBe(
    EXPERIMENT_STEP.DATA,
  );
  expect(values.name).toBe("Prefill readiness run");
  expect(values.splitIds).toEqual([SPLIT_ID]);
  expect(values.promptConfig[0]).toMatchObject({
    promptId: PROMPT_ID,
    promptVersion: VERSION_ID,
    versionLabel: "v1",
    isDraft: false,
    templateMessages: SAVED.prompt_config_snapshot.messages,
    model: [expect.objectContaining({ value: "gpt-4.1" })],
  });
  // Readiness belongs to UI context, never the raw form or the create body.
  const transientKeys =
    /prefillPending|prefillReadiness|setVersionPrefillPending|versionPrefills|mirroredVersion/;
  expect(JSON.stringify(values)).not.toMatch(transientKeys);
  const body = experimentBodyOfValues(values, { datasetId: DATASET_ID });
  expect(body.prompt_config[0]).toMatchObject({
    prompt_id: PROMPT_ID,
    prompt_version: VERSION_ID,
    model: "gpt-4.1",
  });
  expect(JSON.stringify(body)).not.toMatch(transientKeys);
  expect(screen.queryByText(MISSING_VERSION)).not.toBeInTheDocument();
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  response = deferred();
  promptResponse = null;
  suggestionResponse = null;
  firstConfigurationCommit.mockReset();
  useRunExperimentStore.getState().reset();
  usePickerUi.setState({ level: 0 });
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: madeRealSurfaceIds(),
  });
  axiosGetMock.mockReset();
  axiosGetMock.mockImplementation(async (url) => {
    if (url === endpoints.develop.runPrompt.promptExecutions())
      return promptResponse ? promptResponse.promise : page([PROMPT]);
    if (url === endpoints.develop.runPrompt.getPromptVersions())
      return response.promise;
    if (url === `/model-hub/experiments/v2/suggest-name/${DATASET_ID}/`)
      return suggestionResponse
        ? suggestionResponse.promise
        : { data: suggestNameResponse };
    if (url === endpoints.develop.splits.list(DATASET_ID))
      return {
        data: {
          status: true,
          result: { splits: [{ id: SPLIT_ID, name: "smoke", row_count: 1 }] },
        },
      };
    return { data: { status: true, result: {} } };
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  useRunExperimentStore.getState().reset();
  setLaunchOverrideForTest(null);
});

describe("automatic saved-version prefill readiness", () => {
  it("keeps a completed name through the opening render", async () => {
    // Keep the edit before parent effects, but read the controlled input
    // after render's act boundary flushes its queued React state update.
    // Asserting inside the passive effect reads the previous DOM value.
    let name;
    firstConfigurationCommit.mockImplementationOnce(() => {
      name = screen.getByRole("textbox", { name: /Experiment name/ });
      fireEvent.focus(name);
      fireEvent.change(name, { target: { value: "Prefill readiness run" } });
    });
    renderPrefilled();
    expect(firstConfigurationCommit).toHaveBeenCalledOnce();
    expect(name).toHaveValue("Prefill readiness run");
    await screen.findByText(PROMPT_NAME, {
      selector: '[data-testid="run-card-title"]',
    });
    expect(
      screen.getByRole("textbox", { name: /Experiment name/ }),
    ).toHaveValue("Prefill readiness run");
    await continueWithSavedVersion();
  });

  it("keeps a typed name when the suggestion arrives before prompt hydration", async () => {
    promptResponse = deferred();
    suggestionResponse = deferred();
    renderPrefilled();
    const name = screen.getByRole("textbox", { name: /Experiment name/ });
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Prefill readiness run" } });
    expect(name).toHaveValue("Prefill readiness run");
    await resolveSuggestion();
    expect(name).toHaveValue("Prefill readiness run");
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    await act(async () => {
      promptResponse.resolve(page([PROMPT]));
    });
    await screen.findByText(PROMPT_NAME, {
      selector: '[data-testid="run-card-title"]',
    });
    expect(
      screen.getByRole("textbox", { name: /Experiment name/ }),
    ).toHaveValue("Prefill readiness run");
    await continueWithSavedVersion();
  });

  it("leaves a deliberately cleared name empty when the suggestion arrives", async () => {
    suggestionResponse = deferred();
    await openPrefilled();
    const name = screen.getByRole("textbox", { name: /Experiment name/ });
    fireEvent.change(name, { target: { value: "" } });
    fireEvent.blur(name);
    expect(name).toHaveValue("");
    await resolveSuggestion();
    expect(name).toHaveValue("");
    await resolveVersions();
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(await screen.findByText("Name is required")).toBeInTheDocument();
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
    expect(screen.queryByTestId("data-values")).not.toBeInTheDocument();
  });

  it("fills an untouched name when the suggestion arrives", async () => {
    promptResponse = deferred();
    suggestionResponse = deferred();
    renderPrefilled();
    const name = screen.getByRole("textbox", { name: /Experiment name/ });
    expect(name).toHaveValue("");
    await resolveSuggestion();
    expect(name).toHaveValue("support-tickets-2026-10-22-v2");
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
  });

  it("waits for the linked prompt before saved-version hydration and keeps entered fields", async () => {
    promptResponse = deferred();
    renderPrefilled();
    await waitFor(() =>
      expect(client.getQueryState(["prompts", "", "chat"])?.status).toBe(
        "pending",
      ),
    );
    const name = screen.getByRole("textbox", { name: /Experiment name/ });
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Prefill readiness run" } });
    fireEvent.click(screen.getByRole("button", { name: "Description" }));
    fireEvent.change(
      screen.getByRole("textbox", { name: "Description (optional)" }),
      { target: { value: "Keep the question while the prompt loads" } },
    );
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    expect(screen.getByText("Loading linked prompt…")).toHaveAttribute(
      "role",
      "status",
    );
    fireEvent.click(screen.getByTestId("wizard-continue"));
    keyboardContinue();
    expect(screen.queryByText("Add at least one run")).not.toBeInTheDocument();
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
    await act(async () => {
      promptResponse.resolve(page([PROMPT]));
    });
    await screen.findByText(PROMPT_NAME, {
      selector: '[data-testid="run-card-title"]',
    });
    expect(
      screen.getByRole("textbox", { name: /Experiment name/ }),
    ).toHaveValue("Prefill readiness run");
    expect(
      screen.getByRole("textbox", { name: "Description (optional)" }),
    ).toHaveValue("Keep the question while the prompt loads");
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    expect(screen.getByText("Loading saved versions…")).toHaveAttribute(
      "role",
      "status",
    );
    expect(
      screen.queryByText("Loading linked prompt…"),
    ).not.toBeInTheDocument();
    await continueWithSavedVersion();
    const values = JSON.parse(screen.getByTestId("data-values").textContent);
    expect(values.description).toBe("Keep the question while the prompt loads");
    expect(
      experimentBodyOfValues(values, { datasetId: DATASET_ID }).description,
    ).toBe("Keep the question while the prompt loads");
  });

  it("a cached prompt list without the linked prompt holds Continue while it refetches", async () => {
    // The prompt list cache is shared with the prompt picker: here it holds an
    // earlier page without the linked prompt, so the query is not pending but
    // its mount refetch has not answered yet.
    client.setQueryData(["prompts", "", "chat"], {
      pages: [{ results: [], current_page: 1, total_pages: 1 }],
      pageParams: [1],
    });
    promptResponse = deferred();
    renderPrefilled();
    await waitFor(() =>
      expect(client.getQueryState(["prompts", "", "chat"])?.fetchStatus).toBe(
        "fetching",
      ),
    );
    expect(client.getQueryState(["prompts", "", "chat"])?.status).toBe(
      "success",
    );
    const name = screen.getByRole("textbox", { name: /Experiment name/ });
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Prefill readiness run" } });
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    fireEvent.click(screen.getByTestId("wizard-continue"));
    keyboardContinue();
    expect(screen.queryByText("Add at least one run")).not.toBeInTheDocument();
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
    await act(async () => {
      promptResponse.resolve(page([PROMPT]));
    });
    await screen.findByText(PROMPT_NAME, {
      selector: '[data-testid="run-card-title"]',
    });
    expect(
      screen.getByRole("textbox", { name: /Experiment name/ }),
    ).toHaveValue("Prefill readiness run");
    await continueWithSavedVersion();
  });

  it.each(["empty", "error"])(
    "%s linked prompts finish loading without skipping missing-run validation",
    async (result) => {
      promptResponse = deferred();
      renderPrefilled();
      const name = screen.getByRole("textbox", { name: /Experiment name/ });
      fireEvent.focus(name);
      fireEvent.change(name, { target: { value: "Missing prompt run" } });
      expect(screen.getByTestId("wizard-continue")).toBeDisabled();
      await act(async () => {
        if (result === "error")
          promptResponse.reject(new Error("Prompts unavailable"));
        else promptResponse.resolve(page([]));
      });
      await waitFor(() =>
        expect(screen.getByTestId("wizard-continue")).toBeEnabled(),
      );
      expect(
        screen.queryByText("Loading linked prompt…"),
      ).not.toBeInTheDocument();
      keyboardContinue();
      expect(
        screen.getAllByText("Add at least one run")[0],
      ).toBeInTheDocument();
      expect(useRunExperimentStore.getState().currentStep).toBe(
        EXPERIMENT_STEP.CONFIGURATION,
      );
      expect(screen.queryByTestId("data-values")).not.toBeInTheDocument();
    },
  );

  it("prompt-prefilled Continue stays unavailable until its saved version is selected", async () => {
    await openPrefilled();
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    expect(screen.getByText("Loading saved versions…")).toHaveAttribute(
      "role",
      "status",
    );
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.queryByText(MISSING_VERSION)).not.toBeInTheDocument();
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
    await continueWithSavedVersion();
  });

  it("keyboard Continue does not report a missing saved version while prefill is loading", async () => {
    await openPrefilled();
    keyboardContinue();
    expect(screen.queryByText(MISSING_VERSION)).not.toBeInTheDocument();
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
    await continueWithSavedVersion();
  });

  it.each(["empty", "draft-only", "error"])(
    "%s versions finish loading and retain real saved-version validation",
    async (result) => {
      await openPrefilled();
      if (result === "error") {
        await act(async () => {
          response.reject(new Error("Versions unavailable"));
        });
        await waitFor(() =>
          expect(client.getQueryState(["versions", PROMPT_ID])?.status).toBe(
            "error",
          ),
        );
      } else {
        await resolveVersions(
          result === "empty" ? [] : [{ ...SAVED, is_draft: true }],
        );
      }
      await waitFor(() =>
        expect(screen.getByTestId("wizard-continue")).toBeEnabled(),
      );
      expect(
        screen.queryByText("Loading saved versions…"),
      ).not.toBeInTheDocument();
      keyboardContinue();
      expect(await screen.findByText(MISSING_VERSION)).toBeInTheDocument();
      expect(useRunExperimentStore.getState().currentStep).toBe(
        EXPERIMENT_STEP.CONFIGURATION,
      );
      fireEvent.click(screen.getByTestId("wizard-continue"));
      expect(screen.getByText(MISSING_VERSION)).toBeInTheDocument();
      expect(screen.queryByTestId("data-values")).not.toBeInTheDocument();
    },
  );

  it("an unavailable linked version stops loading and keeps the existing explanation", async () => {
    await openPrefilled();
    await resolveVersions([
      { ...SAVED, id: "other-version", template_version: "v2" },
    ]);
    expect(
      await screen.findByTestId("wanted-version-missing"),
    ).toHaveTextContent("which Linked saved prompt does not have");
    expect(screen.getByTestId("wizard-continue")).toBeEnabled();
    expect(
      screen.queryByText("Loading saved versions…"),
    ).not.toBeInTheDocument();
  });

  it("removing an unresolved card releases readiness without hiding the missing-run error", async () => {
    await openPrefilled();
    fireEvent.click(screen.getByRole("button", { name: "Remove run" }));
    await waitFor(() =>
      expect(screen.getByTestId("wizard-continue")).toBeEnabled(),
    );
    keyboardContinue();
    expect(screen.getAllByText("Add at least one run").length).toBeGreaterThan(
      0,
    );
    expect(useRunExperimentStore.getState().currentStep).toBe(
      EXPERIMENT_STEP.CONFIGURATION,
    );
  });
});
