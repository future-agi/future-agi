import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import BuilderActions from "../BuilderActions";
import {
  useAgentPlaygroundStore,
  useWorkflowRunStore,
  useTemplateLoadingStore,
} from "../../store";
import { WORKFLOW_STATE } from "../../utils/workflowExecution";

// Mock useWorkflowExecution
const mockRunWorkflow = vi.fn();
const mockStopWorkflow = vi.fn();
vi.mock("../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

vi.mock("../../hooks/useWorkflowExecution", () => ({
  default: () => ({
    runWorkflow: mockRunWorkflow,
    stopWorkflow: mockStopWorkflow,
    isRunning:
      useWorkflowRunStore.getState().workflowState === WORKFLOW_STATE.RUNNING,
    workflowState: useWorkflowRunStore.getState().workflowState,
  }),
}));

// Mock SvgColor
vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-icon" {...props} />,
}));

// Mock Iconify
vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify-icon" {...props} />,
}));

// Mock CustomTooltip
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children, show, title }) => (
    <div data-testid="tooltip" data-show={show} data-title={title}>
      {children}
    </div>
  ),
}));

// Mock StopTemplateLoadingDialog
vi.mock("../../components/StopTemplateLoadingDialog", () => ({
  default: ({ open }) => (open ? <div data-testid="stop-dialog" /> : null),
}));

// Mock notistack
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  MaterialDesignContent: "div",
}));

// Mock src/components/snackbar
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mock logger
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

// Mock workflowValidation
vi.mock("../../utils/workflowValidation", () => ({
  validateGraphForSave: vi.fn(() => ({ valid: true, invalidNodeIds: [] })),
}));

describe("BuilderActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    useTemplateLoadingStore.getState().reset();
  });

  describe("Run button", () => {
    it("renders when hasNodes and not loading and not running", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: false, version_id: "v1" },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.getByText("Run Agent Workflow")).toBeInTheDocument();
    });

    it("opens save dialog when isDraft and clicked", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: true },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      const button = screen.getByText("Run Agent Workflow");
      expect(button.closest("button")).not.toBeDisabled();
      fireEvent.click(button);
      expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBe(true);
      expect(useAgentPlaygroundStore.getState().pendingRunAfterSave).toBe(true);
    });

    it("does not call runWorkflow when isDraft", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: true },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      fireEvent.click(screen.getByText("Run Agent Workflow"));
      expect(mockRunWorkflow).not.toHaveBeenCalled();
    });

    it("calls runWorkflow on click", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: false, version_id: "v1" },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      fireEvent.click(screen.getByText("Run Agent Workflow"));
      expect(mockRunWorkflow).toHaveBeenCalled();
    });

    it("hidden when hasNodes=false", () => {
      render(<BuilderActions width="300px" hasNodes={false} />);
      expect(screen.queryByText("Run Agent Workflow")).not.toBeInTheDocument();
    });

    it("hidden when isLoadingTemplate=true", () => {
      useTemplateLoadingStore.setState({ isLoadingTemplate: true });
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.queryByText("Run Agent Workflow")).not.toBeInTheDocument();
    });

    it("hidden when isRunning=true", () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.queryByText("Run Agent Workflow")).not.toBeInTheDocument();
    });
  });

  describe("Exit Workflow button", () => {
    it("renders when running", () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
      render(<BuilderActions width="300px" />);
      expect(screen.getByText("Exit Workflow")).toBeInTheDocument();
    });

    it("calls stopWorkflow on click", () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
      render(<BuilderActions width="300px" />);
      fireEvent.click(screen.getByText("Exit Workflow"));
      expect(mockStopWorkflow).toHaveBeenCalled();
    });
  });

  describe("Stop (template) button", () => {
    it("renders when loading template", () => {
      useTemplateLoadingStore.setState({ isLoadingTemplate: true });
      render(<BuilderActions width="300px" />);
      expect(screen.getByText("Stop")).toBeInTheDocument();
    });

    it("opens confirm dialog on click", () => {
      useTemplateLoadingStore.setState({ isLoadingTemplate: true });
      render(<BuilderActions width="300px" />);
      fireEvent.click(screen.getByText("Stop"));
      expect(useTemplateLoadingStore.getState().showStopConfirmDialog).toBe(
        true,
      );
    });
  });

  describe("Show/Hide Outcome button", () => {
    it("renders when hasRun", () => {
      useWorkflowRunStore.setState({ hasRun: true, showOutput: false });
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.getByText("Show Outcome")).toBeInTheDocument();
    });

    it("toggles text on click", () => {
      useWorkflowRunStore.setState({ hasRun: true, showOutput: false });
      render(<BuilderActions width="300px" hasNodes={true} />);
      fireEvent.click(screen.getByText("Show Outcome"));
      // After click, showOutput is toggled in store
      expect(useWorkflowRunStore.getState().showOutput).toBe(true);
    });
  });
});
