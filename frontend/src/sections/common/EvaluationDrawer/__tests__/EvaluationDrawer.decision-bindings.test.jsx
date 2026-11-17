import React from "react";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import EvaluationDrawer from "../EvaluationDrawer";

const mocks = vi.hoisted(() => ({
  picker: null,
  list: { data: { evals: [] }, isLoading: false },
  store: { pendingEditEvalId: null, clearPendingEditEval: () => {} },
}));

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: vi.fn(async () => ({ data: { result: {} } })) },
}));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: (props) => {
    mocks.picker = props;
    return null;
  },
}));
vi.mock("../getEvalsList", () => ({
  useEvalsList: () => mocks.list,
  getUserEvalListKey: () => ["saved-evals"],
}));
vi.mock("../EvaluationsSelectionGrid", () => ({ default: () => null }));
vi.mock("../SavedEvalsList", () => ({ default: () => null }));
vi.mock("../SavedEvalsSkeleton", () => ({ default: () => null }));
vi.mock("../DeleteEval", () => ({ default: () => null }));
vi.mock("../RunEvals", () => ({ default: () => null }));
vi.mock("../CustomEvalsForm", () => ({ default: () => null }));
vi.mock("../EvaluationMappingForm", () => ({ default: () => null }));
vi.mock("../CreateEvaluationGroupDrawer", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@iconify/react", async (original) => ({
  ...(await original()),
  Icon: () => null,
}));
vi.mock("src/sections/evals/store/useEvalStore", () => ({
  resetEvalStore: () => {},
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunEvaluationStore: Object.assign((select) => select(mocks.store), {
    getState: () => mocks.store,
  }),
}));
vi.mock(
  "src/sections/workbench/createPrompt/Evaluation/context/WorkbenchEvaluationContext",
  () => ({
    useWorkbenchEvaluationContext: () => ({ versions: ["prompt-v1"] }),
  }),
);
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));

beforeEach(() => {
  mocks.picker = null;
  vi.clearAllMocks();
});

function mountHost(module) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EvaluationDrawer
        open
        id="source-1"
        module={module}
        onClose={() => {}}
        allColumns={[]}
        listComponent={<div />}
      />
    </QueryClientProvider>,
    { theme: createTheme({ customShadows: { drawer: "none" } }) },
  );
}

const CASES = [
  {
    name: "native Jev pass criteria",
    model: "jev-1.14.0",
    output_type: "Pass/Fail",
    jev_mapping: {
      question_type: "noul",
      pass: {
        criteria_true: "Answers the question",
        criteria_false: "Off topic",
      },
    },
  },
  {
    name: "OpenRouter decision rubric",
    model: "decision/openrouter/morph/reflex",
    output_type: "score",
    jev_mapping: {
      question_type: "score",
      score: { levels: ["Incorrect", "Correct"] },
    },
  },
];

describe.each(["workbench", "dataset"])(
  "%s decision binding host",
  (module) => {
    it.each(CASES)(
      "preserves $name through the real host callback",
      async ({ model, output_type, jev_mapping }) => {
        mountHost(module);
        await waitFor(() => expect(mocks.picker?.source).toBe(module));
        const runConfig = {
          model,
          output_type,
          jev_mapping,
          choice_scores: {},
          choices: [],
          multi_choice: false,
          agent_mode: "quick",
          pass_threshold: 0,
        };
        await act(async () => {
          await mocks.picker.onEvalAdded({
            templateId: "template-1",
            name: "quality",
            model,
            mapping: { input: "output" },
            jev_mapping,
            config: {
              rule_prompt: "Template-private field",
              run_config: {
                ...runConfig,
                internal_only: "not a runtime override",
              },
            },
          });
        });
        const endpoint =
          module === "workbench"
            ? endpoints.develop.runPrompt.createOrUpdateEvalConfig("source-1")
            : endpoints.develop.eval.addEval("source-1");
        expect(axios.post).toHaveBeenCalledWith(
          endpoint,
          expect.objectContaining({
            model,
            config: expect.objectContaining({ run_config: runConfig }),
          }),
        );
        const payload = axios.post.mock.calls[0][1];
        expect(payload.config.run_config).not.toHaveProperty("internal_only");
        expect(payload.config.run_config).not.toHaveProperty("rule_prompt");
        if (module === "workbench") {
          expect(payload).toMatchObject({
            id: "template-1",
            version_to_run: ["prompt-v1"],
          });
        }
      },
    );
  },
);
