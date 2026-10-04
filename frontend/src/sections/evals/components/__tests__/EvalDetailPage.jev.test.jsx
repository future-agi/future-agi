/* eslint-disable react/prop-types */
import React, { forwardRef, useEffect, useImperativeHandle } from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  fireEvent,
} from "src/utils/test-utils";
import {
  buildJevMapping,
  getJevValidationErrors,
} from "../../utils/jevMapping";
import EvalDetailPage from "../EvalDetailPage";

const mocks = vi.hoisted(() => ({
  data: null,
  versions: { versions: [] },
  locked: false,
  update: vi.fn(),
  create: vi.fn(),
  run: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.notify }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: (id) => ({
    locked: id === "jev_models" && mocks.locked,
    isLoading: false,
  }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.data }),
  useUpdateEval: () => ({ mutateAsync: mocks.update }),
  useDuplicateEval: () => ({}),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useCreateEvalVersion: () => ({ mutateAsync: mocks.create }),
  useEvalVersions: () => ({ data: mocks.versions }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({}),
  useUpdateCompositeEval: () => ({}),
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("../LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange, model, onModelChange }) => (
    <>
      <textarea
        aria-label="Instructions"
        value={messages[0]?.content || ""}
        onChange={(e) =>
          onMessagesChange([
            { ...messages[0], content: e.target.value },
            ...messages.slice(1),
          ])
        }
      />
      <select
        aria-label="Evaluator model"
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
      >
        <option value="jev-latest">Jev latest</option>
        <option value="turing_large">Turing Large</option>
      </select>
    </>
  ),
}));
vi.mock("../InstructionEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function Playground(
    { onReadyChange, onVersionSelect },
    ref,
  ) {
    useEffect(() => {
      onReadyChange(true);
    }, [onReadyChange]);
    useImperativeHandle(ref, () => ({ runTest: mocks.run }));
    return (
      <button onClick={() => onVersionSelect(mocks.versions.versions[1])}>
        Load old version
      </button>
    );
  }),
}));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../VersionBadge", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.locked = false;
  mocks.versions = { versions: [] };
  mocks.data = {
    id: "eval",
    name: "Jev eval",
    owner: "user",
    eval_type: "llm",
    instructions: "Check {{input}}",
    output_type: "pass_fail",
    pass_threshold: 0,
    config: { model: "jev-latest" },
  };
  mocks.update.mockResolvedValue({});
  mocks.create.mockResolvedValue({});
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

async function edit() {
  // Initial population deliberately suppresses dirty events for 100 ms.
  await new Promise((resolve) => setTimeout(resolve, 120));
  await userEvent.type(screen.getByLabelText("Instructions"), " updated");
}
const save = () => screen.getByRole("button", { name: "Save Version" });
const test = () => screen.getByRole("button", { name: "Test Evaluation" });
const mapping = (type, extra = {}) => ({
  revision: "jev-map-v1",
  question_type: type,
  pass: null,
  choice: null,
  score: null,
  include_messages: false,
  ...extra,
});

describe("Jev editor save and test contract", () => {
  it("shows typed-decision guidance and saves optional pass criteria with threshold zero in template and version", async () => {
    render(<EvalDetailPage />);
    expect(
      screen.getByText(
        "Jev returns typed decisions with probabilities; no written reasoning.",
      ),
    ).toBeInTheDocument();
    await edit();
    const field = screen.getByLabelText("Describe a pass (true)");
    expect(field).toHaveAttribute("maxlength", "2000");
    await userEvent.type(field, "Accurate answer");
    await userEvent.type(
      screen.getByLabelText("Describe a fail (false)"),
      "Invented answer",
    );
    await userEvent.click(save());
    const expected = mapping("noul", {
      pass: {
        criteria_true: "Accurate answer",
        criteria_false: "Invented answer",
      },
    });
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        model: "jev-latest",
        pass_threshold: 0,
        jev_mapping: expected,
      }),
    );
    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({
        config_snapshot: expect.objectContaining({ jev_mapping: expected }),
      }),
    );
  });

  it("sends the mapping on test auto-save before invoking the playground", async () => {
    render(<EvalDetailPage />);
    await userEvent.click(test());
    await waitFor(() => expect(mocks.run).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        jev_mapping: mapping("noul", {
          pass: { criteria_true: "", criteria_false: "" },
        }),
      }),
    );
    expect(mocks.update.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.run.mock.invocationCallOrder[0],
    );
  });

  it("saves exact single-choice labels, optional descriptions and zero-valued scores", async () => {
    mocks.data.output_type = "deterministic";
    mocks.data.choice_scores = { "Oui ✓": 1, "Non!": 0 };
    render(<EvalDetailPage />);
    await edit();
    await userEvent.type(
      screen.getByLabelText("Description for Oui ✓"),
      "Supported",
    );
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        choice_scores: { "Oui ✓": 1, "Non!": 0 },
        jev_mapping: mapping("choice", {
          choice: {
            labels: ["Oui ✓", "Non!"],
            descriptions: { "Oui ✓": "Supported", "Non!": "" },
          },
        }),
      }),
    );
  });

  it.each([
    ["one", ["Low"]],
    ["eleven", Array.from({ length: 11 }, (_, i) => `Level ${i}`)],
    ["duplicate", ["Low", "Low"]],
    ["blank", ["Low", "  "]],
  ])("blocks Save and Test for %s rubric levels", async (_, levels) => {
    mocks.data.output_type = "percentage";
    mocks.data.config.jev_mapping = mapping("score", { score: { levels } });
    render(<EvalDetailPage />);
    await edit();
    expect(
      screen.getByText("Jev rubric score requires 2 to 10 ordered levels"),
    ).toBeInTheDocument();
    expect(save()).toBeDisabled();
    expect(test()).toBeDisabled();
    fireEvent.click(save());
    fireEvent.click(test());
    expect(mocks.update).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
  });

  it("accepts two levels and saves edits and reordering in order", async () => {
    mocks.data.output_type = "percentage";
    mocks.data.config.jev_mapping = mapping("score", {
      score: { levels: ["Low"] },
    });
    render(<EvalDetailPage />);
    await edit();
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 1"), "High");
    expect(save()).toBeEnabled();
    expect(test()).toBeEnabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Move level 1 up" }),
    );
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        jev_mapping: mapping("score", { score: { levels: ["High", "Low"] } }),
      }),
    );
  });

  it.each([
    ["Multi-choice", { multi_choice: true }],
    ["Tools", { config: { tools: { search: true } } }],
    ["Knowledge bases", { config: { knowledge_bases: ["kb"] } }],
    ["Knowledge bases", { config: { knowledge_base_id: "kb" } }],
    ["Internet check", { config: { check_internet: true } }],
    ["Media inputs", { input_data_types: ["image"] }],
    [
      "Agent mode (agent)",
      { eval_type: "agent", config: { agent_mode: "agent" } },
    ],
    ["Few-shot examples", { config: { few_shot_examples: ["dataset"] } }],
    [
      "Automatic context",
      { config: { data_injection: { trace_context: true } } },
    ],
  ])(
    "blocks incompatible %s without mutating the template",
    async (feature, overrides) => {
      mocks.data = {
        ...mocks.data,
        ...overrides,
        config: { ...mocks.data.config, ...overrides.config },
      };
      const original = structuredClone(mocks.data);
      render(<EvalDetailPage />);
      await edit();
      expect(
        screen.getByText(
          `${feature} is not supported with Jev models. Nothing was changed or removed from your template.`,
        ),
      ).toBeInTheDocument();
      expect(save()).toBeDisabled();
      expect(test()).toBeDisabled();
      expect(mocks.data).toEqual(original);
      expect(mocks.update).not.toHaveBeenCalled();
    },
  );

  it("requires explicit confirmation for extra messages and preserves them in the payload", async () => {
    mocks.data.config.messages = [
      { role: "system", content: "Check {{input}}" },
      { role: "user", content: "Extra context" },
    ];
    render(<EvalDetailPage />);
    await edit();
    expect(save()).toBeDisabled();
    expect(test()).toBeDisabled();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Include extra messages/ }),
    );
    expect(save()).toBeEnabled();
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: expect.arrayContaining([
          { role: "user", content: "Extra context" },
        ]),
        jev_mapping: expect.objectContaining({ include_messages: true }),
      }),
    );
  });

  it("retains a denied saved Jev model, permits saving, and disables execution", async () => {
    mocks.locked = true;
    render(<EvalDetailPage />);
    await edit();
    expect(screen.getByLabelText("Evaluator model")).toHaveValue("jev-latest");
    expect(test()).toBeDisabled();
    expect(save()).toBeEnabled();
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ model: "jev-latest" }),
    );
  });

  it("loads the selected version mapping instead of the template mapping", async () => {
    mocks.data.output_type = "percentage";
    mocks.data.config.jev_mapping = mapping("score", {
      score: { levels: ["Template low", "Template high"] },
    });
    mocks.versions = {
      versions: [
        {
          id: "v2",
          version_number: 2,
          is_default: true,
          model: "jev-latest",
          criteria: "Check {{input}}",
          config_snapshot: {
            model: "jev-latest",
            output: "score",
            jev_mapping: mapping("score", {
              score: { levels: ["New low", "New high"] },
            }),
          },
        },
        {
          id: "v1",
          version_number: 1,
          model: "jev-latest",
          criteria: "Check {{input}}",
          config_snapshot: {
            model: "jev-latest",
            output: "score",
            jev_mapping: mapping("score", {
              score: { levels: ["Old low", "Old high"] },
            }),
          },
        },
      ],
    };
    render(<EvalDetailPage />);
    expect(screen.getByLabelText("Level 0")).toHaveValue("New low");
    await userEvent.click(
      screen.getByRole("button", { name: "Load old version" }),
    );
    expect(screen.getByLabelText("Level 0")).toHaveValue("Old low");
  });

  it("exposes the existing threshold control for Jev Pass/Fail", async () => {
    render(<EvalDetailPage />);
    await edit();
    const slider = screen.getByRole("slider", { name: "Pass threshold" });
    expect(slider).toHaveValue("0");
    fireEvent.change(slider, { target: { value: "70" } });
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ pass_threshold: 0.7 }),
    );
  });

  it("blocks numeric rubrics with existing choice scores without removing them", async () => {
    mocks.data.output_type = "percentage";
    mocks.data.choice_scores = { Low: 0, High: 1 };
    mocks.data.config.jev_mapping = mapping("score", {
      score: { levels: ["Low", "High"] },
    });
    render(<EvalDetailPage />);
    await edit();
    expect(
      screen.getByText(
        "Jev numeric rubrics cannot use choice scores. Remove the choice scores explicitly or choose another model.",
      ),
    ).toBeInTheDocument();
    expect(save()).toBeDisabled();
    expect(test()).toBeDisabled();
    expect(mocks.data.choice_scores).toEqual({ Low: 0, High: 1 });
  });

  it("does not carry few-shot examples from a previous version into an empty version", async () => {
    mocks.versions = {
      versions: [
        {
          id: "v2",
          version_number: 2,
          is_default: true,
          criteria: "Check {{input}}",
          model: "jev-latest",
          config_snapshot: {
            output: "Pass/Fail",
            few_shot_examples: ["dataset"],
          },
        },
        {
          id: "v1",
          version_number: 1,
          criteria: "Check {{input}}",
          model: "jev-latest",
          config_snapshot: { output: "Pass/Fail" },
        },
      ],
    };
    render(<EvalDetailPage />);
    expect(test()).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Load old version" }),
    );
    expect(test()).toBeEnabled();
  });

  it.each([{}, { Same: 0, same: 1 }])(
    "blocks invalid single-choice labels",
    async (scores) => {
      mocks.data.output_type = "deterministic";
      mocks.data.choice_scores = scores;
      render(<EvalDetailPage />);
      expect(
        screen.getByText(
          "Jev single choice requires 1 to 255 unique, non-empty labels.",
        ),
      ).toBeInTheDocument();
      expect(test()).toBeDisabled();
    },
  );

  it("rejects more than 255 labels before submission", () => {
    const scores = Object.fromEntries(
      Array.from({ length: 256 }, (_, i) => [`Label ${i}`, 0]),
    );
    expect(
      getJevValidationErrors({
        mapping: buildJevMapping("deterministic", scores),
        config: {},
        messages: [],
      }),
    ).toContain(
      "Jev single choice requires 1 to 255 unique, non-empty labels.",
    );
  });

  it("preserves the editable threshold on existing non-Jev system evaluations", () => {
    mocks.data.owner = "system";
    mocks.data.output_type = "percentage";
    mocks.data.config.model = "turing_large";
    render(<EvalDetailPage />);
    expect(screen.getByRole("slider", { name: "Pass threshold" })).toBeEnabled();
  });

  it("leaves non-Jev payloads and UI unchanged", async () => {
    mocks.data.config.model = "turing_large";
    render(<EvalDetailPage />);
    await edit();
    expect(screen.queryByText(/Jev returns typed/)).not.toBeInTheDocument();
    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalled();
    expect(mocks.update.mock.calls[0][0]).not.toHaveProperty("jev_mapping");
  });
});
