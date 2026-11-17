import { createContext, useContext } from "react";

/**
 * Data every step of the experiment drawer reads: the dataset, the chosen
 * version's columns (raw column_config with `role`), the sample row the
 * mapping previews use, the next ordinal and the mode.
 */
export const ExperimentFormContext = createContext({
  datasetId: null,
  datasetName: "",
  isEditing: false,
  nextOrdinal: null,
  columns: [],
  mappingColumns: [],
  jsonSchemas: {},
  rows: [],
  sampleRow: null,
  sampleRowLabel: "",
  setSampleRowId: () => {},
  exampleCount: null,
  versions: [],
  splits: [],
});

export const useExperimentForm = () => useContext(ExperimentFormContext);

/** `Row 12` for a table row (row order is 0-based). */
export const rowLabelOf = (row) =>
  row && Number.isFinite(row.order) ? `Row ${row.order + 1}` : "Row";
