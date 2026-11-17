/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "src/utils/test-utils";
import { screen, fireEvent, act, waitFor } from "@testing-library/react";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerCreateNew from "./EvalPickerCreateNew";

// The picker's own create flow offers Code like Create evaluation: it works
// where the custom_code_evals entry allows it (and while that answer loads,
// after a failed read, or from a server without the entry), and where it is
// off the tab shows turned off with the server's sentence. The tab is never
// removed.

// The server's sentence, as the capabilities answer carries it.
const SERVER_SENTENCE =
  "Custom code evaluations are temporarily unavailable on Future AGI Cloud.";
const AGENTS_REFUSED_WITH_CODE =
  "Agent evaluations aren't enabled for this workspace. Use LLM-as-a-Judge or Code evaluations instead.";
const AGENTS_REFUSED =
  "Agent evaluations aren't enabled for this workspace. Use LLM-as-a-Judge instead.";

// What useFeatureLocked answers for custom_code_evals in each case.
const CODE_ANSWERS = {
  on: { locked: false, reason: null, isLoading: false, isError: false },
  loading: { locked: true, reason: null, isLoading: true, isError: false },
  failed: { locked: true, reason: null, isLoading: false, isError: true },
  noEntry: { locked: true, reason: null, isLoading: false, isError: false },
  off: {
    locked: true,
    reason: SERVER_SENTENCE,
    isLoading: false,
    isError: false,
  },
};

const { capturedProps, snack, caps } = vi.hoisted(() => ({
  capturedProps: { simulation: null },
  snack: vi.fn(),
  caps: { code: null, agentLocked: false },
}));

vi.mock("src/sections/evals/components/SimulationTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.simulation = props;
    return <div />;
  });
  M.displayName = "SimulationTestModeMock";
  return { default: M };
});
vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "TracingTestModeMock";
  return { default: M };
});
vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
});
vi.mock("src/sections/evals/components/TestPlayground", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "TestPlaygroundMock";
  return { default: M };
});
vi.mock("src/api/evaluations/decision-models", () => ({
  default: () => ({ data: [] }),
  decisionModelsQueryKey: ["eval-decision-models"],
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(),
}));
vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value ?? ""}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));
vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: () => <div />,
}));
vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div data-testid="code-editor" />,
}));
vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => <div />,
}));
vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => <div />,
}));
vi.mock("src/sections/evals/components/CompositeDetailPanel", () => ({
  default: () => <div />,
}));
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));
vi.mock(
  "src/sections/tasks/components/TaskLivePreview",
  async (importOriginal) => {
    const actual = await importOriginal();
    return { buildApiFilterArray: actual.buildApiFilterArray };
  },
);
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));
vi.mock("src/sections/evals/hooks/useCreateEval", () => ({
  useCreateEval: () => ({
    mutateAsync: vi.fn(async () => ({ id: "draft-1" })),
  }),
}));
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(async () => ({})),
  }),
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: (id) =>
      id === "custom_code_evals"
        ? caps.code
        : {
            locked: id === "agentic_eval" && caps.agentLocked,
            isLoading: false,
          },
    useFeatureDenied: () => false,
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: snack }),
}));

const renderPicker = () =>
  render(
    <EvalPickerProvider
      source="simulation"
      sourceId="sim-1"
      sourceColumns={[]}
      existingEvals={[]}
      onEvalAdded={() => {}}
      onClose={() => {}}
    >
      <EvalPickerCreateNew onBack={() => {}} onSave={() => {}} />
    </EvalPickerProvider>,
  );

const codeTab = () => screen.getByRole("tab", { name: "Code" });

beforeEach(() => {
  vi.clearAllMocks();
  capturedProps.simulation = null;
  caps.code = CODE_ANSWERS.on;
  caps.agentLocked = false;
});

describe("the picker's Code tab where code evaluations run", () => {
  it.each(["on", "loading", "failed", "noEntry"])(
    "offers Code and opens its editor (%s)",
    async (answer) => {
      caps.code = CODE_ANSWERS[answer];
      renderPicker();
      const tab = codeTab();
      expect(tab).not.toHaveAttribute("aria-disabled");

      fireEvent.click(tab);

      await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
      expect(screen.getByTestId("code-editor")).toBeInTheDocument();
      expect(snack).not.toHaveBeenCalled();
    },
  );
});

describe("the picker's Code tab where code evaluations don't run", () => {
  beforeEach(() => {
    caps.code = CODE_ANSWERS.off;
  });

  it("shows Code turned off, described by the server's sentence", () => {
    renderPicker();
    const tab = codeTab();

    expect(tab).toHaveAttribute("aria-disabled", "true");
    expect(tab).toHaveAccessibleDescription(SERVER_SENTENCE);
    expect(tab).not.toHaveAttribute("disabled");
    tab.focus();
    expect(tab).toHaveFocus();
  });

  it("keeps the current type and says why when Code is chosen", () => {
    renderPicker();
    const tab = codeTab();

    fireEvent.click(tab);

    expect(tab).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByTestId("code-editor")).toBeNull();
    expect(snack).toHaveBeenCalledWith(SERVER_SENTENCE, { variant: "info" });
  });
});

describe("saving an Agents evaluation that isn't enabled, in the picker", () => {
  // The Agents type was chosen before Agents evaluations were locked.
  const saveAgents = async () => {
    renderPicker();
    caps.agentLocked = true;
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Pass if {{output}} is polite." },
    });
    fireEvent.change(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      { target: { value: "my_polite_eval" } },
    );
    await act(async () => {
      capturedProps.simulation.onReadyChange(true, { output: "col-1" });
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Save & Add Evaluation" }),
      );
    });
  };

  it.each(["on", "loading", "failed", "noEntry"])(
    "names Code as an alternative (%s)",
    async (answer) => {
      caps.code = CODE_ANSWERS[answer];

      await saveAgents();

      expect(snack).toHaveBeenCalledWith(AGENTS_REFUSED_WITH_CODE, {
        variant: "error",
      });
    },
  );

  it("leaves Code out where code evaluations don't run", async () => {
    caps.code = CODE_ANSWERS.off;

    await saveAgents();

    expect(snack).toHaveBeenCalledWith(AGENTS_REFUSED, { variant: "error" });
    expect(snack).not.toHaveBeenCalledWith(
      AGENTS_REFUSED_WITH_CODE,
      expect.anything(),
    );
  });
});
