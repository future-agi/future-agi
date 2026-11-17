/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "src/utils/test-utils";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

// A selected version runs the prompt and model it was saved with; Latest is
// saved as the newest version. When that prompt or model has unsaved changes,
// Add asks first: save the changes as a new version and add, or add the
// version as saved. The editors below are plain fields so a test can change
// the prompt and the model; every request goes through the mocked hooks and
// host.

const { preview } = vi.hoisted(() => ({ preview: { onReadyChange: null } }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    const { onReadyChange } = props;
    preview.onReadyChange = onReadyChange;
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
  default: ({ value, onChange, model, onModelChange }) => (
    <div>
      <textarea
        aria-label="Instructions"
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      />
      <span data-testid="judge-model">{model}</span>
      <button type="button" onClick={() => onModelChange("turing_flash")}>
        Use Turing Flash
      </button>
    </div>
  ),
}));

vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange, model, onModelChange }) => (
    <div>
      <span data-testid="judge-model">{model}</span>
      <button type="button" onClick={() => onModelChange("turing_flash")}>
        Use Turing Flash
      </button>
      <textarea
        aria-label="System message"
        value={messages?.[0]?.content || ""}
        onChange={(e) =>
          onMessagesChange([
            { ...messages[0], content: e.target.value },
            ...messages.slice(1),
          ])
        }
      />
      <button
        type="button"
        onClick={() =>
          onMessagesChange([
            ...messages,
            { role: "user", content: "An extra turn" },
          ])
        }
      >
        Add a message
      </button>
    </div>
  ),
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: ({ selectedDatasets, onChange }) => (
    <button
      type="button"
      onClick={() =>
        onChange([...(selectedDatasets || []), { id: "ds-9", name: "Shots" }])
      }
    >
      Pick a few-shot dataset
    </button>
  ),
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

const V2_PROMPT = "Version two {{output}}";
const V1_PROMPT = "Version one {{output}}";
const V4_PROMPT = "Version four {{output}}";
const EDITED = "Edited prompt {{output}}";

const versionTwo = {
  id: "ver-2",
  version_number: 2,
  is_default: true,
  config_snapshot: {
    rule_prompt: V2_PROMPT,
    messages: [{ role: "system", content: V2_PROMPT }],
    few_shot_examples: [],
    output: "score",
    model: "turing_small",
  },
  model: "turing_small",
};
const versionFour = {
  id: "ver-4",
  version_number: 4,
  is_default: true,
  config_snapshot: {
    rule_prompt: V4_PROMPT,
    messages: [{ role: "system", content: V4_PROMPT }],
    few_shot_examples: [],
    output: "score",
    model: "turing_small",
  },
  model: "turing_small",
};
const versionOne = {
  id: "ver-1",
  version_number: 1,
  is_default: false,
  config_snapshot: {
    rule_prompt: V1_PROMPT,
    messages: [{ role: "system", content: V1_PROMPT }],
    few_shot_examples: [],
    output: "score",
    model: "turing_small",
  },
  model: "turing_small",
};

// Hook state the mocks read on every render; a saved version joins the list
// as the real versions query does after its refetch.
const { state, updateEval, createVersion, composite, unionKeys } = vi.hoisted(
  () => ({
    state: { detail: null, versions: null },
    updateEval: { mutate: () => {}, mutateAsync: null, isPending: false },
    createVersion: { mutateAsync: null, isPending: false },
    composite: { data: null },
    unionKeys: [],
  }),
);

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => state.detail,
  useUpdateEval: () => updateEval,
}));

vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => state.versions,
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

const detailFor = (evalType, owner = "user") => ({
  data: {
    id: "tpl-1",
    name: "release_scorer",
    owner,
    eval_type: evalType,
    output_type: "percentage",
    instructions: V2_PROMPT,
    current_version: "V2",
    config: {
      model: "turing_small",
      rule_prompt: V2_PROMPT,
      output: "score",
      required_keys: ["output"],
    },
  },
  isLoading: false,
  isError: false,
});

const pick = (overrides = {}) => ({
  id: "tpl-1",
  templateId: "tpl-1",
  name: "release_scorer",
  mapping: { output: "col-output" },
  pinned_version_id: "ver-2",
  ...overrides,
});

// A saved dataset binding, as the dataset's saved eval list opens it.
const savedBinding = (overrides = {}) =>
  pick({
    userEvalId: "binding-1",
    run_config: { model: "turing_small", pass_threshold: 0.7 },
    ...overrides,
  });

const renderPicker = (evalData, onSave, { edit = false } = {}) =>
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
        initialEval={edit ? evalData : undefined}
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

const promptField = (evalType) =>
  screen.findByRole("textbox", {
    name: evalType === "agent" ? "Instructions" : "System message",
  });

const editPrompt = async (evalType = "llm") => {
  const field = await promptField(evalType);
  fireEvent.change(field, { target: { value: EDITED } });
  return field;
};

const clickPrimary = async (name = "Add Evaluation") => {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

const dialogTitle = "Your prompt edits aren't in V2";
const modelTitle = "Your model change isn't in V2";
const bothTitle = "Your prompt and model changes aren't in V2";

const changeModel = async () => {
  fireEvent.click(
    await screen.findByRole("button", { name: "Use Turing Flash" }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("judge-model")).toHaveTextContent("turing_flash"),
  );
};

describe("Add with an edited prompt on a selected version", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    state.detail = detailFor("llm");
    state.versions = { data: { versions: [versionTwo, versionOne] } };
    updateEval.mutateAsync = vi.fn(async () => ({}));
    createVersion.mutateAsync = vi.fn(async (body) => {
      const listed = state.versions.data.versions;
      const number = listed[0].version_number + 1;
      const saved = {
        id: `ver-${number}`,
        version_number: number,
        is_default: true,
        model: body.model,
        config_snapshot: body.config_snapshot,
      };
      state.versions = { data: { versions: [saved, ...listed] } };
      return { id: saved.id, version_number: number };
    });
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
  });

  it.each(["llm", "agent"])(
    "asks first and sends nothing when the %s prompt was edited",
    async (evalType) => {
      state.detail = detailFor(evalType);
      const onSave = vi.fn();
      renderPicker(pick(), onSave);
      await screen.findByText("Version label: V2");
      await editPrompt(evalType);

      await clickPrimary();

      expect(await screen.findByText(dialogTitle)).toBeInTheDocument();
      expect(
        screen.getByText("V2 runs the prompt and model it was saved with."),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Saves your changes as a new version and adds the evaluation " +
            "with that version.",
        ),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
      expect(createVersion.mutateAsync).not.toHaveBeenCalled();
      expect(updateEval.mutateAsync).not.toHaveBeenCalled();
    },
  );

  it("saves the edit as a new version, then adds with that version", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await editPrompt();
    await clickPrimary();
    await screen.findByText(dialogTitle);

    fireEvent.click(
      screen.getByRole("button", { name: "Save as new version and add" }),
    );

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(createVersion.mutateAsync).toHaveBeenCalledTimes(1);
    const created = createVersion.mutateAsync.mock.calls[0][0];
    expect(created.config_snapshot.rule_prompt).toBe(EDITED);
    expect(created.config_snapshot.messages[0].content).toBe(EDITED);
    const added = onSave.mock.calls[0][0];
    expect(added.versionPin).toBe("v3");
    expect(added.config.version_pin).toBe("v3");
    expect(added.config.rule_prompt).toBe(EDITED);
    expect(createVersion.mutateAsync.mock.invocationCallOrder[0]).toBeLessThan(
      onSave.mock.invocationCallOrder[0],
    );
    expect(screen.queryByText(dialogTitle)).not.toBeInTheDocument();
  });

  it("adds the selected version as saved and shows its prompt again", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await editPrompt();
    await clickPrimary();
    await screen.findByText(dialogTitle);

    fireEvent.click(screen.getByRole("button", { name: "Add V2 as saved" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(createVersion.mutateAsync).not.toHaveBeenCalled();
    expect(updateEval.mutateAsync).not.toHaveBeenCalled();
    const added = onSave.mock.calls[0][0];
    expect(added.versionPin).toBe("v2");
    expect(added.config.version_pin).toBe("v2");
    expect(added.config.rule_prompt).toBe(V2_PROMPT);
    expect(added.config.messages).toEqual([
      { role: "system", content: V2_PROMPT },
    ]);
    expect(added.instructions).toBe(V2_PROMPT);
    expect(JSON.stringify(added)).not.toContain("Edited prompt");
    expect(await promptField("llm")).toHaveValue(V2_PROMPT);
  });

  it("cancel sends nothing and keeps the edits", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await editPrompt();
    await changeModel();
    await clickPrimary();
    await screen.findByText(bothTitle);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByText(bothTitle)).not.toBeInTheDocument(),
    );
    expect(onSave).not.toHaveBeenCalled();
    expect(createVersion.mutateAsync).not.toHaveBeenCalled();
    expect(updateEval.mutateAsync).not.toHaveBeenCalled();
    expect(await promptField("llm")).toHaveValue(EDITED);
    expect(screen.getByTestId("judge-model")).toHaveTextContent("turing_flash");
  });

  it("names a model-only change in the title", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await changeModel();

    await clickPrimary();

    expect(await screen.findByText(modelTitle)).toBeInTheDocument();
    expect(screen.queryByText(dialogTitle)).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("names a prompt and model change together in the title", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await editPrompt();
    await changeModel();

    await clickPrimary();

    expect(await screen.findByText(bothTitle)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("names the version's own model in the as-saved line", async () => {
    renderPicker(pick(), vi.fn());
    await screen.findByText("Version label: V2");
    await changeModel();
    await clickPrimary();
    const dialog = await screen.findByRole("dialog");

    expect(
      screen.getByText(
        "Uses V2's own prompt and its model, Turing Small. Your changes " +
          "are discarded.",
      ),
    ).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent("Turing Flash");
  });

  it("adds the version as saved with its own model back in the field", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await changeModel();
    await clickPrimary();
    await screen.findByText(modelTitle);

    fireEvent.click(screen.getByRole("button", { name: "Add V2 as saved" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(createVersion.mutateAsync).not.toHaveBeenCalled();
    const added = onSave.mock.calls[0][0];
    expect(added.model).toBe("turing_small");
    expect(added.versionPin).toBe("v2");
    expect(added.config.version_pin).toBe("v2");
    expect(screen.getByTestId("judge-model")).toHaveTextContent("turing_small");
  });

  it("saves a model change as a new version, then adds with that version", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await changeModel();
    await clickPrimary();
    await screen.findByText(modelTitle);

    fireEvent.click(
      screen.getByRole("button", { name: "Save as new version and add" }),
    );

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(createVersion.mutateAsync).toHaveBeenCalledTimes(1);
    const [created] = createVersion.mutateAsync.mock.calls[0];
    expect(created.model).toBe("turing_flash");
    expect(created.config_snapshot.model).toBe("turing_flash");
    const added = onSave.mock.calls[0][0];
    expect(added.model).toBe("turing_flash");
    expect(added.versionPin).toBe("v3");
    expect(added.config.version_pin).toBe("v3");
  });

  it("adds at once when no specific version is selected", async () => {
    const onSave = vi.fn();
    renderPicker(pick({ pinned_version_id: undefined }), onSave);
    await editPrompt();

    await clickPrimary();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/Your prompt edits aren't in/)).toBeNull();
    expect(onSave.mock.calls[0][0].config.rule_prompt).toBe(EDITED);
  });

  it("adds at once when only the mapping changed", async () => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await promptField("llm");
    await act(async () =>
      preview.onReadyChange(true, { output: "other-column" }),
    );

    await clickPrimary();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(dialogTitle)).toBeNull();
    expect(onSave.mock.calls[0][0].versionPin).toBe("v2");
  });

  it.each([
    ["a message", "Add a message"],
    ["the few-shot examples", "Pick a few-shot dataset"],
  ])("asks first when only %s changed", async (_label, control) => {
    const onSave = vi.fn();
    renderPicker(pick(), onSave);
    await screen.findByText("Version label: V2");
    await promptField("llm");
    fireEvent.click(screen.getByRole("button", { name: control }));

    await clickPrimary();

    expect(await screen.findByText(dialogTitle)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  describe("for a system evaluation", () => {
    // A system evaluation's prompt is read-only and it cannot be saved as a
    // new version; its header shows no version label.
    beforeEach(() => {
      state.detail = detailFor("llm", "system");
    });
    const systemNote = "System evaluations cannot be saved as a new version.";
    const openWithModelChange = async (onSave) => {
      renderPicker(pick(), onSave);
      await waitFor(() =>
        expect(screen.getByTestId("judge-model")).toHaveTextContent(
          "turing_small",
        ),
      );
      await changeModel();
      await clickPrimary();
      await screen.findByText(modelTitle);
    };

    it("offers only the version as saved, with the system note", async () => {
      const onSave = vi.fn();
      await openWithModelChange(onSave);

      expect(screen.getByText(systemNote)).toBeInTheDocument();
      expect(
        screen.getByText("V2 runs the prompt and model it was saved with."),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Add V2 as saved" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Uses V2's own prompt and its model, Turing Small. Your changes " +
            "are discarded.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save as new version and add" }),
      ).toBeNull();
      expect(
        screen.queryByText(/Saves your changes as a new version/),
      ).toBeNull();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
      expect(onSave).not.toHaveBeenCalled();
    });

    it("adds the version as saved with its own model back in the field", async () => {
      const onSave = vi.fn();
      await openWithModelChange(onSave);

      fireEvent.click(screen.getByRole("button", { name: "Add V2 as saved" }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(createVersion.mutateAsync).not.toHaveBeenCalled();
      const added = onSave.mock.calls[0][0];
      expect(added.model).toBe("turing_small");
      expect(added.versionPin).toBe("v2");
      expect(added.config.version_pin).toBe("v2");
      expect(screen.getByTestId("judge-model")).toHaveTextContent(
        "turing_small",
      );
    });

    it("cancel sends nothing and keeps the model", async () => {
      const onSave = vi.fn();
      await openWithModelChange(onSave);

      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(screen.queryByText(modelTitle)).not.toBeInTheDocument(),
      );
      expect(onSave).not.toHaveBeenCalled();
      expect(createVersion.mutateAsync).not.toHaveBeenCalled();
      expect(screen.getByTestId("judge-model")).toHaveTextContent(
        "turing_flash",
      );
    });
  });

  it("offers the save choice, without the system note, for a user evaluation", async () => {
    renderPicker(pick(), vi.fn());
    await screen.findByText("Version label: V2");
    await changeModel();
    await clickPrimary();
    await screen.findByText(modelTitle);

    expect(
      screen.getByRole("button", { name: "Save as new version and add" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "System evaluations cannot be saved as a new version.",
      ),
    ).toBeNull();
  });

  describe("with Latest selected", () => {
    // Latest is held as a saved pin; it loads the newest version, V4.
    const latest = () => ({
      id: "tpl-1",
      templateId: "tpl-1",
      name: "release_scorer",
      mapping: { output: "col-output" },
      run_config: { version_pin: "latest" },
    });
    beforeEach(() => {
      state.versions = {
        data: { versions: [versionFour, versionTwo, versionOne] },
      };
    });

    it("asks first, naming the newest version", async () => {
      const onSave = vi.fn();
      renderPicker(latest(), onSave);
      await screen.findByText("Version label: V4");
      expect(await promptField("llm")).toHaveValue(V4_PROMPT);
      await editPrompt();

      await clickPrimary();

      expect(
        await screen.findByText("Your prompt edits aren't in V4"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Add V4 as saved" }),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
      expect(createVersion.mutateAsync).not.toHaveBeenCalled();
    });

    it("saves the edit as a new version and adds with that version", async () => {
      const onSave = vi.fn();
      renderPicker(latest(), onSave);
      await screen.findByText("Version label: V4");
      await editPrompt();
      await clickPrimary();
      await screen.findByText("Your prompt edits aren't in V4");

      fireEvent.click(
        screen.getByRole("button", { name: "Save as new version and add" }),
      );

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(createVersion.mutateAsync).toHaveBeenCalledTimes(1);
      const [created] = createVersion.mutateAsync.mock.calls[0];
      expect(created.config_snapshot.rule_prompt).toBe(EDITED);
      const added = onSave.mock.calls[0][0];
      expect(added.versionPin).toBe("v5");
      expect(added.config.version_pin).toBe("v5");
      expect(added.config.rule_prompt).toBe(EDITED);
    });
  });

  it("asks first on Update of a saved binding pinned to a version", async () => {
    const onSave = vi.fn();
    renderPicker(savedBinding(), onSave, { edit: true });
    await screen.findByText("Version label: V2");
    await editPrompt();

    await clickPrimary("Update Evaluation");

    expect(await screen.findByText(dialogTitle)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Update with V2 as saved" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save as new version and update" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Saves your changes as a new version and updates the evaluation " +
          "to that version.",
      ),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
    expect(createVersion.mutateAsync).not.toHaveBeenCalled();
  });
});
