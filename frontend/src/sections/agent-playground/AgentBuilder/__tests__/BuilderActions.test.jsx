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
      expect(screen.getByText("Run workflow")).toBeInTheDocument();
    });

    it("opens save dialog when isDraft and clicked", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: true },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      const button = screen.getByText("Save and run");
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
      fireEvent.click(screen.getByText("Save and run"));
      expect(mockRunWorkflow).not.toHaveBeenCalled();
    });

    it("calls runWorkflow on click", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: false, version_id: "v1" },
      });
      render(<BuilderActions width="300px" hasNodes={true} />);
      fireEvent.click(screen.getByText("Run workflow"));
      expect(mockRunWorkflow).toHaveBeenCalled();
    });

    it("hidden when hasNodes=false", () => {
      render(<BuilderActions width="300px" hasNodes={false} />);
      expect(screen.queryByText("Run workflow")).not.toBeInTheDocument();
    });

    it("hidden when isLoadingTemplate=true", () => {
      useTemplateLoadingStore.setState({ isLoadingTemplate: true });
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.queryByText("Run workflow")).not.toBeInTheDocument();
    });

    it("hidden when isRunning=true", () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
      render(<BuilderActions width="300px" hasNodes={true} />);
      expect(screen.queryByText("Run workflow")).not.toBeInTheDocument();
    });
  });

  describe("Onboarding focus", () => {
    it("renders run-scenario guidance and reuses the run action", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: false, version_id: "v1" },
      });

      render(
        <BuilderActions
          width="300px"
          hasNodes={true}
          onboardingMode="run-scenario"
        />,
      );

      expect(screen.getByTestId("agent-onboarding-focus")).toBeInTheDocument();
      expect(screen.getByText("Run one test scenario")).toBeVisible();

      fireEvent.click(screen.getByRole("button", { name: /^run workflow$/i }));

      expect(mockRunWorkflow).toHaveBeenCalled();
    });

    it("uses explicit save-and-run copy for draft agent setup", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: true, version_id: "v1" },
      });

      render(
        <BuilderActions
          width="300px"
          hasNodes={true}
          onboardingMode="run-scenario"
        />,
      );

      expect(screen.getByText("Save this version first")).toBeVisible();

      fireEvent.click(
        screen.getByRole("button", { name: /save agent and run scenario/i }),
      );

      expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBe(true);
      expect(useAgentPlaygroundStore.getState().pendingRunAfterSave).toBe(true);
    });

    it("leaves missing-node guidance to the node selection panel", () => {
      render(
        <BuilderActions
          width="300px"
          hasNodes={false}
          onboardingMode="run-scenario"
        />,
      );

      expect(screen.queryByTestId("agent-onboarding-focus")).toBeNull();
      expect(screen.queryByText("Add one node first")).not.toBeInTheDocument();
    });

    it("renders eval coverage run guidance after the eval node is added", () => {
      useAgentPlaygroundStore.setState({
        currentAgent: { is_draft: true, version_id: "v1" },
      });

      render(
        <BuilderActions
          width="300px"
          hasNodes={true}
          onboardingMode="add-eval"
        />,
      );

      expect(screen.getByTestId("agent-onboarding-focus")).toBeInTheDocument();
      expect(screen.getByText("Run the agent eval coverage")).toBeVisible();
      expect(screen.getByText("Save this eval coverage first")).toBeVisible();

      fireEvent.click(
        screen.getByRole("button", {
          name: /save and run eval coverage/i,
        }),
      );

      expect(useAgentPlaygroundStore.getState().openSaveAgentDialog).toBe(true);
      expect(useAgentPlaygroundStore.getState().pendingRunAfterSave).toBe(true);
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
