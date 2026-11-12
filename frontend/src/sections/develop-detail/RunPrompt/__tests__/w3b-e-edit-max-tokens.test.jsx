import React from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";

// Exercise the default drawer export, not an extracted copy of its loader.
// Keep React Query, RHF, validation, hydration and both serializers real;
// only transport, workspace context and heavyweight leaf controls are mocked.
const { Null, closeDrawer } = vi.hoisted(() => ({
  Null: () => null,
  closeDrawer: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      modelParams: "/model-params",
      runPrompt: {
        getRunPrompt: () => "/run-prompt/config",
        editRunPrompt: () => "/run-prompt/edit",
        preview: "/run-prompt/preview",
        create: "/run-prompt/create",
        responseSchema: "/response-schema",
      },
    },
  },
}));
vi.mock("react-router", () => ({ useParams: () => ({ dataset: "dataset-1" }) }));
vi.mock("../../states", () => ({
  useRunPromptStore: () => ({
    openRunPrompt: { id: "column-1" },
    setOpenRunPrompt: closeDrawer,
  }),
  useCustomAudioDialog: () => ({
    isCustomAudioModalOpen: false,
    setIsCustomAudioModalOpen: vi.fn(),
  }),
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => [],
  useGetJsonColumnSchema: () => ({ data: {} }),
  useDatasetDerivedVariables: () => ({ data: {} }),
  useVoiceOptions: () => ({ data: { voices: [] }, isLoading: false }),
}));
vi.mock("../executionEligibility", () => ({
  useExecutionEligibilityGate: () => async () => true,
}));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(), Events: {}, PropertyName: {},
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/svg-color", () => ({ default: Null }));
vi.mock("src/components/tooltip", () => ({ default: PassThrough }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: Null }));
vi.mock("src/components/IncrementerButton/IncrementerButton", () => ({ default: Null }));
vi.mock("src/components/GeneratePromptDrawer", () => ({ default: Null }));
vi.mock("src/components/ImprovePromptDrawer", () => ({ default: Null }));
vi.mock("src/components/FromSearchSelectField", () => ({ FormSearchSelectFieldControl: Null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdownControl", () => ({ default: Null }));
vi.mock("src/components/custom-audio/context-provider/AudioPlaybackContext", () => ({
  AudioPlaybackProvider: PassThrough,
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({ default: TextInput }));
vi.mock("../Components/PromptTemplateSection", () => ({ default: PromptText }));
vi.mock("../Components/PromptTTSInput", () => ({ default: Null }));
vi.mock("../Components/PromptSTTInput", () => ({ default: Null }));
vi.mock("../Components/PromptImageInput", () => ({ default: Null }));
vi.mock("../Components/ChooseModelType", () => ({ default: Null }));
vi.mock("../ToolConfig/ToolsConfigSection", () => ({ default: Null }));
vi.mock("../TestRunPrompt", () => ({ default: Null }));
vi.mock("../Modals/SavePromptModal", () => ({ default: Null }));
vi.mock("../Modals/CustomToolModal", () => ({ default: Null }));
vi.mock("../Modals/ImportPrompt", () => ({ default: Null }));
vi.mock("../Modals/RunPreviewModal", () => ({ default: PreviewRows }));
vi.mock("../../CustomAudioDialog", () => ({ default: Null }));
vi.mock("../../AddColumn/RerunScopeField", () => ({ default: Null }));
vi.mock("../../Common/ModelOptionsState", () => ({ default: Options }));

function PassThrough({ children }) {
  return children;
}
PassThrough.propTypes = { children: PropTypes.node };

function TextInput({ control, fieldName, label }) {
  return (
    <Controller
      control={control}
      name={fieldName}
      render={({ field }) => <input {...field} aria-label={label} value={field.value ?? ""} />}
    />
  );
}
TextInput.propTypes = {
  control: PropTypes.object.isRequired,
  fieldName: PropTypes.string.isRequired,
  label: PropTypes.string,
};

function PromptText({ control }) {
  return <TextInput control={control} fieldName="config.messages.1.content.0.text" label="Prompt text" />;
}
PromptText.propTypes = { control: PropTypes.object.isRequired };

function Options({ modelParameters, setModelParameters, onClear, onApply, disableActions }) {
  const slider = modelParameters.sliders.find((item) => item.label === "max_tokens");
  return (
    <div>
      <input
        aria-label="Max tokens"
        type="number"
        value={slider?.value ?? ""}
        onChange={(event) => {
          const value = event.target.value === "" ? null : Number(event.target.value);
          setModelParameters((previous) => ({
            ...previous,
            sliders: previous.sliders.map((item) =>
              item.label === "max_tokens" ? { ...item, value } : item,
            ),
          }));
        }}
      />
      <button type="button" onClick={onClear}>Clear options</button>
      <button type="button" disabled={disableActions} onClick={onApply}>Apply options</button>
    </div>
  );
}
Options.propTypes = {
  modelParameters: PropTypes.shape({ sliders: PropTypes.array.isRequired }).isRequired,
  setModelParameters: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  disableActions: PropTypes.bool,
};

function PreviewRows({ open, onRun }) {
  return open ? (
    <button type="button" onClick={() => onRun({ first_n_rows: 1 })}>Preview first row</button>
  ) : null;
}
PreviewRows.propTypes = { open: PropTypes.bool, onRun: PropTypes.func.isRequired };

const { default: RunPrompt } = await import("../RunPrompt");
let client;

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

async function openDrawer(saved = { max_tokens: 64 }, sliderOverrides = {}) {
  const config = {
    name: "Saved prompt",
    model: "fixture-model",
    concurrency: 5,
    response_format: "text",
    messages: [
      { role: "system", content: [{ type: "text", text: "Be concise." }] },
      { role: "user", content: [{ type: "text", text: "Summarize this." }] },
    ],
    tools: [],
    run_prompt_config: {
      model_name: "fixture-model", model_type: "llm", providers: "fixture",
      ...saved,
    },
  };
  const modelParams = {
    sliders: [{ label: "max_tokens", min: 1, max: 65536, step: 1, default: null, ...sliderOverrides }],
    booleans: [], dropdowns: [],
  };
  axios.get.mockImplementation(async (url) => {
    if (url === "/run-prompt/config") return { data: { result: { config } } };
    if (url === "/model-params") return { data: { result: modelParams } };
    if (url === "/response-schema") return { data: { results: [] } };
    throw new Error(`Unexpected request: ${url}`);
  });
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  render(<QueryClientProvider client={client}><RunPrompt /></QueryClientProvider>);
  await screen.findByRole("button", { name: "Update and Run" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Model options" })).toBeEnabled());
  expect(axios.get).toHaveBeenCalledWith("/run-prompt/config", {
    params: { column_id: "column-1" },
  });
}

async function openOptions() {
  fireEvent.click(screen.getByRole("button", { name: "Model options" }));
  return screen.findByRole("spinbutton", { name: "Max tokens" });
}

async function submit(kind = "edit") {
  if (kind === "preview") {
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    fireEvent.click(await screen.findByRole("button", { name: "Preview first row" }));
  } else {
    fireEvent.click(await screen.findByRole("button", { name: "Update and Run" }));
  }
  await waitFor(() => expect(axios.post).toHaveBeenCalledWith(
    `/run-prompt/${kind}`, expect.any(Object),
  ));
  const calls = axios.post.mock.calls.filter(([url]) => url === `/run-prompt/${kind}`);
  expect(calls).toHaveLength(1);
  return calls[0][1];
}

function expectLimit(payload, expected) {
  expect(payload.config.run_prompt_config).toHaveProperty("max_tokens", expected);
  expect(payload.config.run_prompt_config).not.toHaveProperty("maxTokens");
}

const hydrationCases = [
  ["canonical", { max_tokens: 64 }, {}, 64],
  ["legacy camelCase", { maxTokens: 48 }, {}, 48],
  ["canonical wins conflicts", { max_tokens: 64, maxTokens: 256 }, {}, 64],
  ["canonical null beats legacy and default", { max_tokens: null, maxTokens: 256 }, { default: 512 }, null],
  ["legacy null beats default", { maxTokens: null }, { default: 512 }, null],
  ["missing uses model default", {}, { default: 512 }, 512],
  ["missing uses model value before default", {}, { value: 96, default: 512 }, 96],
];

describe("editing a Run Prompt column preserves its saved token ceiling", () => {
  // RED on the pinned bases: canonical 64 is absent from the camel-only
  // lookup, so the drawer displays an empty control and serializes null.
  it.each(["Name", "Prompt text"])("retains 64 after a %s-only update without opening options", async (label) => {
    await openDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value: "Changed only this field" } });
    const payload = await submit();
    expectLimit(payload, 64);
    expect(payload.column_id).toBe("column-1");
    expect(payload.dataset_id).toBe("dataset-1");
    if (label === "Name") expect(payload.name).toBe("Changed only this field");
    else expect(payload.config.messages[1].content[0].text).toBe("Changed only this field");
  });

  it.each(["Name", "Prompt text"])("retains 64 in preview and then update after a %s-only edit", async (label) => {
    await openDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value: "Preview this edit" } });
    const preview = await submit("preview");
    expectLimit(preview, 64);
    expect(preview.first_n_rows).toBe(1);
    expectLimit(await submit(), 64);
  });

  it.each(hydrationCases)("displays %s in the initial model options", async (_name, saved, slider, expected) => {
    await openDrawer(saved, slider);
    expect(await openOptions()).toHaveValue(expected);
  });

  it.each(hydrationCases)("round-trips %s without touching options", async (_name, saved, slider, expected) => {
    await openDrawer(saved, slider);
    expectLimit(await submit(), expected);
  });

  it("allows an explicit new ceiling instead of restoring the saved one", async () => {
    await openDrawer();
    fireEvent.change(await openOptions(), { target: { value: "32" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply options" }));
    expectLimit(await submit(), 32);
  });

  it("submits null when the user explicitly clears and applies model options", async () => {
    await openDrawer();
    await openOptions();
    fireEvent.click(screen.getByRole("button", { name: "Clear options" }));
    expect(screen.getByRole("spinbutton", { name: "Max tokens" })).toHaveValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Apply options" }));
    expectLimit(await submit("preview"), null);
    expectLimit(await submit(), null);
  });

  it("does not apply a draft clear when model options are dismissed", async () => {
    await openDrawer();
    const input = await openOptions();
    fireEvent.click(screen.getByRole("button", { name: "Clear options" }));
    fireEvent.keyDown(input, { key: "Escape", code: "Escape" });
    expectLimit(await submit(), 64);
  });
});
