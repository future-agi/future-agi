import { createContext, useContext } from "react";

export const EvalPickerContext = createContext({
  // Current step in the picker flow
  step: "list", // "list" | "preview" | "config"
  setStep: (_step) => {},

  // Source context — where the eval is being added from
  source: "dataset", // "dataset" | "tracing" | "simulation" | "task" | "custom"
  sourceId: "", // ID of the current dataset/project/simulation
  sourceColumns: [], // Available columns/attributes for variable mapping
  extraColumns: [], // Virtual columns merged into the mapping dropdown alongside fetched dataset columns

  // When set, at least one mapping field must reference this column ID.
  // Used in the optimization context to ensure the optimized column is scored.
  requiredColumnId: "",

  // Selected eval
  selectedEval: null,
  setSelectedEval: (_eval) => {},

  // Multi-select mode — when true the list renders checkboxes instead
  // of per-row "Add" buttons and a footer action bar with "Add Selected (N)".
  multiSelect: false,

  // Selected evals (multi-select mode). Checkbox state per row.
  selectedEvals: [],
  setSelectedEvals: (_evals) => {},
  toggleSelectedEval: (_evalItem) => {},

  // Bulk config queue — when the user picks multiple evals and skipConfig
  // is off, we walk through config for each one sequentially. The first
  // eval is set as selectedEval immediately; the rest live here.
  pendingEvals: [],
  setPendingEvals: (_evals) => {},
  startBulkConfig: (_evals) => {},
  clearConfigQueue: () => {},

  // Existing evals (to prevent duplicates)
  existingEvals: [],

  // Callbacks
  onEvalAdded: (_evalConfig) => {},
  onClose: () => {},

  // True when the drawer was opened for editing an existing eval.
  // Back button closes the drawer instead of returning to list.
  isEditMode: false,
});

export const useEvalPickerContext = () => useContext(EvalPickerContext);
