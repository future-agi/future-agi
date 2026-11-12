import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { evalDetailQuery } from "src/sections/evals/hooks/useEvalDetail";
import { EvalPickerContext } from "../context/EvalPickerContext";
import EvalPickerCreateNew from "../EvalPickerCreateNew";

const { api, enqueueSnackbar } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  enqueueSnackbar: vi.fn(),
}));

// Keep the real create/update/detail hooks and FewShotExamples selector.
// Only the transport is replaced; JSON serialization below drops undefined
// exactly as a request would before the backend's partial-update contract.
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    develop: {
      getDatasets: () => "/test/datasets",
      eval: {
        createEvalTemplateV2: "/test/evals",
        updateEvalTemplate: (id) => `/test/evals/${id}`,
        getEvalDetail: (id) => `/test/evals/${id}`,
      },
    },
  },
}));

vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing_models", AGENTIC_EVAL: "agentic_eval" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));
vi.mock("src/utils/prototype", () => ({ isPrototypeMode: () => false }));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar }),
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/sections/tasks/components/TaskLivePreview", () => ({
  buildApiFilterArray: () => [],
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(["turing_large"]),
}));
vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Agent instructions"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange }) => (
    <textarea
      aria-label="LLM instructions"
      value={messages.find((message) => message.role === "system")?.content || ""}
      onChange={(event) =>
        onMessagesChange([{ role: "system", content: event.target.value }])
      }
    />
  ),
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const DatasetTestMode = React.forwardRef(({ onReadyChange }, ref) => (
    <button
      ref={ref}
      type="button"
      onClick={() => onReadyChange(true, { output: "output-column" })}
    >
      Map inputs
    </button>
  ));
  DatasetTestMode.displayName = "DatasetTestModeMock";
  return { default: DatasetTestMode };
});
vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CompositeDetailPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/TracingTestMode", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SimulationTestMode", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/TestPlayground", () => ({
  default: () => null,
}));
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

const DRAFT_ID = "draft-fewshot";
const DATASET_A = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "dataset_a",
  row_count: 7,
};
const SAVED_SELECTION = [{ id: DATASET_A.id, name: DATASET_A.name }];
const INSTRUCTIONS = "Pass if {{output}} is polite.";
const jsonCopy = (value) => JSON.parse(JSON.stringify(value));

let queryClient;
let storedDraft;
let updateRequests;

const advance = async (milliseconds) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
};

async function renderEditor() {
  const onSave = vi.fn();
  function EditorHarness() {
    const filterForm = useForm({ defaultValues: { filters: [] } });
    return (
      <EvalPickerContext.Provider
        value={{
          source: "dataset",
          sourceId: "source-dataset",
          sourceColumns: [],
          filterForm,
        }}
      >
        <EvalPickerCreateNew onBack={() => {}} onSave={onSave} />
      </EvalPickerContext.Provider>
    );
  }

  await act(async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <EditorHarness />
      </QueryClientProvider>,
    );
  });
  await advance(1);
  expect(screen.getByText("Draft")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("e.g. hallucination_detector"), {
    target: { value: "fewshot_regression" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Map inputs" }));
  return onSave;
}

async function selectDatasetAndAutosave() {
  fireEvent.click(screen.getByRole("tab", { name: "LLM-As-A-Judge" }));
  fireEvent.change(screen.getByLabelText("LLM instructions"), {
    target: { value: INSTRUCTIONS },
  });
  await advance(1);
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(screen.getByRole("option", { name: /dataset_a/ }));
  expect(
    screen.getByText(DATASET_A.name, { selector: ".MuiChip-label" }),
  ).toBeInTheDocument();
  await advance(800);
  expect(updateRequests).toHaveLength(1);
  // UI-only metadata such as row_count must not leak into the saved list.
  expect(updateRequests[0].few_shot_examples).toEqual(SAVED_SELECTION);
  expect((await evalDetailQuery(DRAFT_ID).queryFn()).config.few_shot_examples)
    .toEqual(SAVED_SELECTION);
}

function removeLastChip() {
  const chip = screen
    .getByText(DATASET_A.name, { selector: ".MuiChip-label" })
    .closest(".MuiChip-root");
  const deleteIcon = chip.querySelector(".MuiChip-deleteIcon");
  expect(deleteIcon).not.toBeNull();
  fireEvent.click(deleteIcon);
  expect(
    screen.queryByText(DATASET_A.name, { selector: ".MuiChip-label" }),
  ).not.toBeInTheDocument();
}

async function publish(onSave) {
  const saveButton = screen.getByRole("button", {
    name: "Save & Add Evaluation",
  });
  expect(saveButton).toBeEnabled();
  await act(async () => {
    fireEvent.click(saveButton);
  });
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(enqueueSnackbar).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.useFakeTimers();
  api.get.mockReset();
  api.post.mockReset();
  api.put.mockReset();
  enqueueSnackbar.mockReset();
  updateRequests = [];
  storedDraft = null;
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false, gcTime: Infinity },
    },
  });

  api.post.mockImplementation(async (url, payload) => {
    expect(url).toBe("/test/evals");
    expect(payload.is_draft).toBe(true);
    storedDraft = {
      id: DRAFT_ID,
      is_draft: true,
      config: { few_shot_examples: [] },
    };
    return { data: { result: jsonCopy(storedDraft) } };
  });
  api.put.mockImplementation(async (url, payload) => {
    expect(url).toBe(`/test/evals/${DRAFT_ID}`);
    const wire = jsonCopy(payload);
    updateRequests.push(wire);
    // Stateful contract double for separate_evals.py: omitted/null keeps the
    // previous config; an explicit [] replaces it. This is not a backend test.
    if (wire.few_shot_examples != null) {
      storedDraft.config.few_shot_examples = wire.few_shot_examples;
    }
    storedDraft.eval_type = wire.eval_type;
    if (wire.publish) storedDraft.is_draft = false;
    return { data: { result: jsonCopy(storedDraft) } };
  });
  api.get.mockImplementation(async (url) => {
    if (url === "/test/datasets") {
      return { data: { result: { datasets: [DATASET_A] } } };
    }
    expect(url).toBe(`/test/evals/${DRAFT_ID}`);
    return { data: { result: jsonCopy(storedDraft) } };
  });
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("G2F-evals-01: clearing the final few-shot dataset", () => {
  it("sends [] in both autosave and publish and reads back an empty list", async () => {
    const onSave = await renderEditor();
    await selectDatasetAndAutosave();

    removeLastChip();
    await advance(799);
    expect(updateRequests).toHaveLength(1);
    await advance(1);
    expect(updateRequests).toHaveLength(2);
    expect(updateRequests[1]).toMatchObject({
      eval_type: "llm",
      few_shot_examples: [],
    });
    expect(updateRequests[1]).not.toHaveProperty("publish");
    expect((await evalDetailQuery(DRAFT_ID).queryFn()).config.few_shot_examples)
      .toEqual([]);

    await publish(onSave);
    expect(updateRequests).toHaveLength(3);
    expect(updateRequests[2]).toMatchObject({
      eval_type: "llm",
      publish: true,
      few_shot_examples: [],
    });
    const detail = await evalDetailQuery(DRAFT_ID).queryFn();
    expect(detail.is_draft).toBe(false);
    expect(detail.config.few_shot_examples).toEqual([]);
  });

  it("also clears on publish without waiting for the pending autosave", async () => {
    const onSave = await renderEditor();
    await selectDatasetAndAutosave();

    removeLastChip();
    await publish(onSave);
    expect(updateRequests).toHaveLength(2);
    expect(updateRequests[1]).toMatchObject({
      publish: true,
      few_shot_examples: [],
    });
    expect((await evalDetailQuery(DRAFT_ID).queryFn()).config.few_shot_examples)
      .toEqual([]);
  });

  it.each([false, true])(
    "omits few-shot data for agent autosave/publish (retained LLM selection=%s)",
    async (hasRetainedSelection) => {
      const onSave = await renderEditor();
      if (hasRetainedSelection) {
        await selectDatasetAndAutosave();
        fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
      } else {
        fireEvent.change(screen.getByLabelText("Agent instructions"), {
          target: { value: INSTRUCTIONS },
        });
      }
      const previousRequests = updateRequests.length;
      await advance(800);
      await publish(onSave);

      const agentRequests = updateRequests.slice(previousRequests);
      expect(agentRequests).toHaveLength(2);
      expect(agentRequests[0]).not.toHaveProperty("publish");
      expect(agentRequests[1].publish).toBe(true);
      for (const request of agentRequests) {
        expect(request.eval_type).toBe("agent");
        expect(request).not.toHaveProperty("few_shot_examples");
        expect(request).not.toHaveProperty("messages");
      }
      expect((await evalDetailQuery(DRAFT_ID).queryFn()).config.few_shot_examples)
        .toEqual(hasRetainedSelection ? SAVED_SELECTION : []);
    },
  );
});
