/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
    validateVariable: vi.fn(),
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
        name: "Renamed source",
        version: "prompt-version",
        prompt_version_id: "prompt-version-id",
        prompt_template_id: "prompt-template-id",
        templateFormat: "mustache",
        modelConfig: { model: "gpt-4o-mini", responseFormat: "text" },
        messages: [
          {
            id: "source-message",
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

function seedGraph(overrides = {}) {
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

  const targetNode = {
    id: "target-node",
    data: {
      label: "Target",
      ports: [],
      config: {
        messages: [
          {
            id: "target-message",
            role: "user",
            content: [
              {
                type: "text",
                text: "Use {{Source.response}}",
              },
            ],
          },
        ],
      },
    },
  };

  useAgentPlaygroundStore.getState().reset();
  useAgentPlaygroundStore.setState({
    currentAgent: {
      id: "graph-id",
      version_id: "version-id",
      is_draft: false,
      ...overrides.currentAgent,
    },
    nodes: [sourceNode, targetNode],
    edges: [{ id: "edge-id", source: "source-node", target: "target-node" }],
    selectedNode: sourceNode,
    ...overrides.store,
  });
}

function getNode(id) {
  return useAgentPlaygroundStore
    .getState()
    .nodes.find((node) => node.id === id);
}

function getTargetMessageText() {
  return getNode("target-node").data.config.messages[0].content[0].text;
}

describe("PromptNodeForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedGraph();
  });

  it("propagates downstream variable references before creating an active-version draft", async () => {
    let textAtDraftCreation;
    mockEnsureDraft.mockImplementation(async () => {
      textAtDraftCreation = getTargetMessageText();
      return "created";
    });

    renderForm();
    fireEvent.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(mockEnsureDraft).toHaveBeenCalled());

    expect(textAtDraftCreation).toBe("Use {{Renamed source.response}}");
    expect(getTargetMessageText()).toBe("Use {{Renamed source.response}}");
    expect(mockPartialUpdate).not.toHaveBeenCalled();
  });

  it("propagates before joining a pending active-version draft creation", async () => {
    seedGraph({
      currentAgent: { is_draft: true },
      store: { _isDraftCreating: true },
    });
    let textAtDraftCreation;
    mockEnsureDraft.mockImplementation(async () => {
      textAtDraftCreation = getTargetMessageText();
      return "created";
    });

    renderForm();
    fireEvent.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(mockEnsureDraft).toHaveBeenCalled());

    expect(textAtDraftCreation).toBe("Use {{Renamed source.response}}");
    expect(getTargetMessageText()).toBe("Use {{Renamed source.response}}");
  });

  it("restores downstream variable references when active-version draft creation fails", async () => {
    mockEnsureDraft.mockResolvedValue(false);

    renderForm();
    fireEvent.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(mockEnsureDraft).toHaveBeenCalled());

    expect(getNode("source-node").data.label).toBe("Source");
    expect(getTargetMessageText()).toBe("Use {{Source.response}}");
  });

  it("rolls back the edited node when an existing draft PATCH fails", async () => {
    seedGraph({ currentAgent: { is_draft: true } });
    mockEnsureDraft.mockResolvedValue(true);
    mockPartialUpdate.mockRejectedValueOnce(new Error("patch failed"));

    renderForm();
    fireEvent.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(mockPartialUpdate).toHaveBeenCalled());

    expect(getNode("source-node").data.label).toBe("Source");
    expect(getTargetMessageText()).toBe("Use {{Source.response}}");
  });
});
