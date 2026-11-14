/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import Actions from "../Actions";
import { useAgentPlaygroundStore, useAgentListGridStore } from "../../store";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
vi.mock("react-router", () => ({
  useNavigate: () => mockNavigate,
}));

const mockDeleteMutate = vi.fn();
let mockDeleteIsPending = false;
const mockCreateMutate = vi.fn();
let mockCreateIsPending = false;

vi.mock("../../../../api/agent-playground/agent-playground", () => ({
  useDeleteGraphs: (opts) => {
    // Store onSuccess for testing
    mockDeleteMutate._onSuccess = opts?.onSuccess;
    return { mutate: mockDeleteMutate, isPending: mockDeleteIsPending };
  },
  useCreateGraph: () => ({
    mutate: mockCreateMutate,
    isPending: mockCreateIsPending,
  }),
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-icon" {...props} />,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ searchQuery, onChange, ...props }) => (
    <input
      data-testid="search-field"
      value={searchQuery}
      onChange={onChange}
      {...props}
    />
  ),
}));

vi.mock("../../components/DeleteAgentsDialog", () => ({
  default: ({ open, onClose, onConfirm, agentCount }) =>
    open ? (
      <div data-testid="delete-dialog">
        <span data-testid="agent-count">{agentCount}</span>
        <button data-testid="confirm-delete" onClick={onConfirm}>
          Confirm
        </button>
        <button data-testid="cancel-delete" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const theme = createTheme();
const mockGridApi = {
  deselectAll: vi.fn(),
  refreshServerSide: vi.fn(),
};

function renderActions(overrides = {}) {
  const props = {
    searchQuery: "",
    setSearchQuery: vi.fn(),
    gridApi: mockGridApi,
    ...overrides,
  };
  return render(
    <ThemeProvider theme={theme}>
      <Actions {...props} />
    </ThemeProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("Actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useAgentListGridStore.getState().reset();
    mockDeleteIsPending = false;
    mockCreateIsPending = false;
  });

  // ---- Default state (no selection) ----
  it("renders Create Agent button when no selection", () => {
    renderActions();
    expect(
      screen.getByRole("button", { name: /create agent/i }),
    ).toBeInTheDocument();
  });

  it("renders search field", () => {
    renderActions();
    expect(screen.getByTestId("search-field")).toBeInTheDocument();
  });

  // ---- userMadeSelection logic ----
  it("shows selection actions when items are selected (selectAll=false)", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1", "g2"],
      selectAll: false,
      totalRowCount: 10,
    });

    renderActions();

    expect(screen.getByText(/2 selected/)).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("shows selection actions when selectAll=true with exclusions", () => {
    useAgentListGridStore.setState({
      selectAll: true,
      toggledNodes: ["g3"], // excluded
      totalRowCount: 10,
    });

    renderActions();

    // selectedCount = 10 - 1 = 9
    expect(screen.getByText(/9 selected/)).toBeInTheDocument();
  });

  it("does not show selection when selectAll=true but all items excluded", () => {
    useAgentListGridStore.setState({
      selectAll: true,
      toggledNodes: Array.from({ length: 10 }, (_, i) => `g${i}`),
      totalRowCount: 10,
    });

    renderActions();

    // All excluded — no selection
    expect(
      screen.getByRole("button", { name: /create agent/i }),
    ).toBeInTheDocument();
  });

  // ---- Delete flow ----
  it("opens delete dialog on delete click", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions();

    fireEvent.click(screen.getByText("Delete"));

    expect(screen.getByTestId("delete-dialog")).toBeInTheDocument();
  });

  it("handleDeleteConfirm sends include mode ids when selectAll=false", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1", "g2"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions();
    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(screen.getByTestId("confirm-delete"));

    expect(mockDeleteMutate).toHaveBeenCalledWith(
      { selectAll: false, ids: ["g1", "g2"] },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it("handleDeleteConfirm sends exclude mode when selectAll=true", () => {
    useAgentListGridStore.setState({
      selectAll: true,
      toggledNodes: ["g3"],
      totalRowCount: 10,
    });

    renderActions();
    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(screen.getByTestId("confirm-delete"));

    expect(mockDeleteMutate).toHaveBeenCalledWith(
      { selectAll: true, excludeIds: ["g3"] },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it("onSuccess resets selection and refreshes grid", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions();

    // Trigger onSuccess stored by the mock
    act(() => {
      mockDeleteMutate._onSuccess?.();
    });

    expect(useAgentListGridStore.getState().selectAll).toBe(false);
    expect(useAgentListGridStore.getState().toggledNodes).toEqual([]);
    expect(mockGridApi.deselectAll).toHaveBeenCalled();
    expect(mockGridApi.refreshServerSide).toHaveBeenCalled();
  });

  // ---- Cancel selection ----
  it("onCancel resets selection state and deselects all", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1", "g2"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions();
    fireEvent.click(screen.getByText("Cancel"));

    expect(useAgentListGridStore.getState().selectAll).toBe(false);
    expect(useAgentListGridStore.getState().toggledNodes).toEqual([]);
    expect(mockGridApi.deselectAll).toHaveBeenCalled();
  });

  // ---- Create agent ----
  it("calls createAgent on Create Agent button click", () => {
    renderActions();

    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));

    expect(mockCreateMutate).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("Actions — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useAgentListGridStore.getState().reset();
    mockDeleteIsPending = false;
    mockCreateIsPending = false;
  });

  it("does NOT show selection UI when selectAll=true, toggledNodes=[], totalRowCount=0", () => {
    useAgentListGridStore.setState({
      selectAll: true,
      toggledNodes: [],
      totalRowCount: 0,
    });

    renderActions();

    // selectedCount = 0 - 0 = 0, userMadeSelection should be false
    // because totalRowCount > 0 is false
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create agent/i }),
    ).toBeInTheDocument();
  });

  it("does not crash when gridApi is undefined and cancel is clicked", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions({ gridApi: undefined });

    // Should not throw when clicking Cancel with undefined gridApi
    expect(() => {
      fireEvent.click(screen.getByText("Cancel"));
    }).not.toThrow();
  });

  it("does not crash when gridApi is undefined and delete onSuccess fires", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["g1"],
      selectAll: false,
      totalRowCount: 5,
    });

    renderActions({ gridApi: undefined });

    // Trigger onSuccess stored by the mock — should not throw
    expect(() => {
      act(() => {
        mockDeleteMutate._onSuccess?.();
      });
    }).not.toThrow();
  });
});
