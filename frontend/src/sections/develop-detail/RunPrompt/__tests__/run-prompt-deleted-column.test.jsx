import React from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

// A Run Prompt column whose saved prompt uses a column that was deleted
// later. The drawer is the real default export (React Query, RHF,
// hydration, variable checks and the submit serializer stay real); only
// transport, workspace context and heavyweight leaf controls are mocked, as
// in w3b-e-edit-max-tokens.test.jsx.
const { Null, closeDrawer, LIVE_ID, DEAD_ID, FOREIGN_ID, LIVE_COLUMNS } =
  vi.hoisted(() => {
    const live = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b";
    return {
      Null: () => null,
      closeDrawer: vi.fn(),
      LIVE_ID: live,
      DEAD_ID: "0a9b8c7d-6e5f-4a3b-9c2d-1e0f9a8b7c6d",
      FOREIGN_ID: "d3c2b1a0-9f8e-4d7c-8b6a-5f4e3d2c1b0a",
      LIVE_COLUMNS: [{ headerName: "Question", field: live, dataType: "text" }],
    };
  });

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
  useDatasetColumnConfig: () => LIVE_COLUMNS,
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
vi.mock("../Modals/RunPreviewModal", () => ({ default: PreviewRowsDialog }));
vi.mock("../../CustomAudioDialog", () => ({ default: Null }));
vi.mock("../../AddColumn/RerunScopeField", () => ({ default: Null }));
vi.mock("../../Common/ModelOptionsState", () => ({ default: Null }));

function PassThrough({ children }) {
  return children;
}
PassThrough.propTypes = { children: PropTypes.node };

// The rows dialog Test opens before a preview is sent.
function PreviewRowsDialog({ open }) {
  return open ? <div role="dialog" aria-label="Preview rows" /> : null;
}
PreviewRowsDialog.propTypes = { open: PropTypes.bool };

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

const { default: RunPrompt } = await import("../RunPrompt");
let client;

const ref = (id) => `{{${id}}}`;
// The retrieve config converts live columns to their names; references to a
// deleted (or another dataset's) column stay as the saved {{<id>}}.
const SAVED_TEXT = `Answer {{Question}} like ${ref(DEAD_ID)} or ${ref(FOREIGN_ID)}`;

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

async function openDrawer() {
  const config = {
    name: "Saved prompt",
    model: "fixture-model",
    concurrency: 5,
    response_format: "text",
    messages: [
      { role: "system", content: [{ type: "text", text: "Be concise." }] },
      { role: "user", content: [{ type: "text", text: SAVED_TEXT }] },
    ],
    tools: [],
    run_prompt_config: {
      model_name: "fixture-model", model_type: "llm", providers: "fixture",
    },
    deleted_column_references: [
      { id: DEAD_ID, name: "Old answer" },
      { id: FOREIGN_ID, name: null },
    ],
  };
  const modelParams = { sliders: [], booleans: [], dropdowns: [] };
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
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Prompt text" })).toHaveValue(SAVED_TEXT));
}

function editPosts() {
  return axios.post.mock.calls.filter(([url]) => url === "/run-prompt/edit");
}

function previewPosts() {
  return axios.post.mock.calls.filter(([url]) => url === "/run-prompt/preview");
}

async function updateAndRun() {
  fireEvent.click(screen.getByRole("button", { name: "Update and Run" }));
  await waitFor(() => expect(editPosts()).toHaveLength(1));
  return editPosts()[0][1];
}

describe("a Run Prompt column whose prompt uses a deleted column", () => {
  it("shows each dead reference as a deleted-column chip", async () => {
    await openDrawer();
    expect(await screen.findByText("Deleted column: Old answer")).toBeInTheDocument();
    // Another dataset's column: marked deleted, no name to show.
    expect(screen.getByText("Deleted column")).toBeInTheDocument();
    expect(
      screen.getByText("This prompt uses a column that was deleted. Remove it from the prompt to run this column."),
    ).toBeInTheDocument();
  });

  it("sends an edit that keeps the saved reference unchanged", async () => {
    await openDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Renamed" } });
    const payload = await updateAndRun();
    expect(payload.name).toBe("Renamed");
    expect(payload.column_id).toBe("column-1");
    expect(payload.config.messages[1].content[0].text).toBe(
      `Answer ${ref(LIVE_ID)} like ${ref(DEAD_ID)} or ${ref(FOREIGN_ID)}`,
    );
  });

  it("still stops an edit that uses an unknown variable", async () => {
    await openDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt text" }), {
      target: { value: `Answer {{Nope}} like ${ref(DEAD_ID)}` },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update and Run" }));
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(editPosts()).toHaveLength(0);
  });

  it("drops the chip once the reference is removed from the prompt", async () => {
    await openDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt text" }), {
      target: { value: "Answer {{Question}}" },
    });
    await waitFor(() => expect(screen.queryByText("Deleted column: Old answer")).toBeNull());
    expect(screen.queryByText("Deleted column")).toBeNull();
    const payload = await updateAndRun();
    expect(payload.config.messages[1].content[0].text).toBe(`Answer ${ref(LIVE_ID)}`);
  });

  it("names the deleted column when Test is pressed and sends no preview", async () => {
    await openDrawer();
    const testButton = () => screen.getByRole("button", { name: "Test" });
    await waitFor(() => expect(testButton()).toBeEnabled());
    fireEvent.click(testButton());
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        `This prompt uses deleted columns: Old answer, ${ref(FOREIGN_ID)}. Remove them to test.`,
        { variant: "warning" },
      ),
    );

    // Only the named deleted column left in the prompt.
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt text" }), {
      target: { value: `Answer {{Question}} like ${ref(DEAD_ID)}` },
    });
    await waitFor(() => expect(screen.queryByText("Deleted column")).toBeNull());
    fireEvent.click(testButton());
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "This prompt uses a deleted column: Old answer. Remove it to test.",
        { variant: "warning" },
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.queryByRole("dialog", { name: "Preview rows" })).toBeNull();
    expect(previewPosts()).toHaveLength(0);
  });
});
