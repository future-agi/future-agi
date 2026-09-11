/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import Header from "../Header";
import { useAgentPlaygroundStore, useWorkflowRunStore } from "../../store";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
const mockLocation = { pathname: "/agents/123/build" };
vi.mock("react-router", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => mockLocation,
}));

let mockIsDraft = true;
vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

vi.mock("../../hooks/useDraftConfirmation", () => ({
  default: () => ({ isDraft: mockIsDraft }),
}));

const mockValidateResult = {
  valid: true,
  invalidNodeIds: [],
  hasCycle: false,
  errors: [],
};
vi.mock("../../utils/workflowValidation", () => ({
  validateGraphForSave: vi.fn(() => mockValidateResult),
}));

const mockEnqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-icon" {...props} />,
}));

vi.mock("../../../develop-detail/Common/BackButton", () => ({
  default: ({ onBack }) => (
    <button data-testid="back-button" onClick={onBack}>
      Back
    </button>
  ),
}));

vi.mock("../AgentName", () => ({
  default: ({ currentAgent }) => (
    <span data-testid="agent-name">{currentAgent?.name}</span>
  ),
}));

vi.mock("src/sections/workbench/createPrompt/SharedStyledComponents", () => ({
  DraftBadge: ({ children }) => (
    <span data-testid="draft-badge">{children}</span>
  ),
}));

vi.mock("../../../../components/show/ShowComponent", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const theme = createTheme();
function renderHeader() {
  return render(
    <ThemeProvider theme={theme}>
      <Header />
    </ThemeProvider>,
  );
}

// Get mock for validate
import { validateGraphForSave } from "../../utils/workflowValidation";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("Header", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    mockIsDraft = true;
    mockLocation.pathname = "/agents/123/build";
    validateGraphForSave.mockReturnValue({
      valid: true,
      invalidNodeIds: [],
      hasCycle: false,
      errors: [],
    });
  });

  it("renders agent name when available", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: {
        id: "g1",
        name: "My Agent",
        versionName: "v1",
        is_draft: true,
      },
      nodes: [],
      edges: [],
    });

    renderHeader();

    expect(screen.getByTestId("agent-name")).toHaveTextContent("My Agent");
  });

  it("renders skeleton when agent name is not available", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: null },
      nodes: [],
      edges: [],
    });

    renderHeader();

    expect(screen.queryByTestId("agent-name")).not.toBeInTheDocument();
  });

  it("disables Save Agent button when not a draft", () => {
    mockIsDraft = false;
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: false },
      nodes: [],
      edges: [],
    });

    renderHeader();

    const saveButton = screen.getByRole("button", { name: /save agent/i });
    expect(saveButton).toBeDisabled();
  });

  it("enables Save Agent button when draft", () => {
    mockIsDraft = true;
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();

    const saveButton = screen.getByRole("button", { name: /save agent/i });
    expect(saveButton).not.toBeDisabled();
  });

  // ---- handleSaveClick ----
  it("opens save dialog when validation passes", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [{ id: "n1" }],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBe(true);
    expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
  });

  it("shows error snackbar for cycle detection", () => {
    validateGraphForSave.mockReturnValue({
      valid: false,
      hasCycle: true,
      invalidNodeIds: [],
      errors: [{ message: "Graph contains a cycle." }],
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [{ id: "n1" }],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Graph contains a cycle.",
      { variant: "error" },
    );
    expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBeFalsy();
  });

  it("shows single node error message", () => {
    validateGraphForSave.mockReturnValue({
      valid: false,
      hasCycle: false,
      invalidNodeIds: ["n1"],
      errors: [],
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [{ id: "n1" }],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith("Node not configured", {
      variant: "error",
    });
  });

  it("shows multiple node error message", () => {
    validateGraphForSave.mockReturnValue({
      valid: false,
      hasCycle: false,
      invalidNodeIds: ["n1", "n2", "n3"],
      errors: [],
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "3 nodes are not configured",
      { variant: "error" },
    );
  });

  it("sets validation error node IDs on failure", () => {
    validateGraphForSave.mockReturnValue({
      valid: false,
      hasCycle: false,
      invalidNodeIds: ["n1", "n2"],
      errors: [],
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual([
      "n1",
      "n2",
    ]);
  });

  it("clears validation errors before re-validating", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      validationErrorNodeIds: ["old-n1"],
      nodes: [],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    // clearValidationErrors was called (validation errors should be cleared before new check)
    // Since validation passes, no new errors are set
    expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual(
      [],
    );
  });

  it("shows draft badge when agent is draft", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();

    expect(screen.getByTestId("draft-badge")).toBeInTheDocument();
  });

  it("navigates back on back button click", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent" },
      nodes: [],
      edges: [],
    });

    renderHeader();
    fireEvent.click(screen.getByTestId("back-button"));

    expect(mockNavigate).toHaveBeenCalledWith(-1);
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("Header — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    mockIsDraft = true;
    mockLocation.pathname = "/agents/123/build";
    validateGraphForSave.mockReturnValue({
      valid: true,
      invalidNodeIds: [],
      hasCycle: false,
      errors: [],
    });
  });

  it("does not crash the render tree when validateGraphForSave throws an exception", () => {
    validateGraphForSave.mockImplementation(() => {
      throw new Error("boom");
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [{ id: "n1" }],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();

    // Suppress the unhandled error that React/jsdom propagates to window
    const errorHandler = (e) => e.preventDefault();
    window.addEventListener("error", errorHandler);

    try {
      // The click handler will throw because there is no try-catch in handleSaveClick.
      // React catches it and reports it, but the component tree survives.
      fireEvent.click(screen.getByRole("button", { name: /save agent/i }));
    } catch {
      // swallow — we are testing that the rendered tree is still intact
    }

    window.removeEventListener("error", errorHandler);

    // The component should still be mounted and the save dialog should NOT open
    expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBeFalsy();
    expect(
      screen.getByRole("button", { name: /save agent/i }),
    ).toBeInTheDocument();
  });

  it("shows cycle message (not node-count message) when hasCycle=true AND invalidNodeIds are populated", () => {
    validateGraphForSave.mockReturnValue({
      valid: false,
      hasCycle: true,
      invalidNodeIds: ["n1", "n2"],
      errors: [{ message: "Graph contains a cycle." }],
    });

    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", is_draft: true },
      nodes: [{ id: "n1" }, { id: "n2" }],
      edges: [],
      isGraphReady: true,
    });

    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    // Cycle message should take priority
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Graph contains a cycle.",
      { variant: "error" },
    );

    // The node-count message should NOT have been shown
    expect(mockEnqueueSnackbar).not.toHaveBeenCalledWith(
      expect.stringContaining("nodes are not configured"),
      expect.anything(),
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it("does not crash when currentAgent is null", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: null,
      nodes: [],
      edges: [],
    });

    expect(() => renderHeader()).not.toThrow();
  });
});
