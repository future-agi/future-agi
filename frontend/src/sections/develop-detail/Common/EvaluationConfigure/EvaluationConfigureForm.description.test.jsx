import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import EvaluationConfigureForm from "./EvaluationConfigureForm";
import { generateValidationSchema } from "./validation";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      eval: {
        getPreviouslyConfiguredEvalTemplateConfig: () => "/eval/structure",
        addEval: () => "/eval/add",
        editEval: () => "/eval/edit",
        testEval: () => "/eval/test",
      },
      experiment: { addEval: () => "/experiment/eval/add" },
    },
  },
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureLocked: () => ({ locked: true }),
  CAPABILITY: { TURING_MODELS: "turing_models" },
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  FAGI_MODEL_VALUES: new Set(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("src/components/SpinnerControls/SpinnerControls", () => ({
  SpinnerControls: () => null,
}));
vi.mock("src/components/ComplexFilter/common", () => ({
  handleNumericInput: (value) => value,
}));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("../FieldSelection", () => ({ default: () => null }));
vi.mock("./StringInput", () => ({ default: () => null }));
vi.mock("./BooleanInput", () => ({ default: () => null }));
vi.mock("./OptionInput", () => ({ default: () => null }));
vi.mock("./NumberInput", () => ({ default: () => null }));
vi.mock("./CodeInput", () => ({ default: () => null }));
vi.mock("./PromptInput", () => ({ default: () => null }));
vi.mock("./DictInput", () => ({ default: () => null }));
vi.mock("./RuleStringInput", () => ({ default: () => null }));
vi.mock("./RulePromptInput", () => ({ default: () => null }));
vi.mock("./ListInput", () => ({ default: () => null }));
vi.mock("./ModelSelectInput", () => ({ default: () => null }));
vi.mock("./EvaluationTest", () => ({ default: () => null }));
vi.mock("src/components/ChoiceInput/ChoicesInput", () => ({ default: () => null }));

const templateDescription = "Template instructions, not instance notes.";
const description = "Checks the support dataset.\nEscalated cases only.";
const structure = {
  id: "template-1",
  name: "quality-eval",
  description: templateDescription,
  requiredKeys: [],
  mapping: {},
  config: {},
};
let client;

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  axios.get.mockResolvedValue({ data: { result: { eval: structure } } });
  axios.post.mockResolvedValue({ data: { result: {} } });
});

afterEach(() => {
  cleanup();
  client.clear();
});

function renderForm(props = {}) {
  return render(
    <QueryClientProvider client={client}>
      <EvaluationConfigureForm
        selectedEval={{ id: "template-1", name: "quality-eval", evalType: "preset" }}
        allColumns={[]}
        requiredColumnIds={[]}
        datasetId="dataset-1"
        module="develop"
        onClose={vi.fn()}
        onBackClick={vi.fn()}
        refreshGrid={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

async function save(button = "Save") {
  fireEvent.click(screen.getByRole("button", { name: button, exact: true }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
  return axios.post.mock.calls[0];
}

describe("evaluation instance description", () => {
  it("keeps multiline descriptions through validation and accepts omitted descriptions", () => {
    const schema = generateValidationSchema(structure, [], []);
    const values = {
      name: "quality-eval",
      saveAsTemplate: false,
      config: { mapping: {}, config: {}, reasonColumn: true },
    };
    expect(schema.parse({ ...values, description }).description).toBe(description);
    expect(schema.parse({ ...values, description: "" }).description).toBe("");
    expect(schema.parse(values)).toEqual(values);
  });

  it("shows an optional multiline field below Name without copying template text", async () => {
    renderForm();
    const field = await screen.findByRole("textbox", { name: "Description" });
    expect(field.tagName).toBe("TEXTAREA");
    expect(field).toHaveValue("");
    expect(field).not.toBeRequired();
    expect(screen.getAllByRole("textbox").slice(0, 2)).toEqual([
      screen.getByRole("textbox", { name: "Name" }),
      field,
    ]);
    expect(screen.getByText(templateDescription)).toBeInTheDocument();
  });

  it.each([
    ["dataset Save", {}, "Save", "/eval/add", false],
    ["dataset Save and Run", {}, "Save and Run", "/eval/add", true],
    ["experiment Save and Run", { experimentEval: { experimentId: "experiment-1" } }, "Save and Run", "/experiment/eval/add", true],
    ["dataset edit", { selectedEval: { id: "eval-1", evalType: "user" } }, "Save", "/eval/edit", false],
  ])("includes instance notes in the persisted config for %s", async (_name, props, button, endpoint, run) => {
    renderForm(props);
    fireEvent.change(await screen.findByRole("textbox", { name: "Description" }), {
      target: { value: description },
    });
    const [url, payload] = await save(button);
    expect(url).toBe(endpoint);
    expect(payload).toMatchObject({
      name: "quality-eval",
      run,
      save_as_template: false,
      config: { description, mapping: {}, config: {}, reason_column: true },
    });
    expect(payload).not.toHaveProperty("description");
  });

  describe.each([
    ["Save", false],
    ["Save and Run", true],
  ])("compare evaluation %s", (button, run) => {
    it.each([
      ["multiline", description],
      ["empty", ""],
    ])("includes %s instance notes in the observe callback config", async (_name, notes) => {
      const onFormSave = vi.fn();
      const selectedEval = { id: "template-1", name: "quality-eval", evalType: "preset" };
      axios.get.mockResolvedValue({
        data: {
          result: {
            eval: { ...structure, config: { enabled: { type: "boolean", default: false } } },
          },
        },
      });
      renderForm({ module: "observe", onFormSave, selectedEval });
      fireEvent.change(await screen.findByRole("textbox", { name: "Description" }), {
        target: { value: notes },
      });
      fireEvent.click(screen.getByRole("button", { name: button, exact: true }));
      await waitFor(() => expect(onFormSave).toHaveBeenCalledTimes(1));
      // Compare saves keep the payload they sent before the description field
      // (form keys saveAsTemplate and config.reasonColumn as the form holds
      // them) and only add the notes inside config.
      expect(onFormSave).toHaveBeenCalledWith({
        name: "quality-eval",
        description: notes,
        selectedEval,
        run,
        saveAsTemplate: false,
        config: {
          description: notes,
          mapping: {},
          config: { enabled: false },
          reasonColumn: true,
        },
      });
      const [payload] = onFormSave.mock.calls[0];
      expect(payload.config.description).toBe(notes);
      expect(payload).not.toHaveProperty("save_as_template");
      expect(payload.config).not.toHaveProperty("reason_column");
      expect(axios.post).not.toHaveBeenCalled();
    });
  });

  it("saves when the optional description is left empty", async () => {
    renderForm();
    await screen.findByRole("textbox", { name: "Description" });
    const [, payload] = await save();
    expect(payload.config.description).toBe("");
  });

  it("rehydrates instance notes from the structure response and allows clearing them", async () => {
    axios.get.mockResolvedValue({
      data: { result: { eval: { ...structure, instance_description: description } } },
    });
    renderForm({ selectedEval: { id: "eval-1", evalType: "user" } });
    const field = await screen.findByRole("textbox", { name: "Description" });
    expect(field).toHaveValue(description);
    expect(screen.getByText(templateDescription)).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "" } });
    const [url, payload] = await save();
    expect(url).toBe("/eval/edit");
    expect(payload.config.description).toBe("");
  });

  it("leaves legacy instances without notes blank rather than using template text", async () => {
    renderForm({ selectedEval: { id: "eval-1", evalType: "user" } });
    expect(await screen.findByRole("textbox", { name: "Description" })).toHaveValue("");
    const [, payload] = await save();
    expect(payload.config).toEqual({
      mapping: {}, config: {}, reason_column: true, description: "",
    });
  });

  it("retains notes in the prompt evaluation selection callback", async () => {
    const setEvalsConfigs = vi.fn();
    renderForm({ module: "prompt", evalsConfigs: [], setEvalsConfigs });
    fireEvent.change(await screen.findByRole("textbox", { name: "Description" }), {
      target: { value: description },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(setEvalsConfigs).toHaveBeenCalledWith([
      { id: "template-1", name: "quality-eval", mapping: {}, config: {}, description },
    ]));
    expect(axios.post).not.toHaveBeenCalled();
  });
});
