import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import * as launchSwitch from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import EvaluationStepExperimentCreation from "../StepperComponentExperiment/EvaluationStepExperimentCreation";

// JRN-EXP-NO-PROMPT-DEADEND (first-user journey, step 3): the wizard stopped
// at Evaluations with "Add at least one evaluation". Dev's V2 create refuses
// an experiment with none, so the empty step offers one ready judge, judged
// by the model the run already uses. The real step, cards and form run here;
// the drawers and data hooks around them are inert, and the two template
// requests are the only network seam.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      get: vi.fn(() =>
        Promise.reject(
          new Error("Unexpected GET in the starter evaluation test"),
        ),
      ),
      post: vi.fn(),
    },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/DraftJudgeDialog",
  () => ({ default: () => null }),
);
vi.mock("src/sections/common/EvalHandoffs/AddEvalGroupButton", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/DecisionJudges/DecisionCallPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: null }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ dataset: null }),
}));
vi.mock("src/sections/common/RunSettings/modelCapabilities", () => ({
  useJudgeMediaWarnings: () => [],
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunEvaluationStore: (select) => select({ setOpenRunEvaluation: () => {} }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSetStore: (select) => select({ sets: {} }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/versionedSource", () => ({
  tableRequestFor: () => {
    throw new Error(
      "Unexpected split-row query in the starter evaluation test",
    );
  },
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/useUnsavedSelection",
  () => ({
    useUnsavedSelection: () => ({
      sentence: null,
      saveLabel: "Save",
      saveAndContinue: () => {},
    }),
  }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "dataset-fixture",
    columns: [{ id: "question-column", name: "question", data_type: "text" }],
    rows: [],
    exampleCount: 3,
    sampleRow: {
      row_id: "row-fixture",
      "question-column": "Where is my order?",
    },
    sampleRowLabel: "Row 1",
    versions: [],
  }),
}));

// The show-unreleased switch (SHOW_UNRELEASED_PARTS in launchSwitch) shows
// every surface whatever the launch override says, so a build carrying it has
// no classic wizard. A launch module may not export the switch at all; read
// it optionally, as experiment-detail-classic/experimentsBasic.js does.
const SHOW_UNRELEASED_PARTS =
  "SHOW_UNRELEASED_PARTS" in launchSwitch &&
  Reflect.get(launchSwitch, "SHOW_UNRELEASED_PARTS") === true;

const SHOWN = {
  launch: true,
  experimentsBasic: true,
  madeReal: madeRealSurfaceIds(),
};
const CLASSIC = {
  launch: true,
  experimentsBasic: false,
  madeReal: madeRealSurfaceIds(),
};
const promptRun = {
  experimentType: "llm",
  targetType: "prompt",
  promptId: "p-new",
  promptVersion: "v-1",
  name: "support-faq prompt",
  model: [{ id: "m-1", value: "gpt-4o-mini" }],
  variables: ["question"],
  variableMapping: { question: "question-column" },
};
const agentRun = {
  experimentType: "llm",
  targetType: "agent",
  agentId: "graph-1",
  agentVersion: "graph-version-1",
  name: "Support agent",
};

function EvaluationsProbe({ control }) {
  const evals = useWatch({ control, name: "userEvalMetrics" }) ?? [];
  return (
    <output aria-label="evaluations">
      {JSON.stringify(
        evals.map((e) => ({
          name: e.name,
          templateId: e.templateId,
          model: e.model,
          mapping: e.config?.mapping,
        })),
      )}
    </output>
  );
}
EvaluationsProbe.propTypes = { control: PropTypes.object.isRequired };

function Host({ runs }) {
  const form = useForm({
    defaultValues: {
      userEvalMetrics: [],
      datasetVersionId: null,
      columnId: null,
      splitIds: [],
      rowIds: [],
      promptConfig: runs,
    },
  });
  return (
    <>
      <EvaluationStepExperimentCreation control={form.control} />
      <EvaluationsProbe control={form.control} />
    </>
  );
}
Host.propTypes = { runs: PropTypes.array.isRequired };

const clients = [];
function renderStep(launch, runs) {
  launchSwitch.setLaunchOverrideForTest(launch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <Host runs={runs} />
    </QueryClientProvider>,
  );
}
const evaluations = () =>
  JSON.parse(screen.getByLabelText("evaluations").textContent);

// The step as it read before the starter: the empty line, a working Add
// evaluation and no template request.
function expectStepAsBefore() {
  expect(screen.queryByTestId("starter-evaluation")).toBeNull();
  expect(screen.getByText(/Add at least one evaluation/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add evaluation" })).toBeEnabled();
  expect(axios.post).not.toHaveBeenCalled();
}

beforeEach(() => {
  axios.post.mockReset();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  launchSwitch.setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("first experiment evaluations (JRN-EXP-NO-PROMPT-DEADEND)", () => {
  it("FED-4: an empty step adds Answers the request, judged by the run's model, in one click", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        status: true,
        result: { id: "tpl-1", name: "answers_the_request", version: "v1" },
      },
    });
    renderStep(SHOWN, [promptRun]);

    const starter = screen.getByTestId("starter-evaluation");
    expect(starter).toHaveTextContent("Answers the request");
    expect(starter).toHaveTextContent("gpt-4o-mini");
    fireEvent.click(
      within(starter).getByRole("button", { name: "Add Answers the request" }),
    );

    expect(
      await screen.findByTestId("eval-card-Answers the request"),
    ).toBeInTheDocument();
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][0]).toBe(
      endpoints.develop.eval.createEvalTemplateV2,
    );
    expect(evaluations()).toEqual([
      {
        name: "Answers the request",
        templateId: "tpl-1",
        model: "gpt-4o-mini",
        mapping: { input: "prompt_chain", output: "output" },
      },
    ]);
    expect(screen.queryByTestId("starter-evaluation")).toBeNull();
  });

  it("FED-4b: a refused judge says why and leaves the step as it was", async () => {
    axios.post.mockRejectedValueOnce({
      status: false,
      result: "Instructions are required.",
      statusCode: 400,
    });
    renderStep(SHOWN, [promptRun]);
    const starter = screen.getByTestId("starter-evaluation");
    fireEvent.click(
      within(starter).getByRole("button", { name: "Add Answers the request" }),
    );

    expect(await within(starter).findByRole("alert")).toHaveTextContent(
      "Couldn't add Answers the request. Instructions are required.",
    );
    expect(evaluations()).toEqual([]);
    expect(screen.getByText(/Add at least one evaluation/)).toBeInTheDocument();
  });

  it.each([
    ["an agent run only", SHOWN, [agentRun]],
    ["no run yet", SHOWN, []],
  ])(
    "FED-4c: with %s there is no starter and the step reads as before",
    (_label, launch, runs) => {
      renderStep(launch, runs);
      expectStepAsBefore();
    },
  );

  it.runIf(!SHOW_UNRELEASED_PARTS)(
    "FED-4c: with the classic wizard there is no starter and the step reads as before",
    () => {
      renderStep(CLASSIC, [promptRun]);
      expectStepAsBefore();
    },
  );

  // On a show-all build exp.new shows under any override, so the classic
  // override still reads as the new wizard and offers the starter, unclicked.
  it.runIf(SHOW_UNRELEASED_PARTS)(
    "FED-4d: a show-all staging build has no classic wizard, so the starter shows",
    () => {
      renderStep(CLASSIC, [promptRun]);
      expect(screen.getByTestId("starter-evaluation")).toHaveTextContent(
        "gpt-4o-mini",
      );
      expect(axios.post).not.toHaveBeenCalled();
    },
  );
});
