/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PromptNodeForm from "../PromptNodeForm";
import { useAgentPlaygroundStore } from "../../../../store";

const mockEnsureDraft = vi.fn();
const mockPartialUpdate = vi.fn();
const mockBuildPayload = vi.fn(() => ({ promptConfig: [] }));

vi.mock("../usePromptNodeForm", () => ({
  usePromptNodeForm: () => ({
    control: {},
    modelConfig: { model: "gpt-4o-mini" },
    isModelSelected: true,
    isUnsupportedOutputFormat: false,
    responseFormatMenuItems: [],
    modelParameters: {},
    updateSliderParameter: vi.fn(),
    updateBooleanParameter: vi.fn(),
    updateDropdownParameter: vi.fn(),
    updateReasoningSliderParameter: vi.fn(),
    updateReasoningDropdownParameter: vi.fn(),
    updateShowReasoningProcess: vi.fn(),
    showCreateSchema: false,
    setShowCreateSchema: vi.fn(),
    isParamsPopoverOpen: false,
    paramsAnchorEl: null,
    handleParamsClick: vi.fn(),
    handleParamsClose: vi.fn(),
    handleModelChange: vi.fn(),
    handleToolsApply: vi.fn(),
    buildPayload: mockBuildPayload,
    responseFormatField: { onChange: vi.fn() },
    isLoadingQueries: false,
  }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetPromptVersionsInfinite: () => ({ isLoading: false }),
}));

vi.mock("src/components/custom-model-options/CreateResponseSchema", () => ({
  default: () => null,
}));

vi.mock("src/sections/agent-playground/components/PromptNameRow", () => ({
  default: () => <div data-testid="prompt-name-row" />,
}));

vi.mock("src/sections/agent-playground/components/ModelSelectionRow", () => ({
  default: () => <div data-testid="model-selection-row" />,
}));

vi.mock("src/sections/agent-playground/components/OutputToolsRow", () => ({
  default: () => <div data-testid="output-tools-row" />,
}));

vi.mock(
  "src/sections/agent-playground/components/ModelParametersPopover",
  () => ({
    default: () => null,
  }),
);

vi.mock("../../../../components/PromptMessageRow", () => ({
  default: () => <div data-testid="prompt-message-row" />,
}));

vi.mock("../../../../components/VariableAccessInfo", () => ({
  default: () => <div data-testid="variable-access-info" />,
}));

vi.mock("../../NodeDrawerSkeleton", () => ({
  default: () => <div data-testid="node-drawer-skeleton" />,
}));

vi.mock("../../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: mockEnsureDraft }),
}));

vi.mock("../../../hooks/usePartialNodeUpdate", () => ({
  default: () => ({ partialUpdate: mockPartialUpdate, isPending: false }),
}));

vi.mock("../../../../hooks/useConnectedNodeVariables", () => ({
  default: () => ({
    dropdownOptions: [],
    validateVariable: () => true,
    isLoading: false,
  }),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

function FormWrapper({ children, defaultValues }) {
  const methods = useForm({ defaultValues });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <FormProvider {...methods}>{children}</FormProvider>
    </QueryClientProvider>
  );
}

function renderForm() {
  return render(
    <FormWrapper
      defaultValues={{
        name: "Source",
        version: "prompt-version",
        prompt_version_id: "prompt-version-id",
        prompt_template_id: "prompt-template-id",
        templateFormat: "mustache",
        modelConfig: { model: "gpt-4o-mini", responseFormat: "text" },
        messages: [
          {
            id: "m-user",
            role: "user",
            content: [{ type: "text", text: "Hello" }],
          },
        ],
      }}
    >
      <PromptNodeForm nodeId="source-node" />
    </FormWrapper>,
  );
}

function seedGraph({ isDraft }) {
  const sourceNode = {
    id: "source-node",
    data: {
      label: "Source",
      ports: [
        {
          id: "source-output",
          direction: "output",
          display_name: "response",
        },
      ],
      config: { messages: [] },
    },
  };
  useAgentPlaygroundStore.getState().reset();
  useAgentPlaygroundStore.setState({
    currentAgent: {
      id: "graph-id",
      version_id: "version-id",
      is_draft: isDraft,
    },
    nodes: [sourceNode],
    edges: [],
    selectedNode: sourceNode,
  });
}

const savedToast = () =>
  enqueueSnackbar.mock.calls.filter(([message]) => message === "Prompt saved");

const pressSave = () =>
  fireEvent.click(screen.getByRole("button", { name: /save prompt/i }));

// Save prompt closes the node drawer. Without a word the user could not tell
// a save from a dismiss, so every save that lands says "Prompt saved", and a
// save that fails says only what failed.
describe("PromptNodeForm Save prompt confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('says "Prompt saved" when saving creates the agent\'s draft', async () => {
    seedGraph({ isDraft: false });
    mockEnsureDraft.mockResolvedValue("created");

    renderForm();
    pressSave();

    await waitFor(() => expect(savedToast()).toHaveLength(1));
    expect(savedToast()[0][1]).toEqual({ variant: "success" });
    expect(mockPartialUpdate).not.toHaveBeenCalled();
    expect(useAgentPlaygroundStore.getState().selectedNode).toBeNull();
  });

  it('says "Prompt saved" when the node is saved into an existing draft', async () => {
    seedGraph({ isDraft: true });
    mockEnsureDraft.mockResolvedValue(true);
    mockPartialUpdate.mockResolvedValue(null);

    renderForm();
    pressSave();

    await waitFor(() => expect(savedToast()).toHaveLength(1));
    expect(savedToast()[0][1]).toEqual({ variant: "success" });
    expect(mockPartialUpdate).toHaveBeenCalledTimes(1);
  });

  it("does not say saved when the draft could not be created", async () => {
    seedGraph({ isDraft: false });
    mockEnsureDraft.mockResolvedValue(false);

    renderForm();
    pressSave();

    await waitFor(() => expect(mockEnsureDraft).toHaveBeenCalled());
    // Let the submit handler finish before checking what it said.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(savedToast()).toHaveLength(0);
  });

  it("says the save failed, not saved, when the node update is refused", async () => {
    seedGraph({ isDraft: true });
    mockEnsureDraft.mockResolvedValue(true);
    mockPartialUpdate.mockRejectedValueOnce(new Error("patch failed"));

    renderForm();
    pressSave();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Failed to save prompt", {
        variant: "error",
      }),
    );
    expect(savedToast()).toHaveLength(0);
  });
});
