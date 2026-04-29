/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useState } from "react";
import { ErrorBoundary } from "react-error-boundary";
import Iconify from "src/components/iconify";
import EvalPickerProvider from "./context/EvalPickerProvider";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import EvalPickerList from "./EvalPickerList";
import EvalPickerConfigFull from "./EvalPickerConfigFull";
import EvalPickerCreateNew from "./EvalPickerCreateNew";
import { normalizeEvalPickerEval } from "./evalPickerValue";

const STEP_TITLES = {
  list: "Select Evaluation",
  config: "Configure Evaluation",
  create: "Create New Evaluation",
};

const EvalPickerContent = ({ onStepChange }) => {
  const theme = useTheme();
  const {
    step,
    setStep,
    selectedEval,
    setSelectedEval,
    onEvalAdded,
    onClose,
    skipConfig,
    isEditMode,
    keepOpenAfterSave,
  } = useEvalPickerContext();

  const [isSaving, setIsSaving] = useState(false);

  // Notify parent when step changes (for drawer width)
  useEffect(() => {
    onStepChange?.(step);
  }, [step, onStepChange]);

  // From the list (expand → "Add Evaluation"), go directly to config.
  // When skipConfig is set, fire onEvalAdded immediately with the raw
  // eval metadata — used by composite eval child pickers where there's
  // no column mapping to resolve.
  const handleSelectEval = useCallback(
    async (evalData) => {
      if (skipConfig) {
        setIsSaving(true);
        try {
          await onEvalAdded?.(normalizeEvalPickerEval(evalData));
          onClose?.();
        } catch {
          // Parent handles error display
        } finally {
          setIsSaving(false);
        }
        return;
      }
      setSelectedEval(evalData);
      setStep("config");
    },
    [skipConfig, onEvalAdded, onClose, setSelectedEval, setStep],
  );

  // In edit mode, back closes the drawer (returns to the SavedEvalsList).
  // In create mode, back returns to the list step.
  const handleBackToList = useCallback(() => {
    if (isEditMode) {
      onClose?.();
      return;
    }
    setSelectedEval(null);
    setStep("list");
  }, [isEditMode, onClose, setSelectedEval, setStep]);

  const handleSaveEval = useCallback(
    async (evalConfig) => {
      setIsSaving(true);
      try {
        await onEvalAdded?.(evalConfig);
        if (isEditMode) {
          // Edit mode: just close, no list to return to
          onClose?.();
        } else {
          setSelectedEval(null);
          setStep("list");
          // When the host wants the picker to stay open (e.g. dataset
          // adds, where the user often queues several evals back-to-back),
          // skip the close so the user lands back on the list step
          // without re-opening the drawer.
          if (!keepOpenAfterSave) onClose?.();
        }
      } catch {
        // Keep on config screen if save fails
      } finally {
        setIsSaving(false);
      }
    },
    [
      isEditMode,
      onEvalAdded,
      onClose,
      setSelectedEval,
      setStep,
      keepOpenAfterSave,
    ],
  );

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        p: 2.5,
        backgroundColor: theme.palette.background.paper,
      }}
    >
      {/* Header — only show on list step (config/create have their own headers) */}
      {step === "list" && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 2,
          }}
        >
          <Typography variant="h6" fontWeight={600} sx={{ fontSize: "16px" }}>
            {STEP_TITLES[step]}
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="mingcute:add-line" width={16} />}
              onClick={() => setStep("create")}
              sx={{ textTransform: "none", fontSize: "12px" }}
            >
              Create New Eval
            </Button>
            <IconButton onClick={onClose} size="small" sx={{ p: 0.5 }}>
              <Iconify icon="mingcute:close-line" width={20} />
            </IconButton>
          </Box>
        </Box>
      )}

      {/* Step content */}
      <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <ErrorBoundary
          fallbackRender={({ error, resetErrorBoundary }) => (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                gap: 2,
                py: 8,
              }}
            >
              <Iconify
                icon="mdi:alert-circle-outline"
                width={40}
                sx={{ color: "error.main" }}
              />
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ maxWidth: 400, textAlign: "center" }}
              >
                Something went wrong loading this evaluation.
              </Typography>
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{
                  maxWidth: 400,
                  textAlign: "center",
                  fontFamily: "monospace",
                }}
              >
                {error?.message}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  handleBackToList();
                  resetErrorBoundary();
                }}
                sx={{ textTransform: "none" }}
              >
                Back to list
              </Button>
            </Box>
          )}
          resetKeys={[step, selectedEval?.id]}
        >
          {step === "list" && (
            <EvalPickerList onSelectEval={handleSelectEval} />
          )}
          {step === "config" && selectedEval && (
            <EvalPickerConfigFull
              key={
                selectedEval?.templateId ||
                selectedEval?.template_id ||
                selectedEval?.id
              }
              evalData={selectedEval}
              onBack={handleBackToList}
              onSave={handleSaveEval}
              isSaving={isSaving}
            />
          )}
          {step === "create" && (
            <EvalPickerCreateNew
              onBack={handleBackToList}
              onSave={handleSaveEval}
            />
          )}
        </ErrorBoundary>
      </Box>
    </Box>
  );
};

/**
 * EvalPickerDrawer — Unified eval picker used across the platform.
 *
 * Flow: List → Preview → Config → Done
 *
 * @param {boolean} open - Whether the drawer is open
 * @param {function} onClose - Called when the drawer should close
 * @param {string} source - Source context: "dataset" | "tracing" | "simulation" | "task" | "custom"
 * @param {Array} sourceColumns - Available columns for variable auto-mapping
 * @param {function} onEvalAdded - Called with the configured eval object when user saves
 * @param {Array} existingEvals - Already-added evals (to disable re-adding)
 * @param {string} drawerType - MUI Drawer variant: "temporary" (default) or "persistent"
 * @param {number|string} width - Drawer width (default: 700px)
 */
const EvalPickerDrawer = ({
  open,
  onClose,
  source = "dataset",
  sourceId = "",
  sourceRowType = null,
  sourceColumns = [],
  extraColumns = [],
  onEvalAdded,
  existingEvals = [],
  drawerType = "temporary",
  width = 900,
  // When editing an existing eval, pass its template info here to skip the
  // list step and open directly at the config step.
  initialEval = null,
  // Skip the column-mapping config step. The drawer fires onEvalAdded with
  // raw eval metadata the moment the user clicks "Add". Used by composite
  // eval child pickers.
  skipConfig = false,
  // Filters that are always applied to the list. Shape matches backend:
  // { eval_type?: string[], output_type?: string[] }.
  lockedFilters = null,
  // For create-simulate: pre-resolved preview snapshot built from the
  // form state. See CreateSimulationPreviewMode + EvalPickerProvider.
  sourcePreviewData = null,
  // When set, at least one mapping field must reference this column ID.
  // Used in the optimization context to ensure the optimized column is scored.
  requiredColumnId = "",
  // When true, the drawer stays open after a successful save so the user
  // can queue more evals back-to-back. Used by dataset adds where the
  // picker doubles as a multi-eval entry surface.
  keepOpenAfterSave = false,
}) => {
  const [currentStep, setCurrentStep] = useState("list");

  return (
    <Drawer
      anchor="right"
      open={open}
      variant={drawerType}
      onClose={onClose}
      PaperProps={{
        sx: (theme) => ({
          width:
            currentStep === "config" || currentStep === "create"
              ? "90vw"
              : typeof width === "number"
                ? `${width}px`
                : width,
          maxWidth: "95vw",
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          boxShadow: theme.customShadows?.drawer || theme.shadows[16],
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
        }),
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
          },
        },
      }}
    >
      <EvalPickerProvider
        key={initialEval?.userEvalId || initialEval?.id || "new"}
        source={source}
        sourceId={sourceId}
        sourceRowType={sourceRowType}
        sourceColumns={sourceColumns}
        extraColumns={extraColumns}
        sourcePreviewData={sourcePreviewData}
        existingEvals={existingEvals}
        onEvalAdded={onEvalAdded}
        onClose={onClose}
        initialEval={initialEval}
        skipConfig={skipConfig}
        lockedFilters={lockedFilters}
        requiredColumnId={requiredColumnId}
        keepOpenAfterSave={keepOpenAfterSave}
      >
        <EvalPickerContent onStepChange={setCurrentStep} />
      </EvalPickerProvider>
    </Drawer>
  );
};

EvalPickerDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  source: PropTypes.string,
  sourceId: PropTypes.string,
  sourceRowType: PropTypes.string,
  sourceColumns: PropTypes.array,
  extraColumns: PropTypes.array,
  onEvalAdded: PropTypes.func,
  existingEvals: PropTypes.array,
  drawerType: PropTypes.string,
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  initialEval: PropTypes.object,
  skipConfig: PropTypes.bool,
  lockedFilters: PropTypes.object,
  sourcePreviewData: PropTypes.object,
  requiredColumnId: PropTypes.string,
  keepOpenAfterSave: PropTypes.bool,
};

export default EvalPickerDrawer;
