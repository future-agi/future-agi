import { Box, Button } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
  useTemplateLoadingStoreShallow,
} from "../store";
import AgentOnboardingFocusPanel from "../components/AgentOnboardingFocusPanel";
import StopTemplateLoadingDialog from "../components/StopTemplateLoadingDialog";
import useWorkflowExecution from "../hooks/useWorkflowExecution";
import { validateGraphForSave } from "../utils/workflowValidation";
import { enqueueSnackbar } from "src/components/snackbar";

// Main BuilderActions Component
export default function BuilderActions({
  width,
  hasNodes = true,
  onboardingMode,
  rightOffset = 0,
  tourAnchor,
}) {
  const { runWorkflow, stopWorkflow, isRunning, isInitiating } =
    useWorkflowExecution();

  const {
    isDraft,
    nodes,
    edges,
    setOpenSaveAgentDialog,
    setPendingRunAfterSave,
    setValidationErrorNodeIds,
    clearValidationErrors,
    clearAllExecutionStates,
    isNodeFormDirty,
  } = useAgentPlaygroundStoreShallow((s) => ({
    isDraft: s.currentAgent?.is_draft ?? true,
    nodes: s.nodes,
    edges: s.edges,
    setOpenSaveAgentDialog: s.setOpenSaveAgentDialog,
    setPendingRunAfterSave: s.setPendingRunAfterSave,
    setValidationErrorNodeIds: s.setValidationErrorNodeIds,
    clearValidationErrors: s.clearValidationErrors,
    clearAllExecutionStates: s.clearAllExecutionStates,
    isNodeFormDirty: s._isNodeFormDirty,
  }));

  const { hasRun, showOutput, outputPanelHeight, setShowOutput } =
    useWorkflowRunStoreShallow((state) => ({
      hasRun: state.hasRun,
      showOutput: state.showOutput,
      outputPanelHeight: state.outputPanelHeight,
      setShowOutput: state.setShowOutput,
    }));

  const {
    isLoadingTemplate,
    showStopConfirmDialog,
    setShowStopConfirmDialog,
    cancelLoadingTemplate,
  } = useTemplateLoadingStoreShallow((state) => ({
    isLoadingTemplate: state.isLoadingTemplate,
    showStopConfirmDialog: state.showStopConfirmDialog,
    setShowStopConfirmDialog: state.setShowStopConfirmDialog,
    cancelLoadingTemplate: state.cancelLoadingTemplate,
  }));

  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);

  const handleRunWorkflow = ({ skipDirtyCheck = false } = {}) => {
    if (!skipDirtyCheck && isNodeFormDirty) {
      setShowUnsavedDialog(true);
      return;
    }
    if (isDraft) {
      clearValidationErrors();
      clearAllExecutionStates();
      const result = validateGraphForSave(nodes, edges);
      if (!result.valid) {
        if (result.invalidNodeIds.length > 0) {
          setValidationErrorNodeIds(result.invalidNodeIds);
        }
        const message = result.hasCycle
          ? result.errors[0].message
          : result.invalidNodeIds.length === 1
            ? "Node not configured"
            : `${result.invalidNodeIds.length} nodes are not configured`;
        enqueueSnackbar(message, { variant: "error" });
        return;
      }
      setPendingRunAfterSave(true);
      setOpenSaveAgentDialog(true);
      return;
    }
    runWorkflow();
  };

  const handleStopWorkflow = () => {
    stopWorkflow();
  };

  const handleToggleOutput = () => {
    setShowOutput(!showOutput);
  };

  const handleConfirmStop = () => {
    cancelLoadingTemplate();
    setShowStopConfirmDialog(false);
  };

  const handleCancelStop = () => {
    setShowStopConfirmDialog(false);
  };

  const showRunScenarioFocus = onboardingMode === "run-scenario" && hasNodes;
  const showEvalCoverageFocus = onboardingMode === "add-eval" && hasNodes;
  const showOnboardingFocus = showRunScenarioFocus || showEvalCoverageFocus;
  const onboardingBlocker = (() => {
    if (!hasNodes) {
      return "Add one node first";
    }
    if (isLoadingTemplate) {
      return "Template loading";
    }
    if (isRunning) {
      return "Workflow running";
    }
    if (isDraft) {
      return showEvalCoverageFocus
        ? "Save this eval coverage first"
        : "Save this version first";
    }
    return null;
  })();
  const runWorkflowLabel = (() => {
    if (!hasNodes) {
      return "Add nodes first";
    }
    if (isDraft) {
      return "Save and run";
    }
    return hasRun ? "Rerun workflow" : "Run workflow";
  })();
  const runScenarioLabel =
    showRunScenarioFocus && isDraft
      ? "Save agent and run scenario"
      : runWorkflowLabel;
  const evalCoverageLabel = isDraft
    ? "Save and run eval coverage"
    : hasRun
      ? "Rerun eval coverage"
      : "Run eval coverage";
  const onboardingActionLabel = showEvalCoverageFocus
    ? evalCoverageLabel
    : runScenarioLabel;

  return (
    <>
      <Box
        sx={{
          position: "absolute",
          top: 12,
          left: `calc(${width} + 16px)`,
          right: rightOffset ? `calc(${rightOffset}px + 16px)` : 16,
          zIndex: 8,
          pointerEvents: "auto",
        }}
      >
        <AgentOnboardingFocusPanel
          currentStep={showEvalCoverageFocus ? "Eval" : "Run"}
          description={
            showEvalCoverageFocus
              ? "The eval node is ready. Save this coverage and run it once so the agent setup ends with a measurable check."
              : "The prompt is ready. Save this version, then run one scenario to review the first output."
          }
          hidden={!showOnboardingFocus}
          blocker={onboardingBlocker}
          primaryAction={{
            label: onboardingActionLabel,
            onClick: handleRunWorkflow,
            disabled: !hasNodes || isLoadingTemplate || isRunning,
          }}
          secondaryAction={
            hasRun
              ? {
                  label: showOutput ? "Hide outcome" : "Show outcome",
                  onClick: handleToggleOutput,
                  disabled: isLoadingTemplate,
                }
              : null
          }
          singleActionFocus={showOnboardingFocus}
          steps={
            showEvalCoverageFocus
              ? [
                  { label: "Agent", complete: true },
                  { label: "Scenario", complete: true },
                  { label: "Review", complete: true },
                  { label: "Coverage", complete: true },
                  { label: "Eval", complete: hasRun },
                ]
              : [
                  { label: "Create", complete: true },
                  { label: "Prompt", complete: true },
                  { label: "Run", complete: hasRun },
                  { label: "Review output", complete: false },
                ]
          }
          sx={{ mb: 0 }}
          title={
            showEvalCoverageFocus
              ? "Run the agent eval coverage"
              : "Run one test scenario"
          }
          tourAnchor={tourAnchor}
        />
      </Box>

      {/* Action Buttons */}
      <Box
        sx={{
          position: "absolute",
          bottom:
            showOutput && !isLoadingTemplate ? outputPanelHeight + 20 : 20,
          left: `calc(${width} + 40%)`,
          transform: "translateX(-50%)",
          transition: "bottom 0.2s ease-out",
          display: "flex",
          gap: 1,
        }}
      >
        {/* Stop Button - Show when loading template */}
        {isLoadingTemplate && (
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => setShowStopConfirmDialog(true)}
            startIcon={
              <SvgColor
                sx={{
                  height: 20,
                  width: 20,
                  bgcolor: "red.600",
                }}
                src="/assets/icons/ic_stop.svg"
              />
            }
            sx={{
              color: "red.600",
            }}
          >
            Stop
          </Button>
        )}

        {/* Stop Workflow Button - Show when workflow is running */}
        {isRunning && !isLoadingTemplate && (
          <Button
            variant="outlined"
            color="inherit"
            sx={{
              bgcolor: "background.paper",
            }}
            size="small"
            onClick={handleStopWorkflow}
            startIcon={<Iconify icon="mingcute:exit-door-line" width={20} />}
          >
            Exit Workflow
          </Button>
        )}

        {/* Run Button - Show when has nodes, not loading, and not running */}
        {hasNodes &&
          !isLoadingTemplate &&
          !isRunning &&
          !showOnboardingFocus && (
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              loading={isInitiating}
              onClick={handleRunWorkflow}
              sx={{
                flexShrink: 0,
              }}
              startIcon={
                hasRun ? (
                  <Iconify icon="mingcute:refresh-2-line" width={20} />
                ) : (
                  <SvgColor
                    sx={{
                      height: 20,
                      width: 20,
                    }}
                    src="/assets/icons/navbar/ic_get_started.svg"
                  />
                )
              }
            >
              {runWorkflowLabel}
            </LoadingButton>
          )}

        {/* Show/Hide Outcome Button */}
        {hasNodes && hasRun && !isLoadingTemplate && (
          <Button
            variant="outlined"
            color="primary"
            size="small"
            onClick={handleToggleOutput}
            startIcon={
              <SvgColor
                sx={{
                  height: 16,
                  width: 16,
                }}
                src={"/assets/icons/ic_outcome.svg"}
              />
            }
            sx={{
              backgroundColor: "background.paper",
              flexShrink: 0,
              "&:hover": {
                backgroundColor: "background.paper",
              },
            }}
          >
            {showOutput ? "Hide Outcome" : "Show Outcome"}
          </Button>
        )}
      </Box>

      {/* Stop Template Loading Confirmation Dialog */}
      <StopTemplateLoadingDialog
        open={showStopConfirmDialog}
        onClose={handleCancelStop}
        onConfirm={handleConfirmStop}
      />

      {/* Unsaved node form changes dialog */}
      <ConfirmDialog
        open={showUnsavedDialog}
        onClose={() => setShowUnsavedDialog(false)}
        title="Unsaved Changes"
        content="You have unsaved node changes. Running now will use the last saved configuration."
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setShowUnsavedDialog(false);
              handleRunWorkflow({ skipDirtyCheck: true });
            }}
            sx={{ paddingX: "24px" }}
          >
            Run Anyway
          </Button>
        }
      />
    </>
  );
}

BuilderActions.propTypes = {
  width: PropTypes.string.isRequired,
  hasNodes: PropTypes.bool,
  onboardingMode: PropTypes.string,
  rightOffset: PropTypes.number,
  tourAnchor: PropTypes.string,
};
