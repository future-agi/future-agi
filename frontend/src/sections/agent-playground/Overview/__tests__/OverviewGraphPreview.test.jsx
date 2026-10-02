/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import OverviewGraphPreview from "../OverviewGraphPreview";
import { useAgentPlaygroundStore } from "../../store";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
vi.mock("react-router", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ agentId: "agent-123" }),
}));

let mockVersionData = null;
let mockIsLoading = false;
let mockIsError = false;
const mockActivateVersion = vi.fn();
let mockIsActivating = false;

vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetVersionDetail: () => ({
    data: mockVersionData,
    isLoading: mockIsLoading,
    isError: mockIsError,
  }),
  useActivateVersion: () => ({
    mutate: mockActivateVersion,
    isPending: mockIsActivating,
  }),
}));

vi.mock("../../utils/versionPayloadUtils", () => ({
  parseVersionResponse: vi.fn((data) => {
    if (!data) return { nodes: [], edges: [] };
    return {
      nodes: [{ id: "n1", type: "llm_prompt", data: { label: "Node 1" } }],
      edges: [],
    };
  }),
}));

const mockEnqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("@xyflow/react", () => ({
  ReactFlowProvider: ({ children }) => <div>{children}</div>,
}));

vi.mock("src/sections/agent-playground/components/PreviewGraphInner", () => ({
  PreviewContainer: ({ children, centered, isError: isErr }) => (
    <div
      data-testid="preview-container"
      data-centered={centered}
      data-error={isErr}
    >
      {children}
    </div>
  ),
  PreviewGraphInner: ({ nodes }) => (
    <div data-testid="preview-graph">{nodes?.length ?? 0} nodes</div>
  ),
}));

vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const theme = createTheme();
function renderPreview(props = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <OverviewGraphPreview
        selectedVersion={{ version_id: "v-1", version_name: "Version 1" }}
        currentVersion="v-current"
        {...props}
      />
    </ThemeProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("OverviewGraphPreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    mockVersionData = { status: "active", nodes: [{ id: "n1" }] };
    mockIsLoading = false;
    mockIsError = false;
    mockIsActivating = false;

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "agent-123", name: "My Agent" },
    });
  });

  it("shows loading spinner when loading", () => {
    mockIsLoading = true;
    renderPreview();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows error message when fetch fails", () => {
    mockIsError = true;
    renderPreview();
    expect(
      screen.getByText("Failed to load graph preview"),
    ).toBeInTheDocument();
  });

  it("shows select prompt when no version selected", () => {
    renderPreview({
      selectedVersion: { versionId: null, versionName: null },
    });
    expect(screen.getByText("Select a version to preview")).toBeInTheDocument();
  });

  it("renders graph preview when data available", () => {
    renderPreview();
    expect(screen.getByTestId("preview-graph")).toBeInTheDocument();
  });

  it("shows Restore button when version mismatches current", () => {
    renderPreview();
    expect(
      screen.getByRole("button", { name: /restore/i }),
    ).toBeInTheDocument();
  });

  it("hides Restore button when version matches current", () => {
    renderPreview({ currentVersion: "v-1" });
    expect(
      screen.queryByRole("button", { name: /restore/i }),
    ).not.toBeInTheDocument();
  });

  it("handleRestoreVersion activates inactive version", () => {
    mockVersionData = { status: "inactive", nodes: [{ id: "n1" }] };
    renderPreview();

    fireEvent.click(screen.getByRole("button", { name: /restore/i }));

    expect(mockActivateVersion).toHaveBeenCalledWith(
      { graphId: "agent-123", versionId: "v-1" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("handleRestoreVersion navigates directly for non-inactive version", () => {
    mockVersionData = { status: "active", nodes: [{ id: "n1" }] };
    renderPreview();

    fireEvent.click(screen.getByRole("button", { name: /restore/i }));

    expect(mockActivateVersion).not.toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/agents/playground/agent-123/build?version=v-1",
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "My Agent agent Version 1 restored",
      { variant: "success" },
    );
  });

  it("shows 'No nodes' message when graph has no nodes", async () => {
    // Set data to null so parseVersionResponse returns empty
    mockVersionData = null;

    renderPreview();
    expect(screen.getByText("No nodes in this version")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("OverviewGraphPreview — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    mockVersionData = { status: "inactive", nodes: [{ id: "n1" }] };
    mockIsLoading = false;
    mockIsError = false;
    mockIsActivating = false;
  });

  it("does not crash when currentAgent is null and restore is clicked", () => {
    useAgentPlaygroundStore.setState({ currentAgent: null });

    renderPreview();

    // Restore button should be present (mismatch still applies)
    const restoreBtn = screen.getByRole("button", { name: /restore/i });

    expect(() => {
      fireEvent.click(restoreBtn);
    }).not.toThrow();

    // activateVersion should be called with graphId as undefined (from null agent)
    expect(mockActivateVersion).toHaveBeenCalledWith(
      { graphId: undefined, versionId: "v-1" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("activateVersion onSuccess callback navigates and shows snackbar", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "agent-123", name: "My Agent" },
    });

    mockVersionData = { status: "inactive", nodes: [{ id: "n1" }] };
    renderPreview();

    fireEvent.click(screen.getByRole("button", { name: /restore/i }));

    // Extract the onSuccess callback and invoke it
    const callArgs = mockActivateVersion.mock.calls[0];
    const onSuccess = callArgs[1].onSuccess;
    act(() => {
      onSuccess();
    });

    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/agents/playground/agent-123/build?version=v-1",
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "My Agent agent Version 1 restored",
      { variant: "success" },
    );
  });
});
