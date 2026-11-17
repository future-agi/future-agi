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
import { useExperimentForm } from "../ExperimentFormContext";
import { experimentBody, experimentBodyOfValues } from "../experimentBody";
import { getNewExperimentValidationSchema } from "../schema";
import { serverExperimentBody } from "../serverBody";
import copiedMapping from "./copiedEvaluatorMapping.json";
import {
  DATASET_ID,
  suggestNameResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
const formProbe = vi.hoisted(() => ({ getValues: null, setValue: null }));
const duplicate = vi.hoisted(() => ({ detail: undefined }));
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
      post: axiosPostMock,
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
  useGetExperimentDetails: () => ({ data: duplicate.detail, isLoading: false }),
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
    default: function NameProbe({ control, setValue, getValues }) {
      const name = useWatch({ control, name: "name" });
      formProbe.getValues = getValues;
      formProbe.setValue = setValue;
      return (
        <>
          <output aria-label="experiment name">{name}</output>
          <button
            onClick={() => {
              setValue("promptConfig", [
                {
                  experimentType: "llm",
                  targetType: "prompt",
                  name: "Question prompt",
                  promptId: "prompt",
                  promptVersion: "prompt-version",
                  model: [{ value: "gpt-4o-mini" }],
                  variables: ["question"],
                  variableMapping: { question: "question-column" },
                  messages: [{ role: "user", content: "Answer {{question}}" }],
                },
              ]);
              setValue("userEvalMetrics", [
                {
                  templateId: "judge",
                  name: "Question judge",
                  config: { mapping: { input: "question-column" } },
                },
              ]);
            }}
          >
            Use input fixture
          </button>
        </>
      );
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
  async (importOriginal) => {
    const { default: EvaluationStep } = await importOriginal();
    return {
      default: function EvaluationSampleProbe(props) {
        const { sampleRow, sampleRowLabel, historicalSample } =
          useExperimentForm();
        return (
          <>
            <output aria-label="evaluation sample">
              {JSON.stringify({ sampleRow, sampleRowLabel, historicalSample })}
            </output>
            {historicalSample && <EvaluationStep {...props} />}
          </>
        );
      },
    };
  },
);
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: null }),
}));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
vi.mock("src/sections/common/EvalHandoffs/AddEvalGroupButton", () => ({
  default: () => null,
}));
vi.mock("../StepperComponentExperiment/DraftJudgeDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Experiment/VariableMappingTable", () => ({
  default: function SampleProbe() {
    const { rows } = useExperimentForm();
    return (
      <output aria-label="sample row identities">
        {JSON.stringify(rows.map((row) => row.row_id))}
      </output>
    );
  },
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
  "2 earlier versions, copied when experiments were created. Choose one to run on its rows.";
const FAILED =
  "Couldn't load this dataset's earlier versions. The experiment uses the current rows either way.";
const SWITCH_OFF_BLOCK =
  "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.";
const UNLISTED = "0b5b7b9e-9999-4c8f-9a8e-0e2f8d1d2c3a";

let client;
let versionsAnswer;
let sampleAnswer;
let currentAnswer;
const currentTable = {
  data: {
    status: true,
    result: {
      column_config: [
        {
          id: "question-column",
          name: "question",
          origin_type: "others",
          data_type: "text",
          role: "input",
        },
      ],
      table: [
        {
          row_id: "original-row",
          order: 0,
          "question-column": { cell_value: "edited question" },
        },
        {
          row_id: "added-row",
          order: 1,
          "question-column": { cell_value: "added question" },
        },
      ],
      metadata: { total_rows: 2, dataset_name: "Questions" },
    },
  },
};
const savedTable = (id, count = COPIES[0].item_count) => ({
  data: {
    status: true,
    result: {
      version: { ...COPIES[0], id, item_count: count },
      items: Array.from({ length: count }, (_, index) => ({
        id: `item-${index}`,
        legacy_row: index === 0 ? "original-row" : `saved-row-${index}`,
        input: { values: { "question-column": "saved question" } },
        expected: null,
      })),
      limit: 50,
      offset: 0,
      has_more: false,
    },
  },
});

const SNAPSHOT_REFERENCE = copiedMapping.snapshot_mapping.reference;
const SOURCE_REFERENCE = copiedMapping.source_mapping.reference;
const [SNAPSHOT_INPUT, SOURCE_INPUT] = Object.entries(
  copiedMapping.source_column_ids,
)[1];
const REPLACEMENT = "10000000-0000-4000-8000-000000000014";
const copiedColumns = [
  [SOURCE_REFERENCE, "reference"],
  [SOURCE_INPUT, "question"],
  [REPLACEMENT, "replacement"],
  ["array-column", "answers"],
].map(([id, name]) => ({
  id,
  name,
  origin_type: "others",
  data_type: "json",
}));
const recoveryMapping = {
  ...copiedMapping.snapshot_mapping,
  indexed: `${SNAPSHOT_REFERENCE}[0]`,
  legacy: `${SNAPSHOT_REFERENCE}::$.answer`,
  nested: [[`${SNAPSHOT_REFERENCE}.answer`, "output"], SNAPSHOT_REFERENCE],
};
const recoveredMapping = {
  reference: REPLACEMENT,
  expected: `${REPLACEMENT}.answer`,
  input: [`${SOURCE_INPUT}.question`, `${REPLACEMENT}.answer`],
  output: "output",
  indexed: `${REPLACEMENT}[0]`,
  legacy: `${REPLACEMENT}::$.answer`,
  nested: [[`${REPLACEMENT}.answer`, "output"], REPLACEMENT],
};
const copiedDetail = (kind) => ({
  id: "source-experiment",
  dataset_id: DATASET_ID,
  name: "Saved experiment",
  column_id: SNAPSHOT_REFERENCE,
  source_column_id: null,
  source_column_ids: { [SNAPSHOT_INPUT]: SOURCE_INPUT },
  snapshot_dataset_id: "snapshot-dataset",
  prompt_configs:
    kind === "native"
      ? [
          {
            id: "saved-prompt-config",
            prompt_id: "saved-prompt",
            prompt_version: "saved-prompt-version",
            prompt_name: "Saved prompt",
            model: "gpt-4o-mini",
          },
        ]
      : [],
  agent_configs: [],
  user_eval_metrics: [
    {
      id: "saved-judge",
      template_id: "10000000-0000-4000-8000-000000000006",
      name: "Saved judge",
      model: "turing_flash",
      config: { mapping: recoveryMapping },
    },
  ],
  runs: [
    {
      run_id: "source-run",
      dataset_version_id: COPIES[0].id,
      task_kind: kind === "http" ? "external" : "prompt",
    },
  ],
  ...(kind === "http"
    ? {
        http_configs: [
          {
            id: "saved-endpoint",
            order: 0,
            http: {
              url: "https://fixture.example/run",
              method: "POST",
              body_template: "{}",
              output_path: "$.reply",
            },
          },
        ],
      }
    : {}),
});

const versionCalls = () =>
  axiosGetMock.mock.calls.filter(([url]) => url === VERSIONS_URL);

function openOnDataStep(prefill = null) {
  useRunExperimentStore.setState({
    openRunExperiment: true,
    selectedExperiment: null,
    duplicateFromExperimentId: duplicate.detail?.id ?? null,
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
  duplicate.detail = undefined;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  versionsAnswer = async () => LIST_RESPONSE;
  sampleAnswer = async (id) => savedTable(id);
  currentAnswer = async () => currentTable;
  axiosPostMock.mockReset();
  axiosPostMock.mockImplementation(async () => ({
    data: {
      status: true,
      result: {
        rows: [
          {
            row_id: "original-row",
            inputs_used: {
              input: {
                status: "ok",
                column: "question",
                value: "edited question",
              },
            },
          },
        ],
      },
    },
  }));
  axiosGetMock.mockReset();
  axiosGetMock.mockImplementation(async (url) => {
    if (url === SUGGEST_URL) return { data: suggestNameResponse };
    if (url === VERSIONS_URL) return versionsAnswer();
    if (url === endpoints.develop.getDatasetDetail(DATASET_ID))
      return currentAnswer();
    const savedId = [...COPIES.map((version) => version.id), UNLISTED].find(
      (id) => url === endpoints.develop.versions.detail(DATASET_ID, id),
    );
    if (savedId) return sampleAnswer(savedId);
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

  it("an earlier version from a link stays chosen, with no release words", async () => {
    openOnDataStep({ version_id: UNLISTED });
    await goToDataStep();
    const picker = await screen.findByTestId("experiment-version-select");
    expect(within(picker).getByRole("combobox").textContent).toBe(
      "Earlier version",
    );
    expect(screen.queryByText(/Not available in this release/)).toBeNull();
  });

  it("an earlier version on the list counts its own rows, not the current ones", async () => {
    openOnDataStep({ version_id: COPIES[1].id });
    await goToDataStep();
    const picker = await screen.findByTestId("experiment-version-select");
    await waitFor(() =>
      expect(within(picker).getByRole("combobox").textContent).toBe(
        "Copied for an experiment",
      ),
    );
    // The version list says it holds 5 examples; the current rows are not
    // counted for it.
    expect(
      await screen.findByText("5 examples (all rows)", { exact: true }),
    ).toBeTruthy();
  });

  it.each([
    ["conflicting", "mouse"],
    ["conflicting", "keyboard"],
    ["incomplete", "mouse"],
    ["incomplete", "keyboard"],
  ])(
    "resolves %s duplicate pins by choosing Current rows with the %s",
    async (kind, input) => {
      const conflict =
        "Not ready: The source experiment has no single dataset version. Choose which rows the duplicate should use on the Data step.";
      duplicate.detail = {
        id: "source-experiment",
        dataset_id: DATASET_ID,
        name: "Saved experiment",
        snapshot_dataset_id: "snapshot-dataset",
        prompt_configs: [],
        user_eval_metrics: [],
        run_scope: {
          row_ids: ["snapshot-row"],
          split_ids: ["snapshot-split"],
          sample: { n: 1, seed: 42 },
        },
        http_configs: [
          {
            id: "saved-endpoint",
            order: 0,
            http: {
              url: "https://fixture.example/run",
              method: "POST",
              body_template: "{}",
              output_path: "$.reply",
            },
          },
        ],
        runs: [
          { run_id: "a", dataset_version_id: COPIES[0].id },
          {
            run_id: "b",
            dataset_version_id: kind === "conflicting" ? COPIES[1].id : null,
          },
        ],
      };
      versionsAnswer = async () => {
        if (kind === "incomplete") throw new Error("History unavailable");
        return {
          data: {
            ...LIST_RESPONSE.data,
            result: { ...LIST_RESPONSE.data.result, versions: [] },
          },
        };
      };
      openOnDataStep();
      await screen.findByLabelText("experiment name");
      act(() =>
        useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA),
      );
      expect((await screen.findByText(conflict)).textContent).toBe(conflict);
      expect(formProbe.getValues().datasetVersionConflict).toBe(true);
      const select = within(
        screen.getByTestId("experiment-version-select"),
      ).getByRole("combobox");
      expect(select.textContent).toBe("Choose rows");
      if (input === "keyboard") {
        fireEvent.keyDown(select, { key: "Enter", code: "Enter" });
        const currentRows = screen.getByRole("option", {
          name: "Current rows",
        });
        act(() => currentRows.focus());
        fireEvent.keyDown(currentRows, { key: "Enter", code: "Enter" });
        fireEvent.keyUp(currentRows, { key: "Enter", code: "Enter" });
      } else {
        fireEvent.mouseDown(select);
        fireEvent.click(screen.getByRole("option", { name: "Current rows" }));
      }
      await waitFor(() => expect(screen.queryByText(conflict)).toBeNull());
      const values = formProbe.getValues();
      expect(values.datasetVersionConflict).toBe(false);
      expect(values.datasetVersionId).toBe("");
      expect(values.rowIds).toEqual([]);
      expect(values.splitIds).toEqual([]);
      expect(values.sample).toBeNull();
      expect(
        experimentBodyOfValues(values, { datasetId: DATASET_ID })
          .dataset_version_id,
      ).toBeUndefined();
      expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
        false,
      );
    },
  );

  const chooseVersion = (id) => {
    fireEvent.mouseDown(
      within(screen.getByTestId("experiment-version-select")).getByRole(
        "combobox",
      ),
    );
    fireEvent.click(
      screen
        .getAllByRole("option")
        .find((option) => option.getAttribute("data-value") === id),
    );
  };
  const referenceBlock =
    "Not ready: The source reference column is unavailable. Choose an expected answers column on the Data step.";
  const openUnavailableReference = async (kind) => {
    duplicate.detail = copiedDetail(kind);
    currentAnswer = async () => ({
      data: {
        ...currentTable.data,
        result: { ...currentTable.data.result, column_config: copiedColumns },
      },
    });
    openOnDataStep();
    await screen.findByLabelText("experiment name");
    act(() =>
      useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA),
    );
    expect((await screen.findByText(referenceBlock)).textContent).toBe(
      referenceBlock,
    );
    await screen.findByTestId("reads-evals");
    expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
      true,
    );
  };

  it.each([
    ["native", "reference-first"],
    ["http", "reference-first"],
    ["native", "rows-first"],
    ["http", "rows-first"],
  ])(
    "repairs every unavailable %s reference mapping with %s selection",
    async (kind, order) => {
      await openUnavailableReference(kind);
      if (order === "rows-first") {
        chooseVersion("");
        expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
          true,
        );
      }
      fireEvent.mouseDown(
        screen
          .getAllByRole("combobox")
          .find(
            (select) => select.textContent === "Reference column unavailable",
          ),
      );
      fireEvent.click(screen.getByRole("option", { name: "replacement" }));
      if (order === "reference-first") chooseVersion("");
      await waitFor(() =>
        expect(screen.queryByText(referenceBlock)).toBeNull(),
      );
      expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
        false,
      );
      const values = formProbe.getValues();
      expect(values.datasetVersionId).toBe("");
      expect(values.columnId).toBe(REPLACEMENT);
      expect(values.userEvalMetrics[0].mapping).toEqual(recoveredMapping);
      expect(values.userEvalMetrics[0].config.mapping).toEqual(
        recoveredMapping,
      );
      const parsed = getNewExperimentValidationSchema(
        copiedColumns,
        false,
        DATASET_ID,
      ).parse(values);
      const wire = serverExperimentBody(
        experimentBody(parsed, values, { datasetId: DATASET_ID }),
        { currentRowsChoice: true, columns: copiedColumns },
      );
      expect(wire.blocks).toEqual([]);
      expect(wire.body.dataset_version_id).toBeUndefined();
      expect(wire.body.column_id).toBe(REPLACEMENT);
      expect(wire.body.user_eval_metrics).toHaveLength(1);
      expect(wire.body.user_eval_metrics[0].config.mapping).toEqual(
        recoveredMapping,
      );
      expect(duplicate.detail.user_eval_metrics[0].config.mapping).toEqual(
        recoveryMapping,
      );
    },
  );

  it.each(["native", "http"])(
    "keeps a %s duplicate blocked while any nested snapshot reference remains",
    async (kind) => {
      await openUnavailableReference(kind);
      chooseVersion("");
      const evals = formProbe.getValues().userEvalMetrics;
      const partial = {
        ...recoveredMapping,
        input: [`${SOURCE_INPUT}.question`, [`${SNAPSHOT_REFERENCE}.answer`]],
      };
      const withMapping = (mapping) =>
        evals.map((item) => ({
          ...item,
          mapping,
          config: { ...item.config, mapping },
        }));
      // A restored/editor-updated picker is not proof that its mappings moved.
      act(() => {
        formProbe.setValue("columnId", REPLACEMENT);
        formProbe.setValue("userEvalMetrics", withMapping(partial));
      });
      expect(screen.getByText(referenceBlock).textContent).toBe(referenceBlock);
      expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
        true,
      );
      act(() =>
        formProbe.setValue("userEvalMetrics", withMapping(recoveredMapping)),
      );
      await waitFor(() =>
        expect(screen.queryByText(referenceBlock)).toBeNull(),
      );
      expect(screen.getByRole("button", { name: /Continue/ }).disabled).toBe(
        false,
      );
    },
  );

  const openWithInputs = async () => {
    openOnDataStep();
    fireEvent.click(
      await screen.findByRole("button", { name: "Use input fixture" }),
    );
    act(() =>
      useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA),
    );
    await waitFor(() =>
      expect(
        within(screen.getByTestId("reads-prompts")).getByText(
          "Answer edited question",
          { exact: true },
        ).textContent,
      ).toBe("Answer edited question"),
    );
  };
  const submittedVersion = () =>
    experimentBodyOfValues(formProbe.getValues(), { datasetId: DATASET_ID })
      .dataset_version_id;

  it("switches previews and sample membership to the saved version", async () => {
    sampleAnswer = async (id) => savedTable(id, 1);
    await openWithInputs();
    await waitFor(() =>
      expect(
        within(screen.getByTestId("reads-evals")).getByText(
          "= edited question",
          { exact: true },
        ).textContent,
      ).toBe("= edited question"),
    );
    const previewCount = axiosPostMock.mock.calls.length;
    chooseVersion(COPIES[1].id);
    await waitFor(() =>
      expect(
        within(screen.getByTestId("reads-prompts")).getByText(
          "Answer saved question",
          { exact: true },
        ).textContent,
      ).toBe("Answer saved question"),
    );
    expect(
      within(screen.getByTestId("reads-evals")).getByText("= saved question", {
        exact: true,
      }).textContent,
    ).toBe("= saved question");
    expect(
      screen.queryByText("Answer edited question", { exact: true }),
    ).toBeNull();
    expect(
      JSON.parse(screen.getByLabelText("sample row identities").textContent),
    ).toEqual(["original-row"]);
    expect(axiosPostMock.mock.calls.length).toBe(previewCount);
    expect(axiosGetMock).toHaveBeenCalledWith(
      endpoints.develop.versions.detail(DATASET_ID, COPIES[1].id),
      { params: { limit: 50, offset: 0 } },
    );
    expect(submittedVersion()).toBe(COPIES[1].id);

    chooseVersion("");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("reads-prompts")).getByText(
          "Answer edited question",
          { exact: true },
        ).textContent,
      ).toBe("Answer edited question"),
    );
    expect(
      JSON.parse(screen.getByLabelText("sample row identities").textContent),
    ).toEqual(["original-row", "added-row"]);
    expect(submittedVersion()).toBeUndefined();
  });

  it("renders saved evaluator paths, list positions, and future output from the copied mapping", async () => {
    currentAnswer = async () => ({
      data: {
        ...currentTable.data,
        result: {
          ...currentTable.data.result,
          column_config: copiedColumns,
          table: [
            {
              row_id: "original-row",
              order: 0,
              [SOURCE_REFERENCE]: { cell_value: { answer: "edited answer" } },
              [SOURCE_INPUT]: { cell_value: { question: "edited question" } },
              "array-column": { cell_value: ["edited array"] },
            },
          ],
        },
      },
    });
    sampleAnswer = async (id) => {
      const saved = savedTable(id, 1);
      saved.data.result.items[0].input.values = {
        [SOURCE_REFERENCE]: {
          answer: "saved answer",
          items: ["saved nested"],
          accepted: false,
          count: 0,
        },
        [SOURCE_INPUT]: '{"question":"saved question"}',
        "array-column": ["first saved", "last saved"],
      };
      return saved;
    };
    openOnDataStep({ version_id: COPIES[0].id });
    await screen.findByLabelText("experiment name");
    act(() => {
      formProbe.setValue("userEvalMetrics", [
        {
          templateId: "saved-judge",
          name: "Saved judge",
          config: {
            mapping: {
              ...copiedMapping.source_mapping,
              bracket: `${SOURCE_REFERENCE}.items[0]`,
              array: "array-column[0]",
              legacy: `${SOURCE_REFERENCE}::$.answer`,
              nested: [
                [`${SOURCE_REFERENCE}.answer`, "OUTPUT"],
                ["array-column[1]"],
              ],
              prompt: "prompt_chain",
              empty_list: [],
              accepted: `${SOURCE_REFERENCE}.accepted`,
              count: `${SOURCE_REFERENCE}.count`,
              missing: `${SOURCE_REFERENCE}.absent`,
            },
          },
        },
      ]);
      useRunExperimentStore.getState().setCurrentStep(EXPERIMENT_STEP.DATA);
    });
    const reads = within(await screen.findByTestId("reads-evals"));
    const expected = [
      [
        "reference ← reference",
        '= {"answer":"saved answer","items":["saved nested"],"accepted":false,"count":0}',
      ],
      ["expected ← reference.answer", "= saved answer"],
      ["input[0] ← question.question", "= saved question"],
      ["input[1] ← reference.answer", "= saved answer"],
      ["output ← run output", "· each run's reply"],
      ["bracket ← reference.items[0]", "= saved nested"],
      ["array ← answers[0]", "= first saved"],
      ["legacy ← reference.answer", "= saved answer"],
      ["nested[0][0] ← reference.answer", "= saved answer"],
      ["nested[0][1] ← run output", "· each run's reply"],
      ["nested[1][0] ← answers[1]", "= last saved"],
      ["prompt ← run prompt", "· each run's prompt messages"],
      ["empty_list ← []", "= []"],
      ["accepted ← reference.accepted", "= false"],
      ["count ← reference.count", "= 0"],
      ["missing ← reference.absent", "· Empty on this row"],
    ];
    for (const [label, value] of expected) {
      const row = reads.getByText(label, { exact: true }).parentElement;
      expect(Array.from(row.children, (child) => child.textContent)).toEqual([
        label,
        value,
      ]);
    }
    expect(reads.queryByText(/Not a dataset column/)).toBeNull();
    expect(
      reads.queryByText(/edited answer|edited question|edited array/),
    ).toBeNull();
    expect(axiosPostMock).not.toHaveBeenCalled();
    expect(submittedVersion()).toBe(COPIES[0].id);
  });

  it("tests the saved item and blocks an open Test after its refresh fails", async () => {
    sampleAnswer = async (id) => savedTable(id, 1);
    await openWithInputs();
    chooseVersion(COPIES[0].id);
    await screen.findByText("Answer saved question", { exact: true });
    act(() =>
      useRunExperimentStore
        .getState()
        .setCurrentStep(EXPERIMENT_STEP.EVALUATIONS),
    );
    await waitFor(() =>
      expect(
        JSON.parse(screen.getByLabelText("evaluation sample").textContent),
      ).toEqual({
        sampleRow: {
          "question-column": { cell_value: "saved question" },
          row_id: "original-row",
          version_item_id: "item-0",
          order: 0,
        },
        sampleRowLabel: "Saved row 1",
        historicalSample: true,
      }),
    );
    // Data already sent its current-row mapping preview; Test is a new action.
    expect(axiosPostMock).toHaveBeenCalledTimes(1);
    expect(axiosPostMock.mock.calls[0][0]).toBe(
      "/model-hub/eval-mapping-preview/",
    );
    axiosPostMock.mockClear();
    const metadata = { assessment: { status: "assessed" } };
    const result = { output: "Passed", metadata };
    axiosPostMock.mockResolvedValueOnce({ data: { result } });
    fireEvent.click(
      screen.getByRole("button", { name: "Test on Saved row 1" }),
    );
    fireEvent.click(screen.getByTestId("eval-test-run"));
    expect((await screen.findByTestId("eval-test-score")).textContent).toBe(
      "Passed",
    );
    expect(axiosPostMock).toHaveBeenCalledTimes(1);
    expect(axiosPostMock.mock.calls[0]).toEqual([
      endpoints.develop.eval.evalPlayground,
      {
        experiment_preview: {
          dataset_id: DATASET_ID,
          dataset_version_id: COPIES[0].id,
          version_item_id: "item-0",
          output: "",
          output_name: "Output",
          output_type: "text",
          evaluator: getNewExperimentValidationSchema([]).parse(
            formProbe.getValues(),
          ).userEvalMetrics[0],
        },
      },
    ]);
    expect(submittedVersion()).toBe(COPIES[0].id);
    axiosPostMock.mockClear();
    const uncaught = vi.fn();
    window.addEventListener("error", uncaught);
    try {
      sampleAnswer = async () => {
        throw new Error("Version unavailable");
      };
      await act(() =>
        client.invalidateQueries({
          queryKey: ["experiment-create", "sample-table", DATASET_ID],
        }),
      );
      await waitFor(() =>
        expect(
          JSON.parse(screen.getByLabelText("evaluation sample").textContent)
            .sampleRow,
        ).toBeNull(),
      );
      const runTest = screen.getByTestId("eval-test-run");
      expect(runTest).toBeDisabled();
      expect(() => fireEvent.click(runTest)).not.toThrow();
      expect(axiosPostMock).toHaveBeenCalledTimes(0);
      expect(uncaught).toHaveBeenCalledTimes(0);
    } finally {
      window.removeEventListener("error", uncaught);
    }
  });

  it("suppresses prior rows while loading and separates saved version caches", async () => {
    let resolveSample;
    sampleAnswer = () =>
      new Promise((resolve) => {
        resolveSample = resolve;
      });
    await openWithInputs();
    chooseVersion(COPIES[0].id);
    expect(
      (await screen.findByText("Loading the selected version's sample…"))
        .textContent,
    ).toBe("Loading the selected version's sample…");
    expect(screen.queryByTestId("reads-on-sample")).toBeNull();
    await waitFor(() => expect(typeof resolveSample).toBe("function"));
    await act(async () => resolveSample(savedTable(COPIES[0].id, 1)));
    await screen.findByText("Answer saved question", { exact: true });

    sampleAnswer = async (id) => {
      const saved = savedTable(id, 1);
      saved.data.result.items[0].input.values["question-column"] =
        "other question";
      return saved;
    };
    chooseVersion(COPIES[1].id);
    await screen.findByText("Answer other question", { exact: true });
    const cached = (id) =>
      client.getQueryData([
        "experiment-create",
        "sample-table",
        DATASET_ID,
        `saved:${id}`,
        "",
      ]).data.result.table[0];
    expect(cached(COPIES[0].id)).toEqual({
      "question-column": { cell_value: "saved question" },
      row_id: "original-row",
      version_item_id: "item-0",
      order: 0,
    });
    expect(cached(COPIES[1].id)).toEqual({
      "question-column": { cell_value: "other question" },
      row_id: "original-row",
      version_item_id: "item-0",
      order: 0,
    });
  });

  it("does not retain current-row previews while a historical sample fails", async () => {
    sampleAnswer = async () => {
      throw new Error("Version unavailable");
    };
    await openWithInputs();
    chooseVersion(COPIES[0].id);
    expect(
      (await screen.findByRole("button", { name: "Retry sample" })).textContent,
    ).toBe("Retry sample");
    expect(
      screen.getByText(
        "Could not load the selected version's sample. Current rows are not shown.",
      ).textContent,
    ).toBe(
      "Could not load the selected version's sample. Current rows are not shown.Retry sample",
    );
    expect(screen.queryByTestId("reads-on-sample")).toBeNull();
    expect(submittedVersion()).toBe(COPIES[0].id);
    sampleAnswer = async (id) => savedTable(id, 1);
    fireEvent.click(screen.getByRole("button", { name: "Retry sample" }));
    await screen.findByText("Answer saved question", { exact: true });
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
