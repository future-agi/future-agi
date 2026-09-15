/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SaveAgentDialog from "../SaveAgentDialog";
import { useAgentPlaygroundStore } from "../../store";
import { NODE_TYPES } from "../../utils/constants";

const mockSaveAgent = vi.fn();
let mockVersionsData = null;
let mockBaselineDetail = null;
let mockVersionsLoading = false;
let mockBaselineLoading = false;

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useSaveDraftVersion: (options = {}) => ({
    mutate: (vars) => {
      mockSaveAgent(vars);
      options.onSuccess?.({
        data: {
          result: {
            id: "v-saved",
            version_number: 2,
            nodes: [],
            node_connections: [],
          },
        },
      });
    },
    isPending: false,
  }),
  useGetGraphVersions: () => ({
    data: mockVersionsData,
    isLoading: mockVersionsLoading,
  }),
  useGetVersionDetail: () => ({
    data: mockBaselineDetail,
    isLoading: mockBaselineLoading,
  }),
}));

vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

const mockRunWorkflow = vi.fn();
vi.mock("../../hooks/useWorkflowExecution", () => ({
  default: () => ({ runWorkflow: mockRunWorkflow }),
}));

vi.mock("../../utils/workflowValidation", () => ({
  validateGraphForSave: vi.fn(() => ({
    valid: true,
    invalidNodeIds: [],
    hasCycle: false,
    errors: [],
  })),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-icon" />,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => (
    <span data-testid="iconify-icon" data-icon={props.icon} />
  ),
}));

vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ label, fieldName, disabled }) => (
    <label>
      {label}
      <input aria-label={label} name={fieldName} disabled={disabled} />
    </label>
  ),
}));

const theme = createTheme();

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <SaveAgentDialog />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function seedAgent({ isDraft = true } = {}) {
  useAgentPlaygroundStore.setState({
    openSaveAgentDialog: true,
    currentAgent: {
      id: "graph-1",
      name: "Untitled_1",
      version_id: "draft-1",
      version_name: "Version 2",
      is_draft: isDraft,
    },
    nodes: [
      {
        id: "n-prompt",
        type: NODE_TYPES.LLM_PROMPT,
        position: { x: 0, y: 0 },
        data: {
          label: "Prompt node",
          node_template_id: "tpl-prompt",
          config: {
            modelConfig: {
              model: "gpt-4",
              modelDetail: {
                modelName: "GPT-4",
                logoUrl: "",
                providers: "openai",
                isAvailable: true,
              },
              responseFormat: "text",
              toolChoice: "auto",
              tools: [],
            },
            messages: [
              {
                id: "msg-0",
                role: "system",
                content: [{ type: "text", text: "new instructions" }],
              },
            ],
          },
        },
      },
      {
        id: "n-research",
        type: NODE_TYPES.AGENT,
        position: { x: 400, y: 0 },
        data: {
          label: "Research Agent",
          ref_graph_version_id: "ref-1",
          config: { payload: { inputMappings: [] } },
          ports: [],
        },
      },
    ],
    edges: [{ id: "e1", source: "n-prompt", target: "n-research" }],
  });
}

describe("SaveAgentDialog changelog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    mockSaveAgent.mockClear();
    mockVersionsLoading = false;
    mockBaselineLoading = false;
    mockVersionsData = {
      pages: [
        {
          data: {
            result: {
              versions: [
                {
                  id: "draft-1",
                  status: "draft",
                  created_at: "2026-09-04T00:00:00Z",
                },
                {
                  id: "active-1",
                  status: "active",
                  created_at: "2026-09-03T00:00:00Z",
                },
              ],
            },
          },
        },
      ],
    };
    mockBaselineDetail = {
      id: "active-1",
      nodes: [
        {
          id: "old-prompt",
          name: "Prompt node",
          type: "atomic",
          prompt_template: {
            model: "gpt-4",
            messages: [{ role: "system", content: "old instructions" }],
          },
        },
        {
          id: "old-research",
          name: "Research Agent",
          type: "subgraph",
          ref_graph_version_id: "ref-1",
        },
        {
          id: "old-output",
          name: "Output node",
          type: "atomic",
          prompt_template: { model: "gpt-4", messages: [] },
        },
      ],
      node_connections: [
        { source_node_id: "old-prompt", target_node_id: "old-research" },
      ],
    };
    seedAgent();
  });

  it("shows changelog and code tabs under the commit field", () => {
    renderDialog();
    expect(screen.getByTestId("save-agent-diff-tabs")).toBeInTheDocument();
    expect(screen.getByTestId("save-agent-tab-changelog")).toBeInTheDocument();
    expect(screen.getByTestId("save-agent-tab-code")).toBeInTheDocument();
    expect(screen.getByLabelText("Commit message")).toBeInTheDocument();
  });

  it("lists created, updated, deleted, and unchanged nodes", () => {
    renderDialog();
    expect(
      screen.getByTestId("save-changelog-badge-Prompt node"),
    ).toHaveTextContent("Updated");
    expect(
      screen.getByTestId("save-changelog-badge-Research Agent"),
    ).toHaveTextContent("No changes");
    expect(
      screen.getByTestId("save-changelog-badge-Output node"),
    ).toHaveTextContent("Deleted");
  });

  it("keeps unchanged nodes visible", () => {
    renderDialog();
    expect(
      screen.getByTestId("save-changelog-row-Research Agent"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("save-changelog-badge-Research Agent"),
    ).toHaveTextContent("No changes");
  });

  it("switches to the code tab with copy, download, and line totals", () => {
    renderDialog();
    fireEvent.click(screen.getByTestId("save-agent-tab-code"));
    expect(screen.getByTestId("save-code-tab")).toBeInTheDocument();
    expect(screen.getByTestId("save-code-filename")).toHaveTextContent(
      "untitled-1-agent.json",
    );
    expect(screen.getByTestId("save-code-copy")).toBeInTheDocument();
    expect(screen.getByTestId("save-code-download")).toBeInTheDocument();
    expect(screen.getByTestId("save-code-added-total").textContent).toMatch(
      /^\+\d+ lines$/,
    );
    expect(screen.getByTestId("save-code-removed-total").textContent).toMatch(
      /^-\d+ lines$/,
    );
    expect(screen.getByTestId("save-code-original")).toBeInTheDocument();
    expect(screen.getByTestId("save-code-modified")).toBeInTheDocument();
  });

  it("does not break when there is no previous version", () => {
    mockVersionsData = {
      pages: [
        {
          data: {
            result: {
              versions: [{ id: "draft-1", status: "draft" }],
            },
          },
        },
      ],
    };
    mockBaselineDetail = null;
    renderDialog();
    expect(screen.getByTestId("save-changelog-list")).toBeInTheDocument();
    expect(
      screen.getByTestId("save-changelog-badge-Prompt node"),
    ).toHaveTextContent("Created");
    fireEvent.click(screen.getByTestId("save-agent-tab-code"));
    expect(screen.getByTestId("save-code-empty")).toBeInTheDocument();
  });

  it("saves a draft with the same payload shape as before", async () => {
    renderDialog();
    fireEvent.submit(document.querySelector("form"));
    await waitFor(() => expect(mockSaveAgent).toHaveBeenCalled());
    expect(mockSaveAgent.mock.calls[0][0]).toEqual({
      graphId: "graph-1",
      versionId: "draft-1",
      payload: { status: "active" },
    });
  });

  it("copies the current definition JSON", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.assign(navigator, { clipboard: { writeText } });
    renderDialog();
    fireEvent.click(screen.getByTestId("save-agent-tab-code"));
    fireEvent.click(screen.getByTestId("save-code-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("Prompt node");
  });

  it("downloads the current definition JSON", () => {
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    globalThis.URL.createObjectURL = createObjectURL;
    globalThis.URL.revokeObjectURL = revokeObjectURL;
    const click = vi.fn();
    const originalCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag) => {
      const el = originalCreate(tag);
      if (tag === "a") {
        el.click = click;
      }
      return el;
    });

    renderDialog();
    fireEvent.click(screen.getByTestId("save-agent-tab-code"));
    fireEvent.click(screen.getByTestId("save-code-download"));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
  });
});
