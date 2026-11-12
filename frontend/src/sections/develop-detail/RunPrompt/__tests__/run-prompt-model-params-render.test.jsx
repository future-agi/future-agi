// Restore row 3: Dataset > Add column > Run Prompt > Model options hands the
// parameter section every parameter the chosen model offers, as production
// (origin/main 24cc6576) does: Top P, presence and frequency penalty, every
// on/off and every dropdown option. The launch build passed only Temperature
// and Max tokens. The parameter section (ModelOptionsState) is a probe that
// lists what Run Prompt gives it; everything else in the form is real or a
// blank stand-in.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { afterEach, describe, expect, it, vi } from "vitest";

const { Null, net, FORM, PassThrough } = vi.hoisted(() => ({
  Null: () => null,
  PassThrough: ({ children }) => children ?? null,
  net: {
    get: vi.fn(() =>
      Promise.resolve({ data: { results: [], result: null } }),
    ),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
  // Form values for a chosen LLM. No provider, so the form does not fetch
  // model parameters; the parameters come from initialModelParams below.
  FORM: {
    name: "Answer",
    config: {
      model: "gpt-4o",
      modelType: "llm",
      concurrency: 5,
      responseFormat: "text",
      tools: [],
      messages: [{ id: "m1", role: "user", content: "Hi {{q}}" }],
      run_prompt_config: {
        modelName: "gpt-4o",
        logoUrl: "",
        providers: "",
        isAvailable: true,
        voice: "",
        voiceId: "",
      },
    },
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: net.get, post: net.post },
  };
});
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetColumnConfig: () => [],
  useDatasetDerivedVariables: () => ({ data: {} }),
  useGetJsonColumnSchema: () => ({ data: {} }),
  useVoiceOptions: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("../executionEligibility", async (importOriginal) => ({
  ...(await importOriginal()),
  useExecutionEligibilityGate: () => async () => true,
}));
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  transformDefaultData: () => JSON.parse(JSON.stringify(FORM)),
}));

// The parameter section: lists what Run Prompt hands it.
vi.mock("../../Common/ModelOptionsState", () => ({
  default: function ModelOptionsProbe({ modelParameters }) {
    return (
      <div data-testid="model-options">
        {(modelParameters?.sliders ?? []).map((s) => (
          <span key={`s-${s.id}`}>{`slider:${s.label}`}</span>
        ))}
        {(modelParameters?.booleans ?? []).map((b) => (
          <span key={`b-${b.id}`}>{`switch:${b.label}`}</span>
        ))}
        {(modelParameters?.dropdowns ?? []).map((d) => (
          <span key={`d-${d.id}`}>{`dropdown:${d.label}`}</span>
        ))}
      </div>
    );
  },
}));

// Blank stand-ins for the rest of the form.
vi.mock("../TestRunPrompt", () => ({ default: Null }));
vi.mock("../ToolConfig/ToolsConfigSection", () => ({ default: Null }));
vi.mock("../Modals/SavePromptModal", () => ({ default: Null }));
vi.mock("../Modals/CustomToolModal", () => ({ default: Null }));
vi.mock("../Modals/ImportPrompt", () => ({ default: Null }));
vi.mock("../Modals/RunPreviewModal", () => ({ default: Null }));
vi.mock("../Components/PromptTemplateSection", () => ({ default: Null }));
vi.mock("../Components/PromptTTSInput", () => ({ default: Null }));
vi.mock("../Components/PromptSTTInput", () => ({ default: Null }));
vi.mock("../Components/PromptImageInput", () => ({ default: Null }));
vi.mock("../Components/ChooseModelType", () => ({ default: Null }));
vi.mock("../../CustomAudioDialog", () => ({ default: Null }));
vi.mock("../../AddColumn/RerunScopeField", () => ({ default: Null }));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: Null }),
);
vi.mock("src/components/GeneratePromptDrawer", () => ({ default: Null }));
vi.mock("src/components/ImprovePromptDrawer", () => ({ default: Null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: Null,
}));
vi.mock("src/components/IncrementerButton/IncrementerButton", () => ({
  default: Null,
}));
vi.mock("src/components/svg-color", () => ({ default: Null }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/tooltip", () => ({ default: PassThrough }));
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    AudioPlaybackProvider: PassThrough,
  }),
);

import { RunPromptForm } from "../RunPrompt";

const DATASET = "0b7c5a52-6f0e-4a8e-9a43-2a1f0e1d3c11";

// What the model-params endpoint returns for a model with Top P, both
// penalties, one on/off and one dropdown option.
const slider = (id, label, min, max, value) => ({
  id,
  label,
  min,
  max,
  step: 0.1,
  default: value,
  value,
});
const MODEL_PARAMS = {
  sliders: [
    slider("temperature", "Temperature", 0, 2, 1),
    slider("maxTokens", "Max Tokens", 1, 4096, 1000),
    slider("topP", "Top P", 0, 1, 1),
    slider("presencePenalty", "Presence Penalty", -2, 2, 0),
    slider("frequencyPenalty", "Frequency Penalty", -2, 2, 0),
  ],
  booleans: [
    { id: "logprobs", label: "Logprobs", default: false, value: false },
  ],
  dropdowns: [
    {
      id: "serviceTier",
      label: "Service Tier",
      options: ["auto", "default"],
      default: "auto",
      value: "auto",
    },
  ],
};

const theme = createTheme({ palette: palette("light") });

const renderForm = () =>
  render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false }, mutations: {} },
          })
        }
      >
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={
                <RunPromptForm
                  onClose={vi.fn()}
                  isConfirmationModalOpen={false}
                  setConfirmationModalOpen={vi.fn()}
                  initialData={JSON.parse(JSON.stringify(FORM))}
                  initialModelParams={JSON.parse(JSON.stringify(MODEL_PARAMS))}
                />
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Run Prompt > Model options", () => {
  it("shows every parameter the chosen model offers", async () => {
    renderForm();
    const button = await screen.findByRole("button", { name: "Model options" });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    const section = await screen.findByTestId("model-options");
    for (const text of [
      "slider:Temperature",
      "slider:Max Tokens",
      "slider:Top P",
      "slider:Presence Penalty",
      "slider:Frequency Penalty",
      "switch:Logprobs",
      "dropdown:Service Tier",
    ]) {
      expect(section).toHaveTextContent(text);
    }
  });
});
