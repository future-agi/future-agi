import React, { useState, useCallback, useEffect } from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { EvalPickerContext } from "./EvalPickerContext";
import { normalizeEvalPickerEval } from "../evalPickerValue";

const EvalPickerProvider = ({
  children,
  source = "dataset",
  sourceId = "",
  sourceRowType = null,
  sourceColumns = [],
  extraColumns = [],
  existingEvals = [],
  onEvalAdded,
  onClose,
  // When editing an existing eval, skip the list and open config directly
  initialEval = null,
  // When true, picking an eval fires onEvalAdded directly from the list
  // without stepping into the column-mapping config screen. Used by
  // composite eval child pickers where the parent composite owns the mapping.
  skipConfig = false,
  // Filters that are always applied to the list and cannot be removed by
  // the user. Shape: { eval_type?: string[], output_type?: string[] }.
  lockedFilters = null,
  // Pre-resolved preview data for create-simulate mode. Shape:
  // { sim_call_type, simulation_name, simulation_type, agent_definition,
  //   agent_version, simulator_agent, prompt_template, scenario_info,
  //   scenario_columns }. Used by CreateSimulationPreviewMode to render
  // real agent/persona/scenario values alongside placeholder runtime
  // fields so users can bind eval variables before the sim runs.
  sourcePreviewData = null,
  // When set, at least one mapping field must reference this column ID.
  // Used in the optimization context to ensure the optimized column is scored.
  requiredColumnId = "",
  // When true, a successful save returns to the list step but does NOT
  // close the drawer — used by dataset adds where the picker is also a
  // multi-eval entry surface.
  keepOpenAfterSave = false,
  sourceFilters = null,
  onFiltersChange = null,
}) => {
  const [step, setStep] = useState(initialEval ? "config" : "list");
  const [selectedEval, setSelectedEvalState] = useState(
    normalizeEvalPickerEval(initialEval),
  );
  // True when the drawer was opened in edit mode (initialEval was provided).
  // In edit mode the back button closes the drawer instead of going to list.
  const isEditMode = !!initialEval;

  // When initialEval changes (e.g. user clicks Edit on a different eval),
  // jump to config with that eval pre-selected.
  useEffect(() => {
    if (initialEval) {
      setSelectedEvalState(normalizeEvalPickerEval(initialEval));
      setStep("config");
    }
  }, [initialEval]);

  const setSelectedEval = useCallback((evalData) => {
    setSelectedEvalState(normalizeEvalPickerEval(evalData));
  }, []);

  const handleReset = useCallback(() => {
    setStep("list");
    setSelectedEvalState(null);
  }, []);

  const handleClose = useCallback(() => {
    handleReset();
    onClose?.();
  }, [handleReset, onClose]);

  const filterForm = useForm({
    defaultValues: { filters: sourceFilters || [] },
  });

  return (
    <EvalPickerContext.Provider
      value={{
        step,
        setStep,
        source,
        sourceId,
        sourceRowType,
        sourceColumns,
        extraColumns,
        sourcePreviewData,
        selectedEval,
        setSelectedEval,
        existingEvals,
        onEvalAdded,
        onClose: handleClose,
        reset: handleReset,
        skipConfig,
        lockedFilters,
        isEditMode,
        requiredColumnId,
        keepOpenAfterSave,
        sourceFilters,
        onFiltersChange,
        filterForm,
      }}
    >
      {children}
    </EvalPickerContext.Provider>
  );
};

EvalPickerProvider.propTypes = {
  children: PropTypes.node.isRequired,
  source: PropTypes.string,
  sourceId: PropTypes.string,
  sourceRowType: PropTypes.string,
  sourceColumns: PropTypes.array,
  extraColumns: PropTypes.array,
  existingEvals: PropTypes.array,
  onEvalAdded: PropTypes.func,
  onClose: PropTypes.func,
  initialEval: PropTypes.object,
  skipConfig: PropTypes.bool,
  lockedFilters: PropTypes.object,
  sourcePreviewData: PropTypes.object,
  requiredColumnId: PropTypes.string,
  keepOpenAfterSave: PropTypes.bool,
  sourceFilters: PropTypes.array,
  onFiltersChange: PropTypes.func,
};

export default EvalPickerProvider;
