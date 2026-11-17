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

// Read lazily so a test can give the form its own validator.
let mockValidateVariable = vi.fn();
vi.mock("../../../../hooks/useConnectedNodeVariables", () => ({
  default: () => ({
    dropdownOptions: [],
    validateVariable: mockValidateVariable,
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

const DEFAULT_MESSAGES = [
  {
    id: "source-message",
    role: "user",
    content: [{ type: "text", text: "Hello" }],
  },
];

function renderForm({
  messages = DEFAULT_MESSAGES,
  templateFormat = "mustache",
} = {}) {
  return render(
    <FormWrapper
      defaultValues={{
        name: "Renamed source",
        version: "prompt-version",
        prompt_version_id: "prompt-version-id",
        prompt_template_id: "prompt-template-id",
        templateFormat,
        modelConfig: { model: "gpt-4o-mini", responseFormat: "text" },
        messages,
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
    mockValidateVariable = vi.fn();
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

// FA-PLAYGROUND-VARIABLE-RED: a plain variable with no value source is not
// an error. The editor leaves it plain (validator returns null) and the form
// says, in words, what it is and what to do.
describe("PromptNodeForm variables with no value source", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockValidateVariable = vi.fn();
    seedGraph();
  });

  it("names the variable, says it is an agent input and what to do", () => {
    mockValidateVariable = vi.fn((name) => (name === "question" ? null : true));

    renderForm({
      messages: [
        {
          id: "m-user",
          role: "user",
          content: [
            { type: "text", text: "Answer the question: {{question}}" },
          ],
        },
      ],
    });

    expect(screen.getByRole("status")).toHaveTextContent(
      "{{question}} is an input to this agent. You'll be asked for its value when you run. To use another node's output instead, connect that node to this one.",
    );
  });

  it("lists each one once, in prompt order, and leaves out the others", () => {
    mockValidateVariable = vi.fn((name) => {
      if (name === "question" || name === "context") return null;
      if (name === "Source.response.answer") return false;
      return true;
    });

    renderForm({
      messages: [
        {
          id: "m-system",
          role: "system",
          content: [
            {
              type: "text",
              text: "Use {{context}}, {{Source.response}} and {{Source.response.answer}}.",
            },
          ],
        },
        {
          id: "m-user",
          role: "user",
          content: [{ type: "text", text: "{{question}} {{ context }}" }],
        },
      ],
    });

    const note = screen.getByRole("status");
    expect(note).toHaveTextContent(
      "{{context}} and {{question}} are inputs to this agent. You'll be asked for their values when you run. To use another node's output instead, connect that node to this one.",
    );
    expect(note).not.toHaveTextContent("Source.response");
  });

  it("follows Jinja scoping: a loop variable is not an input", () => {
    mockValidateVariable = vi.fn(() => null);

    renderForm({
      templateFormat: "jinja",
      messages: [
        {
          id: "m-user",
          role: "user",
          content: [
            {
              type: "text",
              text: "{% for item in items %}{{ item }}{% endfor %}",
            },
          ],
        },
      ],
    });

    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("{{items}} is an input to this agent.");
    expect(note).not.toHaveTextContent("{{item}}");
  });

  it("control: says nothing when every variable has a source", () => {
    mockValidateVariable = vi.fn((name) =>
      name === "Source.response.answer" ? false : true,
    );

    renderForm({
      messages: [
        {
          id: "m-user",
          role: "user",
          content: [
            {
              type: "text",
              text: "Use {{Source.response}} and {{Source.response.answer}}",
            },
          ],
        },
      ],
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
