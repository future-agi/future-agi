import { createContext, useContext } from "react";

export const EvalPickerContext = createContext({
  // Current step in the picker flow
  step: "list", // "list" | "preview" | "config"
  setStep: (_step) => {},

  // Source context — where the eval is being added from
  source: "dataset", // "dataset" | "tracing" | "simulation" | "task" | "custom"
  sourceId: "", // ID of the current dataset/project/simulation
  sourceColumns: [], // Available columns/attributes for variable mapping
  onSourceColumnSearchChange: undefined,
  sourceColumnInventoryControls: null,
  extraColumns: [], // Virtual columns merged into the mapping dropdown alongside fetched dataset columns

  // When set, at least one mapping field must reference this column ID.
  // Used in the optimization context to ensure the optimized column is scored.
  requiredColumnId: "",

  // Selected eval
  selectedEval: null,
  setSelectedEval: (_eval) => {},

  // Multi-select the picker runs itself (multiSelect without a host
  // onToggleSelect): the evals ticked in the list, and the pass that walks
  // them ({ queue, total } — the picks still waiting for their config screen
  // after the one on screen), or null.
  ownsSelection: false,
  selectedEvals: [],
  setSelectedEvals: (_evals) => {},
  batch: null,
  setBatch: (_batch) => {},

  // Existing evals (to prevent duplicates)
  existingEvals: [],

  // Callbacks
  onEvalAdded: (_evalConfig) => {},
  onClose: () => {},

  // True when the drawer was opened for editing an existing eval.
  // Back button closes the drawer instead of returning to list.
  isEditMode: false,

  // Opt-in "Added evaluations" box: the evals already on the caller, listed
  // in their own collapsible box instead of as rows in the list;
  // `addedEvalAction` adds one button per row.
  addedEvals: null,
  addedEvalAction: null,

  // Opt-in: refuse to add an eval that has no inputs to map.
  requireInputs: false,

  // Opt-in: "Create new" offers single evals only.
  hideCompositeCreate: false,
});

export const useEvalPickerContext = () => useContext(EvalPickerContext);
