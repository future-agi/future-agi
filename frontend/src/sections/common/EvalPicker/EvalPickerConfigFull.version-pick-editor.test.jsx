/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "src/utils/test-utils";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import Quill from "src/utils/quillHtmlExport";
import { getBlocks } from "src/components/PromptCards/common";
import { installRangePolyfill } from "src/components/PromptCards/test-fixtures/editorHarness";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

// EVAL-E2E-082: the dataset eval drawer reopens a binding pinned to V1 and
// V2 (the default) is picked with nothing typed. The prompt editor is the
// real one (LLMPromptEditor, MessageEditor, MessageEditorBlock and the Quill
// PromptEditor), so a version's prompt is set into Quill as in the browser.

vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    const { onReadyChange } = props;
    React.useEffect(() => {
      onReadyChange?.(true, { output: "col-output" });
    }, [onReadyChange]);
    return <div />;
  });
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
});

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "TracingTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/SimulationTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "SimulationTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/CreateSimulationPreviewMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "CreateSimulationPreviewModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/TestPlayground", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "TestPlaygroundMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(["turing_large", "turing_small", "turing_flash"]),
}));

vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

// The prompts the flow saves: JSON, a line break and the mapped variable.
const judged = (explanation) =>
  `Reply with exactly this JSON: {"result":"saved","explanation":"${explanation}"}\nMapped input: {{output}}`;

// Hook mocks return referentially stable values. Each version keeps the
// template's settings as the backend saves them.
const { detail, versions, updateEval, createVersion, composite, unionKeys } =
  vi.hoisted(() => {
    const judgedText = (explanation) =>
      `Reply with exactly this JSON: {"result":"saved","explanation":"${explanation}"}\nMapped input: {{output}}`;
    const settings = (explanation) => ({
      model: "gpt-4o",
      rule_prompt: judgedText(explanation),
      output: "score",
      required_keys: ["output"],
      few_shot_examples: [],
    });
    return {
      detail: {
        data: {
          id: "tpl-1",
          name: "release_scorer",
          owner: "user",
          eval_type: "llm",
          output_type: "percentage",
          instructions: judgedText("newest"),
          current_version: "V2",
          config: settings("newest"),
        },
        isLoading: false,
        isError: false,
      },
      versions: {
        data: {
          versions: [
            {
              id: "ver-2",
              version_number: 2,
              is_default: true,
              criteria: judgedText("newest"),
              config_snapshot: settings("newest"),
            },
            {
              id: "ver-1",
              version_number: 1,
              is_default: false,
              criteria: judgedText("historic"),
              config_snapshot: settings("historic"),
            },
          ],
        },
      },
      updateEval: { mutate: () => {}, mutateAsync: async () => ({}) },
      createVersion: { mutateAsync: async () => ({}) },
      composite: { data: null },
      unionKeys: [],
    };
  });

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => detail,
  useUpdateEval: () => updateEval,
}));

vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => versions,
  useCreateEvalVersion: () => createVersion,
}));

vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => composite,
}));

vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => unionKeys,
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: () => ({ locked: false, isLoading: false }),
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: "oss",
    isCloud: false,
    isOSS: true,
    isEE: false,
  }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

// A dataset binding pinned to V1, as the dataset's saved eval list opens it.
const pinnedBinding = () => ({
  id: "tpl-1",
  templateId: "tpl-1",
  userEvalId: "binding-1",
  name: "release_scorer",
  mapping: { output: "col-output" },
  run_config: { model: "gpt-4o", pass_threshold: 0.7 },
  pinned_version_id: "ver-1",
});

const renderDatasetBinding = (evalData, onSave) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <EvalPickerProvider
        source="dataset"
        sourceId="dataset-1"
        sourceColumns={[{ field: "col-output", headerName: "output" }]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        initialEval={evalData}
      >
        <EvalPickerConfigFull
          evalData={evalData}
          onBack={() => {}}
          onSave={onSave}
          isSaving={false}
        />
      </EvalPickerProvider>
    </QueryClientProvider>,
  );

// The Quill editor holding the system message, and its text as the editor
// reads it back (without Quill's terminal line break).
const editor = () => Quill.find(document.querySelector(".ql-container"));
const editorText = () => {
  const quill = editor();
  if (!quill) return null;
  return getBlocks(quill)
    .map((block) => block.text || "")
    .join("")
    .replace(/\n$/, "");
};

// The version choice sits beside Save version, as the flow finds it.
const versionChoice = () =>
  within(
    screen.getByRole("button", { name: "Save version" }).parentElement,
  ).getByRole("combobox");

const pick = async (name) => {
  fireEvent.mouseDown(versionChoice());
  fireEvent.click(await screen.findByRole("option", { name }));
};

const clickUpdate = async () => {
  const button = screen.getByRole("button", { name: "Update Evaluation" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

describe("a version picked in the real prompt editor (EVAL-E2E-082)", () => {
  beforeEach(() => {
    installRangePolyfill();
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
  });

  it("picks V2 on a V1 binding with nothing typed, asks nothing and saves V2 as it was saved", async () => {
    const onSave = vi.fn();
    renderDatasetBinding(pinnedBinding(), onSave);
    await screen.findByText("Version label: V1");
    await waitFor(() => expect(editorText()).toBe(judged("historic")));
    await pick("V2 (default)");
    await waitFor(() => expect(editorText()).toBe(judged("newest")));
    expect(versionChoice()).toHaveTextContent("V2");
    await clickUpdate();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/aren't in V2|isn't in V2/)).toBeNull();
    const saved = onSave.mock.calls[0][0];
    expect(saved.userEvalId).toBe("binding-1");
    expect(saved.versionId).toBe("ver-2");
    expect(saved.versionPin).toBe("v2");
    expect(saved.instructions).toBe(judged("newest"));
  });

  it("still asks first when the prompt is typed into after V2 is picked", async () => {
    const onSave = vi.fn();
    renderDatasetBinding(pinnedBinding(), onSave);
    await screen.findByText("Version label: V1");
    await waitFor(() => expect(editorText()).toBe(judged("historic")));
    await pick("V2 (default)");
    await waitFor(() => expect(editorText()).toBe(judged("newest")));
    act(() => {
      editor().insertText(0, "Edited ", "user");
    });
    await waitFor(() =>
      expect(editorText()).toBe(`Edited ${judged("newest")}`),
    );
    await clickUpdate();
    expect(
      await screen.findByText("Your prompt edits aren't in V2"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
});
