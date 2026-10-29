import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import StepConcurrencyControl from "../StepConcurrencyControl";
import {
  useAgentPlaygroundStore,
  useWorkflowRunStore,
} from "../../store";
import { MAX_CONCURRENT_NODES_ERROR } from "../../utils/constants";

const mockUpdateGraph = vi.fn();
vi.mock("../../../../api/agent-playground/agent-playground", () => ({
  useUpdateGraph: () => ({ mutate: mockUpdateGraph }),
}));

vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));

const mockEnqueueSnackbar = vi.fn();
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

const theme = createTheme();
function renderControl() {
  return render(
    <ThemeProvider theme={theme}>
      <StepConcurrencyControl />
    </ThemeProvider>,
  );
}

describe("StepConcurrencyControl", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", name: "Agent", max_concurrent_nodes: 10 },
    });
  });

  it("renders the stored concurrency value", () => {
    renderControl();
    expect(screen.getByLabelText("Concurrent steps")).toHaveValue(10);
  });

  it("persists a valid value on blur", () => {
    renderControl();
    const input = screen.getByLabelText("Concurrent steps");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);

    expect(mockUpdateGraph).toHaveBeenCalledWith(
      { id: "g1", max_concurrent_nodes: 3 },
      expect.any(Object),
    );
    expect(useAgentPlaygroundStore.getState().currentAgent.max_concurrent_nodes).toBe(
      3,
    );
  });

  it("refuses zero and restores the previous value", () => {
    renderControl();
    const input = screen.getByLabelText("Concurrent steps");
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.blur(input);

    expect(mockUpdateGraph).not.toHaveBeenCalled();
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(MAX_CONCURRENT_NODES_ERROR, {
      variant: "error",
    });
    expect(input).toHaveValue(10);
  });

  it("refuses a negative value", () => {
    renderControl();
    const input = screen.getByLabelText("Concurrent steps");
    fireEvent.change(input, { target: { value: "-2" } });
    fireEvent.blur(input);

    expect(mockUpdateGraph).not.toHaveBeenCalled();
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(MAX_CONCURRENT_NODES_ERROR, {
      variant: "error",
    });
  });

  it("does not persist when the value is unchanged", () => {
    renderControl();
    const input = screen.getByLabelText("Concurrent steps");
    fireEvent.blur(input);
    expect(mockUpdateGraph).not.toHaveBeenCalled();
  });
});
